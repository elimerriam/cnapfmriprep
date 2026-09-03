from __future__ import annotations

from pathlib import Path

import nibabel as nb
import numpy as np
import pytest

import cnapfmriprep.nordic as nordic
from cnapfmriprep.errors import ExternalCommandError
from cnapfmriprep.progress import progress_context


def test_concatenate_and_explicit_trim(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(nordic.shutil, "which", lambda _: None)
    affine = np.diag([0.8, 0.8, 0.8, 1.0])
    bold = tmp_path / "bold.nii.gz"
    noise = tmp_path / "noise.nii.gz"
    nb.Nifti1Image(np.ones((5, 6, 4, 3), dtype="float32"), affine).to_filename(bold)
    nb.Nifti1Image(np.full((5, 6, 4, 2), 2, dtype="float32"), affine).to_filename(noise)
    merged, count = nordic.concatenate_bold_and_noise(
        bold, noise, tmp_path / "merged.nii.gz", expected_noise_volumes=2
    )
    assert count == 3
    assert nb.load(merged).shape == (5, 6, 4, 5)
    signal, no_rf = nordic._split_nordic_output(
        merged,
        3,
        2,
        tmp_path / "signal.nii.gz",
        tmp_path / "no_rf.nii.gz",
    )
    assert nb.load(signal).shape[3] == 3
    assert nb.load(no_rf).shape[3] == 2
    assert np.allclose(nb.load(no_rf).get_fdata(), 2)


def test_matlab_license_failure_retries_with_bounded_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "NORDIC_Raw"
    checkout.mkdir()
    (checkout / "NIFTI_NORDIC.m").write_text("% test\n")
    magnitude = tmp_path / "bold.nii.gz"
    magnitude.touch()
    calls: list[int] = []
    sleeps: list[float] = []

    def fake_run(command, *, log_file, **kwargs):
        calls.append(1)
        Path(log_file).write_text("mock log\n")
        if len(calls) == 1:
            raise ExternalCommandError("License checkout failed: no licenses available")
        (Path(log_file).parent / "nordic_full.nii.gz").touch()

    monkeypatch.setattr(nordic, "run_command", fake_run)
    monkeypatch.setattr(nordic, "_find_executable", lambda command: command)
    monkeypatch.setattr(nordic.time, "sleep", sleeps.append)
    events = tmp_path / "progress.jsonl"

    result = nordic._run_nordic_matlab(
        magnitude,
        tmp_path / "output",
        checkout=checkout,
        matlab_command="matlab",
        noise_volume_last=2,
        factor_error=1.0,
        save_gfactor_map=True,
        save_additional_info=True,
        license_retries=3,
        license_retry_initial_seconds=2,
        license_retry_max_seconds=3,
        progress_context=progress_context(events, run_index=1, run_label="BIDS run 1"),
    )

    assert len(calls) == 2
    assert sleeps == [2]
    assert Path(result["full_output"]).is_file()
    assert '"status":"retrying"' in events.read_text()
    assert (tmp_path / "output" / "matlab_nordic_license_attempt-01.log").is_file()
