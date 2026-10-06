"""Typed configuration values from environment variables.

All configuration is centralised in dataclasses (``autoscaler.config``,
``costwise.config``, ``ml.src.config``). These helpers keep parsing in one place
and give clear errors when a value is malformed, instead of failing deep inside
the control loop.

Every getter accepts either one variable name or a tuple of *aliases*; the first
alias that is set wins. This lets the friendly names used in ``.env.example``
(``BUDGET_PER_HOUR``) coexist with the original names (``MAX_BUDGET``).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

_TRUE = {"1", "true", "yes", "y", "on"}
_FALSE = {"0", "false", "no", "n", "off"}

Names = str | tuple[str, ...]


class ConfigError(ValueError):
    """Raised when a configuration value is missing or invalid."""


def load_dotenv(path: str | os.PathLike[str] = ".env", override: bool = False) -> dict[str, str]:
    """Load ``KEY=VALUE`` pairs from a ``.env`` file into ``os.environ``.

    Small, dependency-free implementation: ``#`` comment lines and blank lines are
    ignored, surrounding quotes are stripped, an unquoted value may carry an inline
    ``  # comment``. Existing environment variables win unless ``override`` is True.
    Returns the parsed pairs (empty if the file does not exist).
    """
    file_path = Path(path)
    if not file_path.is_file():
        return {}
    parsed: dict[str, str] = {}
    for raw_line in file_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        parsed[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return parsed


def _raw(names: Names, env: Mapping[str, str] | None) -> tuple[str, str | None]:
    source = os.environ if env is None else env
    candidates = (names,) if isinstance(names, str) else names
    for name in candidates:
        value = source.get(name)
        if value is not None and value.strip() != "":
            return name, value.strip()
    return candidates[0], None


def get_str(names: Names, default: str, env: Mapping[str, str] | None = None) -> str:
    """Return a string variable or ``default``."""
    _, value = _raw(names, env)
    return default if value is None else value


def get_int(names: Names, default: int, env: Mapping[str, str] | None = None) -> int:
    """Return an integer variable or ``default``."""
    name, value = _raw(names, env)
    if value is None:
        return default
    try:
        return int(float(value)) if re.fullmatch(r"-?\d+\.0*", value) else int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {value!r}") from exc


def get_float(names: Names, default: float, env: Mapping[str, str] | None = None) -> float:
    """Return a float variable or ``default``."""
    name, value = _raw(names, env)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {value!r}") from exc


def get_bool(names: Names, default: bool, env: Mapping[str, str] | None = None) -> bool:
    """Return a boolean variable or ``default``."""
    name, value = _raw(names, env)
    if value is None:
        return default
    lowered = value.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ConfigError(f"{name} must be a boolean (true/false), got {value!r}")


def get_choice(
    names: Names, default: str, choices: tuple[str, ...], env: Mapping[str, str] | None = None
) -> str:
    """Return a variable restricted to ``choices`` (case-insensitive)."""
    name, _ = _raw(names, env)
    value = get_str(names, default, env).lower()
    if value not in choices:
        raise ConfigError(f"{name} must be one of {choices}, got {value!r}")
    return value
