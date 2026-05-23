# /comfy-gen - Generate locally, optimized for your GPU

Generate an image (or video) on the local ComfyUI, tuned to fit available VRAM.

## Process
1. Hand off to the **comfy-local** subagent with the user's prompt and intent.
2. The agent runs `device_report` → `recommend_workflow` → confirms models via `list_models` → executes `generate_image`/`generate_video`.
3. Return the asset URL/path plus the workflow + settings used and why.

Usage: `/comfy-gen a first-person POV of a rain-soaked neon alley at night`
