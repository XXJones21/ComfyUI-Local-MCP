# Model bootstrap (download recipes)

Use the **Hugging Face MCP** to discover/verify models, and the `hf` CLI to download them into ComfyUI's model folders. Install the CLI with `pip install -U "huggingface_hub[cli]"`; log in with `hf auth login` for gated repos. Replace `<ComfyUI>` with the install's root (the folder containing `models/`).

Always confirm what `recommend_workflow` says fits before downloading — on a tight GPU prefer the **fp8** text encoder.

## Flux GGUF image set (hero path; ~12–16 GB depending on text encoder)
For the `scene_image_flux` workflow. Nodes required: `UnetLoaderGGUF` (ComfyUI-GGUF), `DualCLIPLoader`, `VAELoader`.

```
# UNET (Q4 GGUF ~6.4 GB) -> models/unet
hf download city96/FLUX.1-dev-gguf flux1-dev-Q4_K_S.gguf --local-dir <ComfyUI>/models/unet

# Text encoders -> models/text_encoders  (pick ONE t5xxl by VRAM)
hf download comfyanonymous/flux_text_encoders clip_l.safetensors --local-dir <ComfyUI>/models/text_encoders
hf download comfyanonymous/flux_text_encoders t5xxl_fp16.safetensors --local-dir <ComfyUI>/models/text_encoders          # ~9.2 GB, best quality
hf download comfyanonymous/flux_text_encoders t5xxl_fp8_e4m3fn.safetensors --local-dir <ComfyUI>/models/text_encoders     # ~4.9 GB, fits tight VRAM / coexists with an LLM

# VAE (16-channel Flux VAE) -> models/vae   (FLUX.1-dev is gated: accept the license + hf auth login)
hf download black-forest-labs/FLUX.1-dev ae.safetensors --local-dir <ComfyUI>/models/vae
# rename to flux_vae.safetensors if your workflow expects that name
```
> The Flux VAE is **16-channel**. Do not substitute a 4-channel SD VAE (causes a VAEDecode channel-mismatch error).

## SDXL-Turbo (lighter draft/fast path; ~6.5 GB)
For `scene_image_sdxl_turbo`. Node: `CheckpointLoaderSimple`.
```
hf download stabilityai/sdxl-turbo sd_xl_turbo_1.0_fp16.safetensors --local-dir <ComfyUI>/models/checkpoints
```

## Video (optional)
- **LTX-Video** (`scene_video_ltx`): needs the **ComfyUI-LTXVideo** custom node (`LTXVCheckpointLoader`) plus an LTX checkpoint in `models/checkpoints`. Install the node first (see install-comfyui.md), then download the LTX model from `Lightricks/LTX-Video`.
- **Wan2.2** (`scene_video_wan22`, cinematic): larger; verify it fits before downloading.

## After downloading
ComfyUI caches its model lists — restart it or refresh, then run `list_models(<loader_node>)` to confirm the file shows up, and `recommend_workflow` to re-check fit.
