@echo off
REM Launch the shared ComfyLocalMCP server (streamable HTTP on :9400).
REM Requires: pip install -e "D:\Tools\ComfyLocalMCP[mcp]" and a running ComfyUI.
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if "%COMFY_TRANSPORT%"=="" set COMFY_TRANSPORT=direct
if "%COMFY_MCP_PORT%"=="" set COMFY_MCP_PORT=9400
echo Starting ComfyLocalMCP (transport=%COMFY_TRANSPORT%) on http://127.0.0.1:%COMFY_MCP_PORT%/mcp
python -m comfy_local_mcp.server
