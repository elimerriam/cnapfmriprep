from __future__ import annotations

import json
from pathlib import Path

import nibabel as nb
import numpy as np
import pandas as pd

from seventprep.qc import run_qc_stage


def test_qc_report_is_created(tmp_path: Path) -> None:
    affine = np.eye(4)
    rng = np.random.default_rng(9)
    images = {}
    for name in ("raw", "nordic", "corrected", "preview"):
        path = tmp_path / f"{name}.nii.gz"
        nb.Nifti1Image(rng.normal(size=(8, 9, 6, 4)).astype("float32"), affine).to_filename(path)
        images[name] = path
    scalars = {}
    for name in ("field", "disp", "jac"):
        path = tmp_path / f"{name}.nii.gz"
        data = np.ones((8, 9, 6), dtype="float32") if name == "jac" else rng.normal(size=(8, 9, 6)).astype("float32")
        nb.Nifti1Image(data, affine).to_filename(path)
        scalars[name] = path
    topup_in = tmp_path / "topup_in.nii.gz"
    topup_out = tmp_path / "topup_out.nii.gz"
    nb.Nifti1Image(rng.normal(size=(8, 9, 6, 2)).astype("float32"), affine).to_filename(topup_in)
    nb.Nifti1Image(rng.normal(size=(8, 9, 6, 2)).astype("float32"), affine).to_filename(topup_out)
    motion = pd.DataFrame(
        {
            "trans_x_mm": [0, 0.1, 0.2, 0.1],
            "trans_y_mm": [0, 0, 0, 0],
            "trans_z_mm": [0, 0, 0, 0],
            "rot_x_rad": [0, 0, 0, 0],
            "rot_y_rad": [0, 0, 0, 0],
            "rot_z_rad": [0, 0, 0, 0],
            "framewise_displacement_power_mm": [0, 0.1, 0.1, 0.1],
        }
    )
    displacement = pd.DataFrame(
        {
            "absolute_displacement_median_mm": [0, 0.1, 0.2, 0.1],
            "absolute_displacement_p95_mm": [0, 0.1, 0.2, 0.1],
            "absolute_displacement_max_mm": [0, 0.1, 0.2, 0.1],
            "framewise_displacement_median_mm": [0, 0.1, 0.1, 0.1],
            "framewise_displacement_p95_mm": [0, 0.1, 0.1, 0.1],
            "framewise_displacement_max_mm": [0, 0.1, 0.1, 0.1],
        }
    )
    motion_path = tmp_path / "motion.tsv"
    displacement_path = tmp_path / "displacement.tsv"
    motion.to_csv(motion_path, sep="\t", index=False)
    displacement.to_csv(displacement_path, sep="\t", index=False)
    no_rf = tmp_path / "no_rf.json"
    no_rf.write_text(json.dumps({"number_of_no_rf_volumes": 2}))
    order = tmp_path / "order.json"
    order.write_text(
        json.dumps(
            {
                "selected": "affine_then_sdc",
                "ambiguous": False,
                "mean_normalized_rmse": {"affine_then_sdc": 0.02, "sdc_then_affine": 0.2},
            }
        )
    )
    result = run_qc_stage(
        str(images["raw"]),
        str(images["nordic"]),
        str(images["corrected"]),
        str(images["preview"]),
        str(scalars["field"]),
        str(scalars["disp"]),
        str(scalars["jac"]),
        str(topup_in),
        str(topup_out),
        str(motion_path),
        str(displacement_path),
        str(no_rf),
        str(order),
        str(tmp_path / "qc"),
    )
    assert Path(result["report_html"]).is_file()
    assert len(result["figures"]) == 9
