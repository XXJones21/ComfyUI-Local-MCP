"""HTTP + WebSocket client for the ComfyUI harness.

Two transports:
  - ``rust``    : talks to a supervised ComfyUI server's /comfy/* routes (production)
  - ``direct``  : talks to ComfyUI's /prompt and /ws directly (dev / offline)

The public surface is identical across transports.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx
import websockets

from comfy_local_mcp.config import default_assets_dir, load_config
from comfy_local_mcp.workflows import load_workflow

logger = logging.getLogger(__name__)


class ComfyError(RuntimeError):
    """Raised when the supervisor or ComfyUI reports a job failure."""

    def __init__(self, kind: str, message: str, prompt_id: str | None = None):
        super().__init__(message)
        self.kind = kind
        self.prompt_id = prompt_id


@dataclass
class JobResult:
    prompt_id: str
    asset_url: str
    local_path: str | None
    filename: str
    metadata: dict[str, Any]


class ComfyClient:
    """Async client for the ComfyUI harness.

    Use ``ComfyImageGenerator`` / ``ComfyVideoGenerator`` for the sync,
    drop-in-shaped API most callers consume. Use this class directly when you
    need progress streaming.
    """

    def __init__(
        self,
        base_url: str | None = None,
        transport: str | None = None,
        timeout_s: float | None = None,
        client_id: str | None = None,
    ):
        # Precedence: explicit arg -> env -> user config -> built-in default.
        # Default is `direct` (vanilla ComfyUI on :8188) — what a fresh ComfyUI
        # install serves. `rust` is opt-in for a supervised backend on :8765.
        cfg = load_config()
        self.transport = (transport or os.environ.get("COMFY_TRANSPORT") or cfg.get("transport") or "direct").lower()
        if self.transport not in {"rust", "direct"}:
            raise ValueError(f"transport must be 'rust' or 'direct', got {self.transport!r}")
        default_base = "http://127.0.0.1:8188" if self.transport == "direct" else "http://127.0.0.1:8765"
        self.base_url = (base_url or os.environ.get("COMFY_BASE_URL") or cfg.get("base_url") or default_base).rstrip("/")
        self.timeout_s = float(timeout_s or os.environ.get("COMFY_TIMEOUT_S") or cfg.get("timeout_s") or 300)
        self.client_id = client_id or uuid.uuid4().hex

    # ---- public API ----------------------------------------------------

    async def submit(self, workflow: str, overrides: dict[str, Any] | None = None) -> str:
        """Submit a workflow and return its ``prompt_id``."""
        graph = load_workflow(workflow, overrides)
        if self.transport == "rust":
            url = f"{self.base_url}/comfy/submit"
            body = {"workflow_name": workflow, "graph": graph, "client_id": self.client_id}
        else:
            url = f"{self.base_url}/prompt"
            body = {"prompt": graph, "client_id": self.client_id}
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.post(url, json=body)
        if resp.status_code >= 400:
            raise ComfyError("http", f"submit failed {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyError("invalid_response", f"submit returned no prompt_id: {data!r}")
        logger.info("comfy submit ok workflow=%s prompt_id=%s", workflow, prompt_id)
        return prompt_id

    async def stream(self, prompt_id: str) -> AsyncIterator[dict[str, Any]]:
        """Yield progress events until the job finishes or errors.

        Events: ``{"stage": "queued"|"executing"|"progress"|"executed"|"error", ...}``
        """
        ws_url = self._ws_url(prompt_id)
        async with websockets.connect(ws_url, max_size=2**24) as ws:
            async for raw in ws:
                if isinstance(raw, bytes):
                    # Direct ComfyUI sends preview images as binary; pass through metadata only.
                    yield {"stage": "preview", "bytes": len(raw)}
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                event = self._normalize_event(msg, prompt_id)
                if event is None:
                    continue
                yield event
                if event["stage"] in {"executed", "error", "completed"}:
                    break

    async def wait_for_result(self, prompt_id: str) -> JobResult:
        """Block until the job completes and return its primary asset.

        Resilient to WebSocket drops on long renders: ComfyUI keeps executing a
        submitted job server-side even if the progress WS closes (which happens
        on multi-minute video renders). If the WS errors or ends before the
        completion event, fall back to polling ``/history/{prompt_id}`` so a
        transient WS drop no longer reports a false failure (and discards a
        video ComfyUI actually produced).
        """
        completed = False
        try:
            async for event in self.stream(prompt_id):
                if event["stage"] == "error":
                    raise ComfyError(event.get("kind", "error"), event.get("message", "job failed"), prompt_id)
                if event["stage"] in {"executed", "completed"}:
                    completed = True
                    break
        except ComfyError:
            raise
        except Exception as exc:  # noqa: BLE001 — any WS failure (close/timeout/reset)
            logger.warning(
                "comfy progress WS ended early for prompt_id=%s (%s); polling /history",
                prompt_id, exc,
            )
        if not completed:
            # WS dropped or ended without a completion event — confirm via history.
            await self._poll_history_until_done(prompt_id)
        return await self.fetch_result(prompt_id)

    async def _poll_history_until_done(self, prompt_id: str, poll_interval: float = 3.0) -> None:
        """Poll ``/history/{prompt_id}`` until the job completes or errors.

        Bounded by ``timeout_s``. Used as the WS-drop fallback in
        ``wait_for_result`` so long renders are recovered rather than failed.
        """
        if self.transport == "rust":
            history_url = f"{self.base_url}/comfy/history/{prompt_id}"
        else:
            history_url = f"{self.base_url}/history/{prompt_id}"
        deadline = asyncio.get_event_loop().time() + self.timeout_s
        async with httpx.AsyncClient(timeout=30.0) as http:
            while True:
                try:
                    resp = await http.get(history_url)
                    if resp.status_code < 400:
                        history = resp.json()
                        entry = history.get(prompt_id) if isinstance(history, dict) else None
                        if entry:
                            status = entry.get("status") or {}
                            if status.get("status_str") == "error":
                                raise ComfyError("execution_error", "ComfyUI reported job error", prompt_id)
                            if status.get("completed") or entry.get("outputs"):
                                return
                except ComfyError:
                    raise
                except Exception:  # noqa: BLE001 — transient fetch error, keep polling
                    pass
                if asyncio.get_event_loop().time() > deadline:
                    raise ComfyError("timeout", f"job did not complete within {self.timeout_s}s", prompt_id)
                await asyncio.sleep(poll_interval)

    async def fetch_result(self, prompt_id: str) -> JobResult:
        """Fetch the primary output asset metadata for a completed job."""
        if self.transport == "rust":
            history_url = f"{self.base_url}/comfy/history/{prompt_id}"
        else:
            history_url = f"{self.base_url}/history/{prompt_id}"
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.get(history_url)
        if resp.status_code >= 400:
            raise ComfyError("http", f"history fetch failed {resp.status_code}", prompt_id)
        history = resp.json()
        entry = history.get(prompt_id) if isinstance(history, dict) and prompt_id in history else history
        outputs = (entry or {}).get("outputs") or {}
        for node_outputs in outputs.values():
            for asset_kind in ("images", "gifs", "videos"):
                items = node_outputs.get(asset_kind) or []
                if items:
                    item = items[0]
                    filename = item.get("filename") or "output.png"
                    subfolder = item.get("subfolder", "")
                    asset_url = self._asset_url(filename, subfolder, item.get("type", "output"))
                    return JobResult(
                        prompt_id=prompt_id,
                        asset_url=asset_url,
                        local_path=None,
                        filename=filename,
                        metadata={"history_entry": entry, "node_outputs": node_outputs},
                    )
        raise ComfyError("invalid_response", "history entry contained no image/video outputs", prompt_id)

    async def download(self, result: JobResult, dest_dir: str | None = None) -> str:
        """Download the result asset to ``dest_dir`` and return the local path."""
        target_dir = dest_dir or os.environ.get("COMFY_ASSETS_DIR") or load_config().get("assets_dir") or default_assets_dir()
        os.makedirs(target_dir, exist_ok=True)
        local_path = os.path.join(target_dir, result.filename)
        async with httpx.AsyncClient(timeout=self.timeout_s) as http:
            async with http.stream("GET", result.asset_url) as resp:
                if resp.status_code >= 400:
                    raise ComfyError("http", f"asset download failed {resp.status_code}", result.prompt_id)
                with open(local_path, "wb") as fh:
                    async for chunk in resp.aiter_bytes():
                        fh.write(chunk)
        result.local_path = local_path
        return local_path

    async def object_info(self, node_class: str | None = None) -> dict[str, Any]:
        """Fetch ComfyUI's ``/object_info`` (optionally for one node class).

        Useful for discovery/diagnostics — e.g. enumerating the checkpoints,
        unets, or VAEs a node will accept, which catches "model not installed"
        errors before submitting a job. The ``rust`` supervisor does not proxy
        this today, so it is reachable only on the ``direct`` transport.
        """
        if self.transport == "rust":
            url = f"{self.base_url}/comfy/object_info"
        else:
            url = f"{self.base_url}/object_info"
        if node_class:
            url = f"{url}/{node_class}"
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.get(url)
        if resp.status_code >= 400:
            raise ComfyError(
                "http",
                f"object_info fetch failed {resp.status_code} (transport={self.transport}): {resp.text[:200]}",
            )
        return resp.json()

    async def system_stats(self) -> dict[str, Any]:
        """Fetch ComfyUI's ``/system_stats`` (system info + per-device VRAM).

        Returns ``{"system": {...}, "devices": [{"name", "type", "vram_total",
        "vram_free", ...}]}``. VRAM values are bytes. Powers ``device_report``.
        """
        if self.transport == "rust":
            url = f"{self.base_url}/comfy/system_stats"
        else:
            url = f"{self.base_url}/system_stats"
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.get(url)
        if resp.status_code >= 400:
            raise ComfyError(
                "http",
                f"system_stats fetch failed {resp.status_code} (transport={self.transport}): {resp.text[:200]}",
            )
        return resp.json()

    # ---- internals -----------------------------------------------------

    def _ws_url(self, prompt_id: str) -> str:
        # Translate http(s) -> ws(s)
        scheme = "wss" if self.base_url.startswith("https://") else "ws"
        host = self.base_url.split("://", 1)[1]
        if self.transport == "rust":
            return f"{scheme}://{host}/comfy/stream/{prompt_id}"
        # Direct ComfyUI uses a single global WS keyed by client_id; we filter
        # events by prompt_id ourselves.
        return f"{scheme}://{host}/ws?clientId={self.client_id}"

    def _asset_url(self, filename: str, subfolder: str, asset_type: str) -> str:
        if self.transport == "rust":
            return f"{self.base_url}/comfy/asset/{filename}"
        params = f"filename={filename}&type={asset_type}"
        if subfolder:
            params += f"&subfolder={subfolder}"
        return f"{self.base_url}/view?{params}"

    def _normalize_event(self, msg: dict, expected_prompt_id: str) -> dict | None:
        """Convert a ComfyUI WS message into the harness's normalized event shape."""
        msg_type = msg.get("type")
        data = msg.get("data") or {}
        # The Rust supervisor forwards a pre-filtered stream and may add a "stage"
        # field directly; pass it through if present.
        if "stage" in msg:
            return msg
        if data.get("prompt_id") and data["prompt_id"] != expected_prompt_id:
            return None
        if msg_type == "status":
            queue_remaining = (data.get("status") or {}).get("exec_info", {}).get("queue_remaining")
            return {"stage": "queued", "queue_remaining": queue_remaining}
        if msg_type == "execution_start":
            return {"stage": "executing", "node": None}
        if msg_type == "executing":
            node = data.get("node")
            if node is None:
                return {"stage": "completed"}
            return {"stage": "executing", "node": node}
        if msg_type == "progress":
            value = data.get("value", 0)
            maximum = data.get("max", 1) or 1
            return {
                "stage": "progress",
                "percent": round(100.0 * value / maximum, 2),
                "value": value,
                "max": maximum,
            }
        if msg_type == "executed":
            return {"stage": "executed", "node": data.get("node"), "output": data.get("output")}
        if msg_type == "execution_error":
            return {
                "stage": "error",
                "kind": "execution_error",
                "message": data.get("exception_message", "ComfyUI execution error"),
                "node": data.get("node_id"),
            }
        return None


def run_sync(coro):
    """Run an async coroutine from sync code, even when a loop is already present."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # We're inside a running loop (e.g. FastAPI request handler). Spin a thread.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result()
