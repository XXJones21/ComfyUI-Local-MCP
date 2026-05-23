"""Drop-in image/video generators with a stable, diffusers-like return shape.

generate_image / generate_video return a dict shaped like::

    {
      "image_data": "<base64>",
      "filepath": "...",
      "filename": "...",
      "prompt": "...",
      "service": "comfy",
      "metadata": {...},
    }

so they slot into code that previously called an in-process SDXL generator —
callers swap only their instantiation line.
"""

from __future__ import annotations

import base64
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from comfy_local_mcp.client import ComfyClient, ComfyError, run_sync
from comfy_local_mcp.config import default_assets_dir, load_config

logger = logging.getLogger(__name__)


@dataclass
class GenerationResult:
    asset_url: str
    local_path: str
    filename: str
    seed: int
    prompt: str
    prompt_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _default_seed() -> int:
    return int.from_bytes(uuid.uuid4().bytes[:4], "big") & 0x7FFFFFFF


class ComfyImageGenerator:
    """Image generator backed by ComfyUI.

    Returns the stable diffusers-like dict (see module docstring) so it's a
    one-line swap for code that previously used an in-process SDXL generator.

    Args:
        client: optional pre-built `ComfyClient`. One is constructed from env if omitted.
        default_workflow: workflow name applied when `generate_image` is called without `workflow=`.
        assets_dir: where downloaded outputs are written; falls back to `COMFY_ASSETS_DIR`.
    """

    service_name = "comfy"

    def __init__(
        self,
        client: ComfyClient | None = None,
        default_workflow: str = "scene_image_sdxl_turbo",
        assets_dir: str | None = None,
    ):
        self.client = client or ComfyClient()
        self.default_workflow = default_workflow
        self.assets_dir = assets_dir or os.environ.get("COMFY_ASSETS_DIR") or load_config().get("assets_dir") or default_assets_dir()

    def generate_image(self, prompt: str, **kwargs) -> dict[str, Any] | None:
        """Synchronous wrapper preserving the legacy return shape.

        Recognized kwargs (others are passed to the workflow as overrides):
          width, height, num_inference_steps, seed, workflow, service
        """
        workflow = kwargs.pop("workflow", None) or self.default_workflow
        # `service=` kept for legacy callers that pass service="sdxl_turbo";
        # the workflow name supersedes it but we keep the field for downstream logs.
        legacy_service = kwargs.pop("service", None)

        seed = int(kwargs.pop("seed", None) or _default_seed())
        overrides = {
            "prompt": prompt,
            "seed": seed,
            **{k: v for k, v in kwargs.items() if v is not None},
        }

        try:
            result = run_sync(self._run(workflow, overrides))
        except ComfyError as err:
            logger.error("[comfy_local_mcp] generation failed (%s): %s", err.kind, err)
            return None
        except Exception as err:  # noqa: BLE001 - protect host pipeline
            logger.exception("[comfy_local_mcp] unexpected generator failure: %s", err)
            return None

        try:
            with open(result.local_path, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode("utf-8")
        except OSError as err:
            logger.error("[comfy_local_mcp] could not read downloaded asset: %s", err)
            b64 = ""

        return {
            "image_data": b64,
            "filepath": result.local_path,
            "filename": result.filename,
            "prompt": prompt,
            "service": self.service_name,
            "metadata": {
                "workflow": workflow,
                "legacy_service": legacy_service,
                "prompt_id": result.prompt_id,
                "seed": seed,
                "asset_url": result.asset_url,
                "generated_at": datetime.now().isoformat(),
                **result.metadata,
            },
        }

    async def _run(self, workflow: str, overrides: dict[str, Any]) -> GenerationResult:
        prompt_id = await self.client.submit(workflow, overrides)
        job = await self.client.wait_for_result(prompt_id)
        local_path = await self.client.download(job, self.assets_dir)
        return GenerationResult(
            asset_url=job.asset_url,
            local_path=local_path,
            filename=job.filename,
            seed=int(overrides.get("seed", 0) or 0),
            prompt=str(overrides.get("prompt", "")),
            prompt_id=prompt_id,
            metadata=job.metadata,
        )


class ComfyVideoGenerator(ComfyImageGenerator):
    """Image-to-video generator.

    Defaults to `scene_video_ltx`. Pass `workflow="scene_video_wan22"` for the
    cinematic/hero-shot path.
    """

    service_name = "comfy_video"

    def __init__(
        self,
        client: ComfyClient | None = None,
        default_workflow: str = "scene_video_ltx",
        assets_dir: str | None = None,
    ):
        super().__init__(client=client, default_workflow=default_workflow, assets_dir=assets_dir)

    def generate_video(
        self,
        prompt: str,
        image_input: str,
        **kwargs,
    ) -> dict[str, Any] | None:
        """Generate a video conditioned on a starting image.

        ``image_input`` is the URL or path to the prior scene's image, used by
        the I2V node in the workflow.
        """
        kwargs.setdefault("image_input", image_input)
        return self.generate_image(prompt, **kwargs)
