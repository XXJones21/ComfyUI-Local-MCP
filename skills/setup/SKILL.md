---
name: comfy-local-setup
description: "Set up ComfyLocalMCP end to end: detect the GPU, install/locate ComfyUI, install the comfy-local-mcp package, register the comfy-local MCP and the Hugging Face MCP, and bootstrap a working image model. Use when: 'set up comfy-local', 'install ComfyLocalMCP', 'get local image generation working', 'connect Claude to my ComfyUI'. Outputs: a verified local generation stack and a smoke-tested image."
version: 0.1.0
---

# ComfyLocalMCP setup

Walk the user through standing up a local ComfyUI generation stack that Claude can drive. Go step by step, confirm each before moving on, and prefer detection over assumptions. Cross-platform; **verified on Windows + NVIDIA**, best-effort on macOS/Linux — flag untested branches honestly.

Detailed per-OS commands live in `references/install-comfyui.md`; model download recipes in `references/models.md`. Read them when you reach those steps.

## Step 0 — Detect the device
Run `device_report` (comfy-local MCP) if the server is already up; otherwise:
- Windows/Linux NVIDIA: `nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader`
- macOS: `system_profiler SPDisplaysDataType | grep -A3 Chipset` (Apple Silicon → MPS, not CUDA)
Record GPU name and total VRAM — it drives every model choice below.

## Step 1 — ComfyUI
Check if one is already running: `GET http://127.0.0.1:8188/system_stats` (200 = present). If yes, note its install path and skip install. If not, install per platform (see `references/install-comfyui.md`): Windows portable, the ComfyUI **Desktop** app, `comfy-cli`, or git+venv. Start it and re-confirm `/system_stats`.

## Step 2 — Install comfy-local-mcp
From the repo root: `pip install -e ".[mcp]"` (or `pip install "comfy-local-mcp[mcp] @ git+https://github.com/XXJones21/ComfyUI-Local-MCP"`). Use the **same Python** that Claude Code's plugin will launch (the one on PATH). Verify: `python -c "import comfy_local_mcp; print(comfy_local_mcp.list_workflows())"`.

## Step 3 — Register the MCP servers
- **comfy-local**: if installed as a Claude Code plugin, the bundled `.mcp.json` wires it automatically (stdio). For a standalone/shared HTTP server instead: start `comfy-local-mcp --transport http` and `claude mcp add --transport http comfy-local http://127.0.0.1:9400/mcp`.
- **Hugging Face MCP** (for model discovery): `claude mcp add hf-mcp-server -t http "https://huggingface.co/mcp?login"` then complete the OAuth login. Configure enabled tools at https://huggingface.co/settings/mcp. Note: the HF MCP **finds** models; it does not download files — pulls use the `hf` CLI (Step 4).
Confirm with `claude mcp list` (both should show connected).

## Step 4 — Bootstrap a model that fits
Call `recommend_workflow(goal="image")` — it reports what's installed and what fits the GPU. If the recommended workflow's model is missing, install the **Flux GGUF set** (or a lighter option for small GPUs) per `references/models.md`, e.g.:
```
hf download city96/FLUX.1-dev-gguf flux1-dev-Q4_K_S.gguf --local-dir <ComfyUI>/models/unet
hf download comfyanonymous/flux_text_encoders t5xxl_fp8_e4m3fn.safetensors --local-dir <ComfyUI>/models/text_encoders
```
Pick the text-encoder precision `recommend_workflow` advises (fp8 on tight VRAM). Re-run `list_models` to confirm registration (ComfyUI may need a refresh/restart).

## Step 5 — Verify
1. `device_report` → sane GPU + VRAM.
2. `recommend_workflow(goal="image")` → a workflow with `fits: true` (or a clear downsize warning).
3. `generate_image(prompt="first person POV, misty forest at dusk")` → returns an `asset_url`. Open it.
Report the final working configuration (GPU, workflow, model, text-encoder precision) so the user knows their baseline.

## Notes
- Re-run this skill after a ComfyUI update — custom nodes/torch can break (e.g. a CPU-only torch reinstall kills CUDA).
- If `generate_image` hangs for minutes, it's almost always VRAM thrash: free other models or let `recommend_workflow` downsize.
