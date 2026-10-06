"""JSON helpers: NumPy values and NaN/inf → strict JSON."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

try:  # NumPy is optional for the marketplace image
    import numpy as np
except ImportError:  # pragma: no cover - depends on environment
    np = None  # type: ignore[assignment]


def to_jsonable(obj: Any) -> Any:
    """Recursively convert ``obj`` into strict-JSON-compatible Python objects.

    NumPy scalars become numbers, arrays become lists, non-finite floats become
    ``None`` (browsers reject ``NaN`` in JSON), paths become strings.
    """
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    if np is not None:
        if isinstance(obj, np.ndarray):
            return [to_jsonable(v) for v in obj.tolist()]
        if isinstance(obj, np.generic):
            obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, Path):
        return str(obj)
    return obj


def dumps(obj: Any, **kwargs: Any) -> str:
    """Strict JSON string."""
    return json.dumps(to_jsonable(obj), allow_nan=False, **kwargs)


def write_json(path: Path, obj: Any) -> None:
    """Write ``obj`` as pretty strict JSON (creating parent folders)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(obj, indent=2), encoding="utf-8")
