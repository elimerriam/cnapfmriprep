from __future__ import annotations

from pathlib import Path

import nibabel as nb
import numpy as np
import pandas as pd
from nitransforms.io.itk import ITKLinearTransform

from cnapfmriprep.motion import write_motion_metrics


def test_motion_metrics_have_one_row_per_volume(tmp_path: Path) -> None:
    affine_files = []
    for index in range(3):
        matrix = np.eye(4)
        matrix[0, 3] = index * 0.25
        path = tmp_path / f"affine_{index}.txt"
        ITKLinearTransform.from_ras(matrix).to_filename(path)
        affine_files.append(path)
    mask = tmp_path / "mask.nii.gz"
    nb.Nifti1Image(np.ones((5, 6, 4), dtype="uint8"), np.eye(4)).to_filename(mask)
    motion_path, displacement_path = write_motion_metrics(
        affine_files,
        mask,
        tmp_path / "motion.tsv",
        tmp_path / "displacement.tsv",
    )
    motion = pd.read_csv(motion_path, sep="\t")
    displacement = pd.read_csv(displacement_path, sep="\t")
    assert len(motion) == 3
    assert len(displacement) == 3
    assert motion.loc[0, "framewise_displacement_power_mm"] == 0
    assert displacement.loc[2, "absolute_displacement_p95_mm"] > 0
