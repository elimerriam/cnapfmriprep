from __future__ import annotations

from pathlib import Path

import nibabel as nb
import numpy as np

import seventprep.nordic as nordic


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
