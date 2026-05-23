# ComfyLocalMCP

**A local-first, device-aware ComfyUI client + MCP server for *building* with local image/video generation.** Use it as a plain Python library inside your own pipelines, or as an MCP server + Claude Code plugin so an agent can build, optimize, and run ComfyUI workflows that actually fit your GPU.

> 🙏 **Shout-out to the ComfyUI + MCP community.** ComfyUI is the de-facto engine for local GenAI, and there's great prior art for driving it from agents — if you want a feature-rich, interactive "talk to ComfyUI from Claude" experience, check out [**artokun/comfyui-mcp**](https://github.com/artokun/comfyui-mcp). ComfyLocalMCP is a complementary, narrower take: **Python-native + embeddable-as-a-library + VRAM/hardware-aware**, aimed at people wiring ComfyUI into their own products.

## Why this one?

- **Library *and* MCP.** Import `comfy_local_mcp` directly in your Python pipeline (deterministic, no agent in the loop), *or* run the MCP server for agent-driven use. Same code, both shapes.
- **Device-aware.** `device_report` + `recommend_workflow` read your actual free VRAM (via ComfyUI's `/system_stats`) and pick a workflow + settings that fit — and only ever suggest workflows whose models/nodes are actually installed. No more 10-minute thrash because Flux + a resident LLM didn't fit on a 16 GB card.
- **Workflows as parameterized templates.** Named ComfyUI graphs with logical override points (`prompt`, `seed`, `width`, `height`, `steps`).
- **Two transports.** `direct` (vanilla ComfyUI on `:8188`) or `rust` (any supervised ComfyUI fronting `/comfy/*` with restart/health/watchdog).

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
