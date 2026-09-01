"""Detection and recoverable quarantine of interrupted Pydra cache entries."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import cloudpickle

from .errors import ValidationError
from .utils import write_json


def _cache_result_state(path: Path) -> tuple[bool, str]:
    try:
        result = cloudpickle.loads(path.read_bytes())
    except Exception as error:
        return False, f"unreadable result: {type(error).__name__}: {error}"
    if not bool(getattr(result, "errored", False)) and getattr(result, "output", None) is None:
        return False, "result was marked successful but contains no output"
    return True, "valid"


def inspect_pydra_cache(cache_dir: str | Path) -> dict[str, Any]:
    """Inspect only active top-level cache entries; backup folders are ignored."""
    root = Path(cache_dir).expanduser().resolve()
    locks = sorted(str(path) for path in root.glob("*.lock")) if root.exists() else []
    invalid: list[dict[str, str]] = []
    if root.exists():
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or not entry.name.startswith(("FunctionTask_", "Workflow_")):
                continue
            result_file = entry / "_result.pklz"
            if not result_file.is_file():
                continue
            valid, reason = _cache_result_state(result_file)
            if not valid:
                invalid.append({"entry": str(entry), "reason": reason})
    return {
        "cache_dir": str(root),
        "locks": locks,
        "invalid_entries": invalid,
    }


def recover_interrupted_pydra_cache(cache_dir: str | Path) -> dict[str, Any]:
    """Move invalid cache entries aside while preserving all valid completed work."""
    root = Path(cache_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    inspection = inspect_pydra_cache(root)
    if inspection["locks"]:
        raise ValidationError(
            "The Pydra cache is locked by an active or unclean workflow. Do not start "
            "a second process in the same work directory. Lock files: "
            + ", ".join(inspection["locks"])
        )
    invalid = inspection["invalid_entries"]
    if not invalid:
        return {**inspection, "recovered": [], "backup_dir": None}
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    backup = root / "interrupted-cache-backups" / stamp
    backup.mkdir(parents=True, exist_ok=False)
    recovered: list[dict[str, str]] = []
    for record in invalid:
        source = Path(record["entry"])
        target = backup / source.name
        shutil.move(str(source), str(target))
        recovered.append(
            {
                "entry": str(source),
                "backup": str(target),
                "reason": record["reason"],
            }
        )
    report = {
        "cache_dir": str(root),
        "locks": [],
        "invalid_entries": [],
        "recovered": recovered,
        "backup_dir": str(backup),
    }
    write_json(backup / "recovery_report.json", report)
    return report
