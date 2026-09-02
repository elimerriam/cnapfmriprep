"""Small shared utilities used by the command wrappers and validators."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import nibabel as nb
import numpy as np

from .errors import ExternalCommandError, ValidationError


def ensure_dir(path: str | Path) -> Path:
    """Create *path* when needed and return an absolute ``Path``."""
    output = Path(path).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    return output


def read_json(path: str | Path) -> dict[str, Any]:
    """Read a JSON object and provide a useful validation error."""
    source = Path(path)
    try:
        value = json.loads(source.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValidationError(f"Could not read JSON file {source}: {error}") from error
    if not isinstance(value, dict):
        raise ValidationError(f"Expected a JSON object in {source}")
    return value


def write_json(path: str | Path, value: Any) -> Path:
    """Write deterministic, human-readable JSON."""
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")
    temporary.replace(target)
    return target


def require_executable(command: str) -> str:
    """Resolve an executable name or absolute path."""
    candidate = Path(command).expanduser()
    if candidate.is_absolute() or os.sep in command:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
        raise ExternalCommandError(f"Executable was not found or is not executable: {command}")
    resolved = shutil.which(command)
    if resolved is None:
        raise ExternalCommandError(f"Required executable was not found on PATH: {command}")
    return resolved


def run_command(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    log_file: str | Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run an external command and capture a reproducible log."""
    args = [str(item) for item in command]
    if not args:
        raise ValueError("run_command requires at least one argument")
    completed = subprocess.run(
        args,
        cwd=str(Path(cwd).resolve()) if cwd is not None else None,
        env=dict(os.environ) | (dict(env) if env is not None else {}),
        text=True,
        capture_output=True,
        check=False,
    )
    if log_file is not None:
        target = Path(log_file).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "$ " + shlex.join(args) + "\n\n"
            + "[stdout]\n" + completed.stdout
            + "\n[stderr]\n" + completed.stderr
            + f"\n[returncode]\n{completed.returncode}\n"
        )
    if check and completed.returncode != 0:
        tail = (completed.stderr or completed.stdout).strip()
        if len(tail) > 2000:
            tail = tail[-2000:]
        log_hint = f" See {Path(log_file).resolve()}." if log_file is not None else ""
        raise ExternalCommandError(
            f"Command failed with exit status {completed.returncode}: {shlex.join(args)}."
            f"{log_hint}\n{tail}"
        )
    return completed


def _spatial_shape(image: nb.spatialimages.SpatialImage) -> tuple[int, int, int]:
    if len(image.shape) < 3:
        raise ValidationError(f"Expected at least three image dimensions, got {image.shape}")
    return tuple(int(value) for value in image.shape[:3])


def same_nifti_grid(
    first: str | Path,
    second: str | Path,
    *,
    affine_tolerance: float = 1e-5,
) -> bool:
    """Return whether two NIfTI images share a spatial shape and affine."""
    image_a = nb.load(str(first))
    image_b = nb.load(str(second))
    return _spatial_shape(image_a) == _spatial_shape(image_b) and np.allclose(
        image_a.affine,
        image_b.affine,
        rtol=0.0,
        atol=affine_tolerance,
    )


def assert_same_nifti_grid(
    first: str | Path,
    second: str | Path,
    *,
    context: str = "images",
    affine_tolerance: float = 1e-5,
) -> None:
    """Raise ``ValidationError`` when two NIfTI grids differ."""
    if not same_nifti_grid(first, second, affine_tolerance=affine_tolerance):
        first_image = nb.load(str(first))
        second_image = nb.load(str(second))
        raise ValidationError(
            f"The {context} are not on the same spatial grid: "
            f"{Path(first).name} shape={first_image.shape[:3]} and "
            f"{Path(second).name} shape={second_image.shape[:3]}"
        )
