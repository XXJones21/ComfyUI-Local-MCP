# ComfyLocalMCP

**A local-first, device-aware ComfyUI client + MCP server for *building* with local image/video generation.** Use it as a plain Python library inside your own pipelines, or as an MCP server + Claude Code plugin so an agent can build, optimize, and run ComfyUI workflows that actually fit your GPU.

> 🙏 **Shout-out to the ComfyUI + MCP community.** ComfyUI is the de-facto engine for local GenAI, and there's great prior art for driving it from agents — if you want a feature-rich, interactive "talk to ComfyUI from Claude" experience, check out [**artokun/comfyui-mcp**](https://github.com/artokun/comfyui-mcp). ComfyLocalMCP is a complementary, narrower take: **Python-native + embeddable-as-a-library + VRAM/hardware-aware**, aimed at people wiring ComfyUI into their own products.

## Why this one?

- **Library *and* MCP.** Import `comfy_local_mcp` directly in your Python pipeline (deterministic, no agent in the loop), *or* run the MCP server for agent-driven use. Same code, both shapes.
- **Device-aware.** `device_report` + `recommend_workflow` read your actual free VRAM (via ComfyUI's `/system_stats`) and pick a workflow + settings that fit — and only ever suggest workflows whose models/nodes are actually installed. No more 10-minute thrash because Flux + a resident LLM didn't fit on a 16 GB card.
- **Workflows as parameterized templates.** Named ComfyUI graphs with logical override points (`prompt`, `seed`, `width`, `height`, `steps`).
- **Two transports.** `direct` (vanilla ComfyUI on `:8188`) or `rust` (any supervised ComfyUI fronting `/comfy/*` with restart/health/watchdog).

## Features

Four surfaces over one local ComfyUI: a Python **library**, an **MCP server**, a **Claude Code plugin**, and a **workflow template** system.

### Python library (`comfy_local_mcp`)
Embed it directly in your own pipelines — no agent required.

- `ComfyClient` — async core: `submit`, `stream` (normalized progress events), `wait_for_result`, `fetch_result`, `download`, plus `object_info` and `system_stats` for discovery/diagnostics.
- `ComfyImageGenerator` / `ComfyVideoGenerator` — sync, drop-in generators returning a stable diffusers-like dict (`filepath`, `filename`, `image_data`, `metadata{asset_url, prompt_id, seed, …}`).
- `load_workflow` / `list_workflows` — load and patch a named template.
- `comfy_local_mcp.progress.stream` — convenience progress iterator.

### MCP server — 9 tools

| Tool | Purpose |
|---|---|
| `device_report()` | GPU name + total/free VRAM + ComfyUI/torch/python versions (via `/system_stats`; falls back to `nvidia-smi`). |
| `recommend_workflow(goal, vram_free_gb?, resident_model_gb?)` | **The differentiator.** Picks a workflow + overrides that *fit* the GPU, accounts for a resident model (e.g. an LLM), downsizes when tight, and only suggests workflows whose models/nodes are installed. Returns `{workflow, overrides, fits, budget_gb, rationale, warnings, candidates}`. |
| `list_workflows()` | Available workflow names. |
| `list_models(node_class)` | What a loader node accepts (catches "model not installed" before you submit). |
| `generate_image(prompt, workflow, width, height, seed?, steps?)` | High-level text→image. |
| `generate_video(prompt, image_input, workflow)` | High-level image→video. |
| `submit_workflow(name, overrides)` / `get_result(prompt_id)` | Low-level build/execute loop for tuned graphs. |
| `health()` | Transport + ComfyUI reachability. |

Tools return asset URLs/paths, not raw image bytes (keeps agent context small).

### Claude Code plugin
- **`comfy-local` subagent** — a **build → optimize → execute** loop: `device_report` → `recommend_workflow` → confirm models → run → report, with honest "this won't fit, here's the lighter option" behavior instead of silent VRAM thrash.
- **`comfy-local-setup` skill** — cross-platform install walkthrough (detect GPU, install/locate ComfyUI, register the comfy-local + Hugging Face MCPs, bootstrap a fitting model, smoke-test) + references for per-OS install and model-download recipes.
- **Slash commands** — `/comfy-gen` (generate, GPU-optimized) and `/comfy-setup`.
- **`.mcp.json`** auto-wires the server over stdio, so installing the plugin makes the tools appear.

### Workflow templates
ComfyUI API-format graphs wrapped as `{_meta, _overrides, graph}`, patched by logical name (`prompt`, `seed`, `width`, `height`, `steps`):

| Workflow | Model / use |
|---|---|
| `scene_image_flux` | Flux.1-dev Q4 GGUF, portrait 9:16 — the proven hero path |
| `scene_image_sdxl_turbo` | SDXL-Turbo, 1-step draft/fast |
| `scene_image_sdxl_lora` | SDXL + LoRA, styled |
| `scene_video_ltx` | LTX-Video, image→video |
| `scene_video_wan22` | Wan2.2, cinematic video |
| `skybox_sdxl_turbo` | 360° skybox |

### Compatibility shim
`shim/` ships an `engram_comfy` package that re-exports the API, so code importing the pre-rename name keeps working.

### Not included (v1, by design)
Model downloading (compose with the [Hugging Face MCP](https://huggingface.co/mcp) + `hf` CLI), free-form graph authoring, an asset/history database, and cloud GPU.

## Install

```bash
pip install -e ".[mcp]"          # library + MCP server (FastMCP)
```

Requires a running ComfyUI (`http://127.0.0.1:8188`). New to ComfyUI? Run the bundled **setup** skill (Claude Code) or see `skills/setup/references/`.

## Library quickstart

```python
from comfy_local_mcp import ComfyImageGenerator

gen = ComfyImageGenerator(default_workflow="scene_image_flux")
result = gen.generate_image(
    prompt="first person POV, misty forest at dusk, glowing mushrooms",
    width=768, height=1344,
)
print(result["filepath"], result["metadata"]["asset_url"])
```

## MCP server

```bash
comfy-local-mcp --transport http      # shared server on http://127.0.0.1:9400/mcp
# or stdio (for a Claude Code plugin): comfy-local-mcp --transport stdio
```

Tools: `device_report`, `recommend_workflow`, `list_workflows`, `list_models`, `generate_image`, `generate_video`, `submit_workflow`, `get_result`, `health`.

Register with Claude Code:
```bash
claude mcp add --transport http comfy-local http://127.0.0.1:9400/mcp
```

## Claude Code plugin

This repo is also a Claude Code plugin: it ships the **`comfy-local`** subagent (build → optimize → execute) and the **setup** skill, and auto-wires the MCP server via `.mcp.json` (stdio). Pair it with the [Hugging Face MCP](https://huggingface.co/mcp) for model discovery (downloads use the `hf` CLI — see the setup skill).

## Environment

| Var | Default | Purpose |
|---|---|---|
| `COMFY_TRANSPORT` | `rust` | `direct` (ComfyUI `:8188`) or `rust` (supervised `/comfy/*`) |
| `COMFY_BASE_URL` | per transport | ComfyUI/supervisor base URL |
| `COMFY_TIMEOUT_S` | `300` | Per-job timeout |
| `COMFY_ASSETS_DIR` | `generated_assets` | Where downloaded outputs land |
| `COMFY_MCP_TRANSPORT` | `http` | MCP transport (`http` or `stdio`) |
| `COMFY_MCP_PORT` | `9400` | HTTP MCP port |

## Status

Verified on **Windows + NVIDIA (RTX 4080)**. macOS/Linux and non-NVIDIA paths are best-effort — issues and PRs welcome. v1 deliberately does **not** rebuild model downloading (use the HF MCP + `hf` CLI) or free-form graph editing.

## License

MIT © Joshua Jones
