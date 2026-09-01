"""Portable execution profiles for laptops, workstations, and servers."""

from __future__ import annotations

import os
from typing import Any

from .errors import ValidationError

_PROFILES: dict[str, dict[str, int | str]] = {
    "laptop": {
        "pydra_plugin": "serial",
        "n_procs": 1,
        "volume_workers": 1,
        "threads_per_ants": 2,
    },
    "workstation": {
        "pydra_plugin": "cf",
        "n_procs": 2,
        "volume_workers": 2,
        "threads_per_ants": 2,
    },
    "server": {
        "pydra_plugin": "cf",
        "n_procs": 4,
        "volume_workers": 4,
        "threads_per_ants": 2,
    },
}


def physical_memory_gb() -> float | None:
    """Return installed physical memory without adding a system dependency."""
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        page_count = int(os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    if page_size <= 0 or page_count <= 0:
        return None
    return page_size * page_count / 1024**3


def choose_auto_profile(
    *,
    memory_gb: float | None = None,
    cpu_count: int | None = None,
) -> str:
    """Choose a conservative profile from RAM and logical CPU capacity."""
    memory = physical_memory_gb() if memory_gb is None else float(memory_gb)
    cpus = os.cpu_count() if cpu_count is None else int(cpu_count)
    cpus = max(int(cpus or 1), 1)
    if memory is None or memory < 48 or cpus < 8:
        return "laptop"
    if memory < 128 or cpus < 24:
        return "workstation"
    return "server"


def execution_profiles() -> dict[str, dict[str, int | str]]:
    """Return copies of the named profile settings for CLI display."""
    return {name: dict(values) for name, values in _PROFILES.items()}


def resolve_execution_mapping(
    values: dict[str, Any],
    *,
    profile_override: str | None = None,
) -> dict[str, Any]:
    """Apply a named preset while retaining explicitly supplied field overrides."""
    source = dict(values)
    requested = profile_override or str(source.get("profile", "custom"))
    allowed = {"custom", "laptop", "workstation", "server", "auto"}
    if requested not in allowed:
        raise ValidationError(
            f"Unknown execution profile {requested!r}; choose one of {sorted(allowed)}"
        )
    resolved = choose_auto_profile() if requested == "auto" else requested
    if profile_override is not None:
        # A command-line profile is an explicit request for the complete preset.
        source = {
            key: value
            for key, value in source.items()
            if key in {"show_progress", "progress_interval_percent"}
        }
    preset = dict(_PROFILES.get(resolved, {}))
    preset.update(source)
    preset["profile"] = requested
    preset["resolved_profile"] = resolved
    return preset


def apply_execution_profile(config: Any, profile: str) -> Any:
    """Return a StudyConfig copy with a command-line execution profile applied."""
    from .config import ExecutionConfig

    mapping = resolve_execution_mapping(
        config.execution.model_dump(mode="python"),
        profile_override=profile,
    )
    return config.model_copy(update={"execution": ExecutionConfig.model_validate(mapping)})
