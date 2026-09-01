from pathlib import Path

import pytest

from cnapfmriprep.errors import ValidationError
from cnapfmriprep.preprocess import _select_reference_run, _validate_shared_inputs


def _record(
    tmp_path: Path,
    run: int,
    *,
    task: str = "demo",
    fieldmap_stem: str = "shared",
) -> dict:
    return {
        "bold": tmp_path / f"sub-001_task-{task}_run-{run:02d}_bold.nii.gz",
        "bold_json": tmp_path / f"run-{run:02d}.json",
        "noise": tmp_path / f"run-{run:02d}_noRF.nii.gz",
        "fieldmaps": [
            tmp_path / f"{fieldmap_stem}_ap.nii.gz",
            tmp_path / f"{fieldmap_stem}_pa.nii.gz",
        ],
        "fieldmap_jsons": [
            tmp_path / f"{fieldmap_stem}_ap.json",
            tmp_path / f"{fieldmap_stem}_pa.json",
        ],
        "entities": {"task": task, "acq": "hires", "run": f"{run:02d}"},
        "metadata": {"B0FieldSource": "pepolar"},
    }


def test_selects_explicit_reference_task_and_run(tmp_path: Path) -> None:
    records = [
        _record(tmp_path, 2),
        _record(tmp_path, 1),
        _record(tmp_path, 1, task="localizer"),
    ]
    selected = _select_reference_run(
        records,
        reference_task="demo",
        reference_run=1,
    )
    assert selected["entities"]["task"] == "demo"
    assert selected["entities"]["run"] == "01"


def test_reference_run_is_ambiguous_across_tasks_without_task(tmp_path: Path) -> None:
    records = [
        _record(tmp_path, 1, task="demo"),
        _record(tmp_path, 1, task="localizer"),
    ]
    with pytest.raises(ValidationError, match="ambiguous across tasks"):
        _select_reference_run(records, reference_task=None, reference_run=1)


def test_shared_topup_requires_same_resolved_fieldmaps(tmp_path: Path) -> None:
    reference = _record(tmp_path, 1, fieldmap_stem="first")
    other = _record(tmp_path, 2, fieldmap_stem="second")
    with pytest.raises(ValidationError, match="same reverse-PE files"):
        _validate_shared_inputs(
            [reference, other],
            shared_topup=True,
            shared_motion_reference=False,
            reference_run=reference,
        )
