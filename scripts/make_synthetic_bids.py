#!/usr/bin/env python3
"""Create a tiny BIDS-like dataset for semantic-validator and unit-test demos.

This does not simulate DICOM conversion, NORDIC, TOPUP, or ANTs. It only creates
image/metadata relationships that exercise seventprep's discovery code.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import nibabel as nb
import numpy as np


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    func = root / "sub-001" / "ses-01" / "func"
    fmap = root / "sub-001" / "ses-01" / "fmap"
    func.mkdir(parents=True, exist_ok=True)
    fmap.mkdir(parents=True, exist_ok=True)
    write_json(
        root / "dataset_description.json",
        {"Name": "synthetic seventprep demo", "BIDSVersion": "1.10.1", "DatasetType": "raw"},
    )
    affine = np.diag([0.8, 0.8, 0.8, 1.0])
    rng = np.random.default_rng(7)
    bold = func / "sub-001_ses-01_task-demo_run-01_bold.nii.gz"
    nb.Nifti1Image(rng.normal(100, 5, (12, 10, 8, 12)).astype("float32"), affine).to_filename(bold)
    noise = fmap / "sub-001_ses-01_acq-demo_run-01_mod-bold_noRF.nii.gz"
    nb.Nifti1Image(rng.normal(0, 1, (12, 10, 8, 2)).astype("float32"), affine).to_filename(noise)
    for direction, label in (("j-", "AP"), ("j", "PA")):
        epi = fmap / f"sub-001_ses-01_acq-bold_dir-{label}_run-01_epi.nii.gz"
        nb.Nifti1Image(rng.normal(100, 5, (12, 10, 8, 1)).astype("float32"), affine).to_filename(epi)
        write_json(
            Path(str(epi).replace(".nii.gz", ".json")),
            {
                "PhaseEncodingDirection": direction,
                "TotalReadoutTime": 0.03,
                "B0FieldIdentifier": "pepolar01",
            },
        )
    write_json(
        Path(str(noise).replace(".nii.gz", ".json")),
        {"PhaseEncodingDirection": "j-", "TotalReadoutTime": 0.03, "Modality": "bold"},
    )
    write_json(
        Path(str(bold).replace(".nii.gz", ".json")),
        {
            "TaskName": "demo",
            "RepetitionTime": 1.5,
            "PhaseEncodingDirection": "j-",
            "TotalReadoutTime": 0.03,
            "B0FieldSource": "pepolar01",
            "NORDICNoiseFile": noise.relative_to(root).as_posix(),
        },
    )
    print(root)


if __name__ == "__main__":
    main()
