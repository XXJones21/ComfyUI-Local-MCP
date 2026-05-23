"""Backward-compat shim. `engram_comfy` was renamed to `comfy_local_mcp`.

Re-exports the public API so existing consumers keep working. Prefer importing
`comfy_local_mcp` directly in new code.
"""

from comfy_local_mcp import *  # noqa: F401,F403
from comfy_local_mcp import (  # noqa: F401
    ComfyClient,
    ComfyError,
    ComfyImageGenerator,
    ComfyVideoGenerator,
    GenerationResult,
    list_workflows,
    load_workflow,
)
