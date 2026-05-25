"""ComfyLocalMCP — a local-first, device-aware MCP server over ComfyUI.

Exposes the comfy_local_mcp client as MCP tools so any LLM-driven agent
(Claude Code, your own products) can build, optimize, and run ComfyUI
workflows without re-implementing submission/polling — and without guessing
whether a model fits the user's GPU.

Transports:
  - http  (default): one shared long-running server (good for multi-product reuse)
  - stdio          : spawned per-client by a Claude Code plugin's .mcp.json

Respects the client env vars — COMFY_TRANSPORT (direct|rust), COMFY_BASE_URL,
COMFY_ASSETS_DIR — so it fronts a dev ComfyUI on :8188 or a supervised one.

Run:  comfy-local-mcp [--transport http|stdio]
  or  python -m comfy_local_mcp.server --transport stdio
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from typing import Any

from fastmcp import FastMCP

from comfy_local_mcp import (
    ComfyClient,
    ComfyError,
    ComfyImageGenerator,
    ComfyVideoGenerator,
    list_workflows as _list_workflows,
)
from comfy_local_mcp import config as cfg
from comfy_local_mcp.client import run_sync

mcp = FastMCP(
    name="comfy-local",
    instructions=(
        "Local, device-aware ComfyUI image/video generation. First run / setup: "
        "get_config; if its models map is empty, run suggest_config then "
        "save_config(models=...) so workflows use the user's installed model files. "
        "Generation flow: device_report -> recommend_workflow(goal) -> list_models "
        "(confirm installed) -> generate_image/generate_video (or submit_workflow/"
        "get_result for a tuned graph). recommend_workflow encodes VRAM fit for this "
        "GPU and only suggests workflows whose models/nodes are installed. Tools "
        "return asset URLs/paths, not raw bytes."
    ),
)

# Approximate VRAM footprints (GiB) and install requirements per workflow.
# Heuristics, not guarantees — recommend_workflow returns warnings, never promises.
_WORKFLOW_PROFILES: dict[str, dict[str, Any]] = {
    "scene_image_flux": {
        "goal": "image",
        "quality": "high",
        "footprint_fp16_gb": 16.2,  # flux Q4 unet 6.4 + t5xxl fp16 9.2 + clip_l 0.25 + vae 0.3
        "footprint_fp8_gb": 11.9,   # with t5xxl_fp8_e4m3fn (4.9)
        "require_node": "UnetLoaderGGUF",
        "model_check": ("UnetLoaderGGUF", "unet_name", "flux"),
    },
    "scene_image_sdxl_turbo": {
        "goal": "image",
        "quality": "draft",
        "footprint_fp16_gb": 6.5,
        "require_node": "CheckpointLoaderSimple",
        "model_check": ("CheckpointLoaderSimple", "ckpt_name", "turbo"),
    },
    "scene_image_sdxl_lora": {
        "goal": "image",
        "quality": "styled",
        "footprint_fp16_gb": 7.0,
        "require_node": "CheckpointLoaderSimple",
        "model_check": ("CheckpointLoaderSimple", "ckpt_name", "xl"),
    },
    "scene_video_ltx2": {
        "goal": "video",
        "quality": "high",
        # ltx-2.3 Q3 unet ~9.9 + gemma Q4 gguf ~7.3 (offloaded) + connectors 2.3 + vaes 1.8;
        # sequential offload keeps resident VRAM well under 16 GB (verified ~273s on a 4080).
        "footprint_fp16_gb": 13.5,
        "require_node": "DualCLIPLoaderGGUF",
        "model_check": ("UnetLoaderGGUF", "unet_name", "ltx-2.3"),
    },
    "scene_video_ltx": {
        "goal": "video",
        "quality": "standard",
        "footprint_fp16_gb": 9.0,
        "require_node": "LTXVCheckpointLoader",
        "model_check": ("LTXVCheckpointLoader", "ckpt_name", "ltx-video"),
    },
    "scene_video_wan22": {
        "goal": "video",
        "quality": "cinematic",
        "footprint_fp16_gb": 13.0,
        "require_node": "UnetLoaderGGUF",
        "model_check": ("UnetLoaderGGUF", "unet_name", "wan"),
    },
}

_HEADROOM_GB = 1.0  # leave a little VRAM for activations/overhead


def _strip_bytes(result: dict[str, Any] | None) -> dict[str, Any]:
    """Drop the base64 payload from a generator result to keep agent context small."""
    if not result:
        return {"status": "failed", "detail": "generator returned no result"}
    out = {k: v for k, v in result.items() if k != "image_data"}
    out["status"] = "ok"
    out["has_image_data"] = bool(result.get("image_data"))
    return out


def _nvidia_smi_report() -> dict[str, Any] | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    name, total, free = (x.strip() for x in out.stdout.strip().splitlines()[0].split(","))
    return {
        "source": "nvidia-smi",
        "gpu": name,
        "vram_total_gb": round(int(total) / 1024, 2),
        "vram_free_gb": round(int(free) / 1024, 2),
    }


def _installed_models(client: ComfyClient, node_class: str, field: str) -> list[str]:
    try:
        info = run_sync(client.object_info(node_class))
    except ComfyError:
        return []
    node = info.get(node_class) or {}
    if not node:
        return []
    spec = ((node.get("input") or {}).get("required") or {}).get(field)
    if isinstance(spec, list) and spec and isinstance(spec[0], list):
        return spec[0]
    return []


def _node_installed(client: ComfyClient, node_class: str) -> bool:
    try:
        info = run_sync(client.object_info(node_class))
    except ComfyError:
        return False
    return bool(info.get(node_class))


@mcp.tool(annotations={"readOnlyHint": True})
def list_workflows() -> list[str]:
    """List the available ComfyUI workflow names (image and video)."""
    return _list_workflows()


@mcp.tool(annotations={"readOnlyHint": True})
def list_models(node_class: str = "CheckpointLoaderSimple") -> dict[str, Any]:
    """List the models/options a ComfyUI loader node accepts.

    Use this to confirm a checkpoint/unet/vae is installed before generating.
    Examples: CheckpointLoaderSimple (ckpt_name), UnetLoaderGGUF (unet_name),
    VAELoader (vae_name), DualCLIPLoader (clip_name1/2). Returns each combo-typed
    required input with its list of valid values.
    """
    info = run_sync(ComfyClient().object_info(node_class))
    node = info.get(node_class) or {}
    required = (node.get("input") or {}).get("required") or {}
    options: dict[str, Any] = {}
    for field, spec in required.items():
        if isinstance(spec, list) and spec and isinstance(spec[0], list):
            options[field] = spec[0]
    return {"node_class": node_class, "options": options}


@mcp.tool(annotations={"readOnlyHint": True})
def device_report() -> dict[str, Any]:
    """Report the GPU and VRAM available for generation.

    Reads ComfyUI's /system_stats (no extra deps); falls back to nvidia-smi if
    ComfyUI isn't running (useful before install). VRAM is in GiB.
    """
    try:
        stats = run_sync(ComfyClient().system_stats())
        dev = (stats.get("devices") or [{}])[0]
        sysinfo = stats.get("system") or {}
        return {
            "source": "comfyui",
            "gpu": dev.get("name"),
            "type": dev.get("type"),
            "vram_total_gb": round(dev.get("vram_total", 0) / 1024**3, 2),
            "vram_free_gb": round(dev.get("vram_free", 0) / 1024**3, 2),
            "comfyui_version": sysinfo.get("comfyui_version"),
            "torch": sysinfo.get("pytorch_version"),
            "python": sysinfo.get("python_version"),
        }
    except Exception:  # noqa: BLE001 - fall back when ComfyUI is unreachable
        nv = _nvidia_smi_report()
        if nv:
            return nv
        return {"source": "none", "error": "ComfyUI /system_stats unreachable and nvidia-smi unavailable"}


@mcp.tool(annotations={"readOnlyHint": True})
def recommend_workflow(
    goal: str = "image",
    vram_free_gb: float | None = None,
    resident_model_gb: float = 0.0,
) -> dict[str, Any]:
    """Recommend a workflow + settings that fit this GPU for a goal.

    ``goal``: "image" (or "video"); keywords like "draft"/"fast", "cinematic"
    nudge the pick. ``vram_free_gb`` auto-fills from device_report if omitted.
    ``resident_model_gb`` is VRAM already held by another model you're keeping
    loaded (e.g. an in-process LLM) — it's subtracted from the budget.

    Only recommends workflows whose required node + a matching model are actually
    installed. Returns {workflow, overrides, fits, rationale, warnings, candidates}.
    """
    client = ComfyClient()
    if vram_free_gb is None:
        rep = device_report()
        vram_free_gb = rep.get("vram_free_gb") or rep.get("vram_total_gb") or 0.0
    budget = max(0.0, float(vram_free_gb) - float(resident_model_gb) - _HEADROOM_GB)

    g = goal.lower()
    want_video = "video" in g or "clip" in g or "motion" in g
    prefer_draft = any(k in g for k in ("draft", "fast", "quick"))
    prefer_cinematic = "cinematic" in g or "hero" in g

    candidates: list[dict[str, Any]] = []
    for name, p in _WORKFLOW_PROFILES.items():
        if (p["goal"] == "video") != want_video:
            continue
        node_ok = _node_installed(client, p["require_node"])
        mnode, mfield, msub = p["model_check"]
        models = _installed_models(client, mnode, mfield)
        model_ok = any(msub.lower() in m.lower() for m in models)
        fp16 = p.get("footprint_fp16_gb", 99)
        fp8 = p.get("footprint_fp8_gb")
        fits_fp16 = budget >= fp16
        fits_fp8 = fp8 is not None and budget >= fp8
        candidates.append({
            "workflow": name, "quality": p["quality"], "installed": node_ok and model_ok,
            "node_ok": node_ok, "model_ok": model_ok, "matched_models": [m for m in models if msub.lower() in m.lower()],
            "footprint_fp16_gb": fp16, "footprint_fp8_gb": fp8,
            "fits_fp16": fits_fp16, "fits_fp8": fits_fp8,
        })

    installed = [c for c in candidates if c["installed"]]
    warnings: list[str] = []
    if not installed:
        return {
            "workflow": None, "fits": False, "budget_gb": round(budget, 2),
            "rationale": f"No installed {'video' if want_video else 'image'} workflow found. "
                         "Install the required model/custom node (see the setup skill / HF MCP).",
            "warnings": [f"{c['workflow']}: node_ok={c['node_ok']} model_ok={c['model_ok']}" for c in candidates],
            "candidates": candidates,
        }

    # Rank: honor draft/cinematic preference, then prefer ones that fit, then quality.
    def rank(c: dict[str, Any]) -> tuple:
        pref = 0
        if prefer_draft and c["quality"] == "draft":
            pref = -2
        if prefer_cinematic and c["quality"] == "cinematic":
            pref = -2
        return (pref, 0 if (c["fits_fp16"] or c["fits_fp8"]) else 1, {"high": 0, "cinematic": 0, "styled": 1, "standard": 1, "draft": 2}.get(c["quality"], 3))

    best = sorted(installed, key=rank)[0]
    overrides: dict[str, Any] = {}
    fits = best["fits_fp16"]
    if not best["fits_fp16"] and best["fits_fp8"]:
        fits = True
        warnings.append(
            f"{best['workflow']} fits only with the fp8 text encoder: swap t5xxl_fp16 -> "
            f"t5xxl_fp8_e4m3fn (~4.9 GB) in ComfyUI/models/text_encoders, or expect slow T5 reloads."
        )
    if not fits:
        # try shrinking resolution for image workflows
        if best["workflow"].startswith("scene_image"):
            overrides.update({"width": 512, "height": 896})
            warnings.append(
                f"Budget {budget:.1f} GB is below the ~{best['footprint_fp16_gb']} GB footprint; "
                "dropped to 512x896 and you may still need fp8/quantized text encoders or to free VRAM "
                f"(resident_model_gb={resident_model_gb})."
            )
        else:
            warnings.append(
                f"Budget {budget:.1f} GB is below the ~{best['footprint_fp16_gb']} GB footprint; "
                "free VRAM or pick a lighter workflow."
            )

    rationale = (
        f"Picked '{best['workflow']}' ({best['quality']}) for goal '{goal}'. "
        f"Budget {budget:.1f} GB (free {vram_free_gb:.1f} - resident {resident_model_gb} - {_HEADROOM_GB} headroom) "
        f"vs ~{best['footprint_fp16_gb']} GB fp16."
    )
    return {
        "workflow": best["workflow"], "overrides": overrides, "fits": bool(fits),
        "budget_gb": round(budget, 2), "rationale": rationale, "warnings": warnings,
        "candidates": candidates,
    }


@mcp.tool
def generate_image(
    prompt: str,
    workflow: str = "scene_image_flux",
    width: int = 768,
    height: int = 1344,
    seed: int | None = None,
    steps: int | None = None,
) -> dict[str, Any]:
    """Generate an image from a text prompt via ComfyUI.

    Defaults to the proven Flux GGUF workflow at portrait 9:16. Returns the
    asset URL, local file path, filename, and metadata (no raw bytes).
    """
    gen = ComfyImageGenerator(default_workflow=workflow)
    kwargs: dict[str, Any] = {"workflow": workflow, "width": width, "height": height}
    if seed is not None:
        kwargs["seed"] = seed
    if steps is not None:
        kwargs["steps"] = steps
    return _strip_bytes(gen.generate_image(prompt, **kwargs))


@mcp.tool
def generate_video(
    prompt: str,
    image_input: str,
    workflow: str = "scene_video_ltx",
) -> dict[str, Any]:
    """Generate a video conditioned on a starting image (image-to-video).

    ``image_input`` is the URL or local path of the source image (e.g. from a
    prior generate_image call). Use workflow 'scene_video_wan22' for the
    cinematic/hero-shot path.
    """
    gen = ComfyVideoGenerator(default_workflow=workflow)
    return _strip_bytes(gen.generate_video(prompt, image_input=image_input, workflow=workflow))


@mcp.tool
def submit_workflow(name: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Submit a named workflow with parameter overrides; returns its prompt_id.

    Lower-level than generate_image — pair with get_result to poll. Overrides use
    the workflow's logical names (e.g. prompt, seed, width, height, steps).
    """
    prompt_id = run_sync(ComfyClient().submit(name, overrides or {}))
    return {"prompt_id": prompt_id, "workflow": name}


@mcp.tool
def get_result(prompt_id: str) -> dict[str, Any]:
    """Wait for a submitted job to finish and return its primary asset URL/metadata."""
    client = ComfyClient()

    async def _run() -> dict[str, Any]:
        job = await client.wait_for_result(prompt_id)
        return {
            "status": "ok", "prompt_id": prompt_id, "asset_url": job.asset_url,
            "filename": job.filename,
        }

    try:
        return run_sync(_run())
    except ComfyError as err:
        return {"status": "failed", "prompt_id": prompt_id, "detail": str(err)}


@mcp.tool(annotations={"readOnlyHint": True})
def health() -> dict[str, Any]:
    """Report ComfyUI reachability and the active transport/base URL."""
    client = ComfyClient()
    info: dict[str, Any] = {"transport": client.transport, "base_url": client.base_url}
    try:
        run_sync(client.object_info("CheckpointLoaderSimple"))
        info["reachable"] = True
    except ComfyError as err:
        info["reachable"] = False
        info["detail"] = str(err)
    return info


# How to discover each model role from installed ComfyUI models.
# (node_class, field, [match substrings], [prefer substrings])
_ROLE_DISCOVERY: dict[str, tuple] = {
    "flux_unet": ("UnetLoaderGGUF", "unet_name", ["flux"], ["q4", "q5"]),
    "t5": ("DualCLIPLoader", "clip_name2", ["t5"], ["fp8"]),
    "clip_l": ("DualCLIPLoader", "clip_name1", ["clip_l", "clip-l"], []),
    "flux_vae": ("VAELoader", "vae_name", ["flux", "ae"], ["flux"]),
    "sdxl_turbo_ckpt": ("CheckpointLoaderSimple", "ckpt_name", ["turbo"], []),
    "sdxl_base_ckpt": ("CheckpointLoaderSimple", "ckpt_name", ["xl_base", "sd_xl_base", "xl-base"], []),
    "ltx_ckpt": ("LTXVCheckpointLoader", "ckpt_name", ["ltx"], []),
    "wan_vae": ("WanVideoVAELoader", "model_name", ["wan"], []),
    "wan_t5": ("WanVideoT5TextEncoderLoader", "model_name", ["t5", "umt5"], []),
}


def _best_match(options: list[str], match: list[str], prefer: list[str]) -> str | None:
    cands = [o for o in options if any(m.lower() in o.lower() for m in match)]
    if not cands:
        return None
    preferred = [o for o in cands if any(p.lower() in o.lower() for p in prefer)]
    return (preferred or cands)[0]


@mcp.tool(annotations={"readOnlyHint": True})
def get_config() -> dict[str, Any]:
    """Return the current ComfyLocalMCP user config and its file path.

    The config holds transport/base_url, assets_dir, and a models map (logical
    role -> the installed filename) that workflows use. Empty config is normal
    on a fresh install — run suggest_config + save_config to populate it.
    """
    return {"path": str(cfg.config_path()), "config": cfg.load_config(), "model_roles": list(cfg.MODEL_ROLES)}


@mcp.tool(annotations={"readOnlyHint": True})
def suggest_config() -> dict[str, Any]:
    """Inspect installed ComfyUI models and propose a models map for the config.

    Read-only: does NOT write anything. Review the result, then persist it with
    save_config(models=<proposed>). Roles with no installed match are omitted
    (and listed under 'missing') so you know what still needs downloading.
    """
    client = ComfyClient()
    proposed: dict[str, str] = {}
    missing: list[str] = []
    for role, (node_class, field, match, prefer) in _ROLE_DISCOVERY.items():
        options = _installed_models(client, node_class, field)
        pick = _best_match(options, match, prefer) if options else None
        if pick:
            proposed[role] = pick
        else:
            missing.append(role)
    return {
        "proposed_models": proposed,
        "missing": missing,
        "note": "Review, then call save_config(models=proposed_models). Missing roles need their models/custom nodes installed first.",
    }


@mcp.tool
def save_config(
    models: dict[str, str] | None = None,
    transport: str | None = None,
    base_url: str | None = None,
    assets_dir: str | None = None,
) -> dict[str, Any]:
    """Persist user config (merged into ~/.comfy-local-mcp/config.json).

    Pass any subset. ``models`` is merged role-by-role (won't drop existing
    roles). Typically called once during setup after suggest_config.
    """
    updates: dict[str, Any] = {}
    if models is not None:
        updates["models"] = models
    if transport is not None:
        updates["transport"] = transport
    if base_url is not None:
        updates["base_url"] = base_url
    if assets_dir is not None:
        updates["assets_dir"] = assets_dir
    if not updates:
        return {"saved": False, "config": cfg.load_config(), "note": "nothing to update"}
    return {"saved": True, "config": cfg.save_config(updates), "path": str(cfg.config_path())}


def main() -> None:
    parser = argparse.ArgumentParser(description="ComfyLocalMCP server")
    parser.add_argument(
        "--transport",
        default=os.environ.get("COMFY_MCP_TRANSPORT", "http"),
        choices=["http", "stdio"],
        help="MCP transport (default http; plugins use stdio).",
    )
    args, _ = parser.parse_known_args()
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        host = os.environ.get("COMFY_MCP_HOST", "127.0.0.1")
        port = int(os.environ.get("COMFY_MCP_PORT", "9400"))
        mcp.run(transport="http", host=host, port=port)


if __name__ == "__main__":
    main()
