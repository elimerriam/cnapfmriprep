"""Regression tests for import-safe command-line startup."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _environment() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def test_cli_import_is_lazy() -> None:
    command = [
        sys.executable,
        "-c",
        (
            "import sys; import seventprep.cli; "
            "assert 'seventprep.preprocess' not in sys.modules; "
            "assert 'seventprep.pydra_workflows' not in sys.modules"
        ),
    ]
    subprocess.run(command, check=True, env=_environment())


def test_version_starts_without_preprocessing_imports() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "seventprep.cli", "version"],
        check=True,
        env=_environment(),
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "0.3.0"
