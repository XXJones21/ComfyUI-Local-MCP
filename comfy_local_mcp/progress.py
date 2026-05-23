"""Progress streaming helpers — re-exports the canonical async iterator from client.

Kept as a separate module so callers can do `from comfy_local_mcp.progress import stream`
which reads better at call sites that only need progress events.
"""

from __future__ import annotations

from typing import AsyncIterator, Any

from comfy_local_mcp.client import ComfyClient


async def stream(prompt_id: str, client: ComfyClient | None = None) -> AsyncIterator[dict[str, Any]]:
    client = client or ComfyClient()
    async for event in client.stream(prompt_id):
        yield event
