# Installing / locating ComfyUI (per platform)

Goal: a ComfyUI reachable at `http://127.0.0.1:8188` with `GET /system_stats` returning 200. **Verified path is Windows + NVIDIA portable**; macOS/Linux steps are best-effort and untested here — confirm versions and report issues.

## Detect an existing install first
```
curl -s http://127.0.0.1:8188/system_stats     # 200 + JSON => already running, skip install
```
Common install locations:
- Windows portable: `...\ComfyUI_windows_portable_nvidia\ComfyUI_windows_portable\`
- ComfyUI Desktop: `%APPDATA%\ComfyUI` (config) + app install dir
- comfy-cli / git: wherever the user cloned it (look for `main.py`)

## Windows — NVIDIA portable (verified)
1. Download the portable build from the ComfyUI releases (the `ComfyUI_windows_portable_nvidia` 7z).
2. Extract, then launch: `run_nvidia_gpu.bat` (or `run_nvidia_gpu_fast_fp16_accumulation.bat` for the fp16-accumulation speedup on Ada+ with torch >= 2.7).
3. **torch must be CUDA-built.** If a full update pulled CPU-only torch ("Torch not compiled with CUDA enabled"):
   ```
   python_embeded\python.exe -s -m pip install --force-reinstall torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
   ```
4. Confirm `http://127.0.0.1:8188/system_stats` shows your GPU under `devices[]`.

## ComfyUI Desktop (any OS, easiest for non-developers)
Install the Desktop app from comfy.org / the Comfy-Org/desktop releases. It manages Python + ComfyUI for you and serves on `:8188`. Good default for users who don't want a manual setup.

## comfy-cli (cross-platform, developer)
```
pip install comfy-cli
comfy install            # interactive: picks GPU backend, clones ComfyUI
comfy launch             # serves on 127.0.0.1:8188
```

## git + venv (manual, Linux/macOS/Windows)
```
git clone https://github.com/comfyanonymous/ComfyUI && cd ComfyUI
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
# NVIDIA:
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
# Apple Silicon (MPS): pip install torch torchvision torchaudio   (CPU/MPS wheels)
pip install -r requirements.txt
python main.py            # add --listen for LAN; serves :8188
```

## Custom nodes
Some workflows need custom nodes (e.g. **ComfyUI-GGUF** for `UnetLoaderGGUF`, **ComfyUI-LTXVideo** for LTX video). Install via ComfyUI-Manager (UI) or:
```
cd <ComfyUI>/custom_nodes
git clone https://github.com/city96/ComfyUI-GGUF
git clone https://github.com/Lightricks/ComfyUI-LTXVideo
# then restart ComfyUI; re-run list_models / device_report to confirm
```
