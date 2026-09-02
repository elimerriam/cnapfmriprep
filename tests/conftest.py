from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def synthetic_bids(tmp_path: Path) -> Path:
    import nibabel as nb
    import numpy as np

    root = tmp_path / "bids"
    func = root / "sub-001" / "ses-01" / "func"
    fmap = root / "sub-001" / "ses-01" / "fmap"
    func.mkdir(parents=True)
    fmap.mkdir(parents=True)
    (root / "dataset_description.json").write_text(
        json.dumps({"Name": "test", "BIDSVersion": "1.10.1", "DatasetType": "raw"})
    )
    affine = np.diag([0.8, 0.8, 0.8, 1.0])
    rng = np.random.default_rng(42)
    bold = func / "sub-001_ses-01_task-demo_run-01_bold.nii.gz"
    nb.Nifti1Image(rng.normal(100, 5, size=(8, 9, 6, 7)).astype("float32"), affine).to_filename(bold)
    noise = func / "sub-001_ses-01_task-demo_acq-demo_run-01_mod-bold_noRF.nii.gz"
    nb.Nifti1Image(rng.normal(size=(8, 9, 6, 2)).astype("float32"), affine).to_filename(noise)
    sidecar = noise.with_name(noise.name[:-7] + ".json")
    sidecar.write_text(
        json.dumps(
            {
                "TaskName": "demo",
                "RepetitionTime": 1.2,
                "PhaseEncodingDirection": "j-",
                "TotalReadoutTime": 0.03,
            }
        )
    )
    fieldmaps = []
    for label, pe in (("AP", "j-"), ("PA", "j")):
        epi = fmap / f"sub-001_ses-01_acq-bold_dir-{label}_run-01_epi.nii.gz"
        nb.Nifti1Image(rng.normal(100, 5, size=(8, 9, 6, 1)).astype("float32"), affine).to_filename(epi)
        sidecar = epi.with_name(epi.name[:-7] + ".json")
        sidecar.write_text(
            json.dumps(
                {
                    "PhaseEncodingDirection": pe,
                    "TotalReadoutTime": 0.03,
                    "B0FieldIdentifier": "pepolar01",
                }
            )
        )
        fieldmaps.append(epi)
    bold.with_name(bold.name[:-7] + ".json").write_text(
        json.dumps(
            {
                "TaskName": "demo",
                "RepetitionTime": 1.2,
                "PhaseEncodingDirection": "j-",
                "TotalReadoutTime": 0.03,
                "B0FieldSource": "pepolar01",
                "NORDICNoiseFile": noise.relative_to(root).as_posix(),
            }
        )
    )
    return root


@pytest.fixture
def synthetic_bids_multi_run(tmp_path: Path) -> Path:
    """Three BOLD runs sharing one AP/PA fieldmap pair."""
    import nibabel as nb
    import numpy as np

    root = tmp_path / "bids-multi"
    func = root / "sub-001" / "ses-01" / "func"
    fmap = root / "sub-001" / "ses-01" / "fmap"
    func.mkdir(parents=True)
    fmap.mkdir(parents=True)
    (root / "dataset_description.json").write_text(
        json.dumps({"Name": "test", "BIDSVersion": "1.10.1", "DatasetType": "raw"})
    )
    affine = np.diag([0.8, 0.8, 0.8, 1.0])
    rng = np.random.default_rng(314)

    for label, pe in (("AP", "j-"), ("PA", "j")):
        epi = fmap / f"sub-001_ses-01_acq-bold_dir-{label}_epi.nii.gz"
        nb.Nifti1Image(
            rng.normal(100, 5, size=(8, 9, 6, 1)).astype("float32"), affine
        ).to_filename(epi)
        epi.with_name(epi.name[:-7] + ".json").write_text(
            json.dumps(
                {
                    "PhaseEncodingDirection": pe,
                    "TotalReadoutTime": 0.03,
                    "B0FieldIdentifier": "pepolar_session01",
                }
            )
        )

    for run in range(1, 4):
        bold = func / f"sub-001_ses-01_task-demo_run-{run:02d}_bold.nii.gz"
        noise = (
            func
            / f"sub-001_ses-01_task-demo_acq-demo_run-{run:02d}_mod-bold_noRF.nii.gz"
        )
        nb.Nifti1Image(
            rng.normal(100 + run, 5, size=(8, 9, 6, 7)).astype("float32"), affine
        ).to_filename(bold)
        nb.Nifti1Image(
            rng.normal(size=(8, 9, 6, 2)).astype("float32"), affine
        ).to_filename(noise)
        noise.with_name(noise.name[:-7] + ".json").write_text(
            json.dumps(
                {
                    "TaskName": "demo",
                    "RepetitionTime": 1.2,
                    "PhaseEncodingDirection": "j-",
                    "TotalReadoutTime": 0.03,
                }
            )
        )
        bold.with_name(bold.name[:-7] + ".json").write_text(
            json.dumps(
                {
                    "TaskName": "demo",
                    "RepetitionTime": 1.2,
                    "PhaseEncodingDirection": "j-",
                    "TotalReadoutTime": 0.03,
                    "B0FieldSource": "pepolar_session01",
                    "NORDICNoiseFile": noise.relative_to(root).as_posix(),
                }
            )
        )
    return root
