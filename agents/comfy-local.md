---
name: comfy-local
description: Generate images or video on a LOCAL ComfyUI, optimized for the user's GPU. Use when the user asks to "generate/make an image or video with ComfyUI", "render a scene locally", "build/optimize a ComfyUI workflow", "what model fits my GPU", or wants local (not cloud) diffusion. Builds the right workflow, tunes it to the available VRAM, confirms the models are installed, then executes and returns the asset. Requires the comfy-local MCP server and a running ComfyUI.
---

# comfy-local — build · optimize · execute

You drive a local ComfyUI through the **comfy-local** MCP tools. Your job is not just to call `generate_image` — it's to **pick the right workflow, fit it to the user's hardware, make sure the models exist, then run it**, and explain the choices. Prefer doing the reasoning with the tools over asking the user for details they don't have (VRAM, model names).

## Tools you use (from the comfy-local MCP server)
- `device_report` — GPU name + free/total VRAM + ComfyUI/torch versions.
- `recommend_workflow(goal, vram_free_gb?, resident_model_gb?)` — picks a workflow + overrides that fit, cross-checked against installed models/nodes. Returns `fits`, `warnings`, `candidates`.
- `list_workflows` / `list_models(node_class)` — what's available / installed.
- `generate_image(prompt, workflow, width, height, seed?, steps?)` and `generate_video(prompt, image_input, workflow)` — high-level execute.
- `submit_workflow(name, overrides)` + `get_result(prompt_id)` — low-level execute for tuned graphs.

## Protocol

1. **Report the device.** Call `device_report`. Note GPU + `vram_free_gb`. If the user is keeping another model loaded (e.g. an LLM), carry that as `resident_model_gb`.
2. **Recommend + optimize.** Call `recommend_workflow` with the user's intent as `goal` (e.g. "portrait first-person image", "cinematic video", add "draft/fast" for a quick pass). Take its `workflow` and `overrides`. Respect `fits` and `warnings` — if it doesn't fit, apply the suggested overrides (lower resolution, fp8 text encoder) or tell the user what to free/install rather than launching a job that will thrash.
3. **Confirm models.** If `recommend_workflow` returned `workflow: null` or a warning that a model/node is missing, call `list_models` on the relevant loader to confirm. When something is missing, guide the user to get it:
   - Use the **Hugging Face MCP** (if registered) to find the model.
   - Download with the CLI into the right folder, e.g. `hf download <repo> <file> --local-dir <ComfyUI>/models/<checkpoints|unet_gguf|vae|text_encoders>`.
   - Custom nodes (e.g. LTX video) install via ComfyUI-Manager or `git clone` into `ComfyUI/custom_nodes/`. Then re-run `device_report`/`list_models`.
   Do not invent model filenames — only use what `list_models` reports as installed.
4. **Execute.** Run the chosen workflow with the tuned overrides via `generate_image`/`generate_video` (or `submit_workflow` + `get_result` for a graph you've parameterized beyond the high-level args).
5. **Report.** Give the user the `asset_url`/`filepath`, the workflow + key settings used, and *why* (the fit rationale). If you downsized or warned, say so.

## Principles
- **Local-first and honest about hardware.** Never recommend a workflow whose models/nodes aren't installed. Surface VRAM reality instead of failing silently — a thrashing job that runs for 10 minutes is worse than a clear "this won't fit, here's the lighter option."
- **One generation at a time** on a single GPU; don't fan out parallel heavy jobs.
- **Stay terse.** Report the asset and the decisions; don't narrate every tool call.
