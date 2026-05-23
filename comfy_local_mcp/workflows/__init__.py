"""Workflow JSON registry.

Each workflow is a ComfyUI API-format JSON graph with named override points.
Overrides are applied as `(node_id, input_key) -> value` substitutions before
the graph is submitted.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

_WORKFLOWS_DIR = Path(__file__).parent


def list_workflows() -> list[str]:
    return sorted(p.stem for p in _WORKFLOWS_DIR.glob("*.json"))


def load_workflow(name: str, overrides: dict[str, Any] | None = None) -> dict:
    """Load a workflow JSON and apply named overrides.

    Override keys use the form ``"<node_id>.<input_key>"``. The workflow JSON
    contains a top-level ``"_overrides"`` map from logical names (e.g. ``"prompt"``)
    to ``"<node_id>.<input_key>"`` targets. Callers pass logical names; this
    function resolves them.
    """
    path = _WORKFLOWS_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"workflow '{name}' not found; available: {list_workflows()}"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    graph = copy.deepcopy(raw.get("graph") or raw)
    override_map: dict[str, str] = raw.get("_overrides") or {}
    for key, value in (overrides or {}).items():
        target = override_map.get(key, key)
        if "." not in target:
            raise KeyError(
                f"workflow '{name}': override '{key}' must resolve to '<node_id>.<input_key>'"
            )
        node_id, input_key = target.split(".", 1)
        # Targets are authored as "<node_id>.inputs.<field>" but the value is
        # assigned under node["inputs"], so strip a redundant leading "inputs."
        # (also tolerate the bare "<node_id>.<field>" form).
        if input_key.startswith("inputs."):
            input_key = input_key[len("inputs."):]
        node = graph.get(node_id)
        if node is None:
            raise KeyError(
                f"workflow '{name}': node '{node_id}' (target of '{key}') not in graph"
            )
        node.setdefault("inputs", {})[input_key] = value
    return graph
