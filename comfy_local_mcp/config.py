"""Per-user configuration for ComfyLocalMCP.

Instead of hardcoding one machine's ComfyUI URL, model filenames, and output
directory, those live in a small JSON config the user (or the setup skill /
subagent) writes once. Everything else reads it:

- `ComfyClient` falls back to `transport`/`base_url` here.
- `load_workflow` injects `models` (logical role -> the user's actual filename)
  for any role a workflow exposes, so workflows adapt to whatever is installed.
- Generators write outputs under `assets_dir`.

Location: ``~/.comfy-local-mcp/config.json`` (override with the
``COMFY_LOCAL_MCP_CONFIG`` env var). Missing/partial configs are fine — every
consumer has a sensible fallback, so the package works with no config at all.

Schema (all keys optional)::

    {
      "transport": "direct",
      "base_url": "http://127.0.0.1:8188",
      "assets_dir": "/home/me/.comfy-local-mcp/assets",
      "models": {
        "flux_unet": "flux1-dev-Q4_K_S.gguf",
        "t5": "t5xxl_fp8_e4m3fn.safetensors",
        "clip_l": "clip_l.safetensors",
        "flux_vae": "flux_vae.safetensors",
        "sdxl_turbo_ckpt": "sd_xl_turbo_1.0_fp16.safetensors",
        "sdxl_base_ckpt": "sd_xl_base_1.0.safetensors",
        "ltx_ckpt": "ltx-video-2b-v0.9.5.safetensors"
      }
    }
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Logical model roles workflows can reference. The setup skill / suggest_config
# fills these with the user's actual installed filenames.
MODEL_ROLES = (
    "flux_unet",
    "t5",
    "clip_l",
    "flux_vae",
    "sdxl_turbo_ckpt",
    "sdxl_base_ckpt",
    "ltx_ckpt",
    "wan_vae",
    "wan_t5",
)


def config_dir() -> Path:
    """Root dir for ComfyLocalMCP user state (config + default assets)."""
    return Path.home() / ".comfy-local-mcp"


def config_path() -> Path:
    """Path to the config file (override with COMFY_LOCAL_MCP_CONFIG)."""
    override = os.environ.get("COMFY_LOCAL_MCP_CONFIG")
    return Path(override) if override else config_dir() / "config.json"


def default_assets_dir() -> str:
    """Where generated assets land when nothing else is configured."""
    return str(config_dir() / "assets")


def load_config() -> dict[str, Any]:
    """Load the config, or {} if none/unreadable (never raises)."""
    path = config_path()
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def save_config(updates: dict[str, Any]) -> dict[str, Any]:
    """Shallow-merge ``updates`` into the config and write it. Returns the result.

    ``models`` is merged one level deep so callers can set a single role without
    dropping the rest.
    """
    cfg = load_config()
    incoming_models = updates.get("models")
    merged = {**cfg, **{k: v for k, v in updates.items() if k != "models"}}
    if incoming_models is not None:
        merged["models"] = {**(cfg.get("models") or {}), **incoming_models}
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def configured_models() -> dict[str, str]:
    """The role -> filename map from config (empty if unset)."""
    return dict(load_config().get("models") or {})
