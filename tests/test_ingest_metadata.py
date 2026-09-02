import json
from pathlib import Path

import nibabel as nb
import numpy as np
import pytest

from cnapfmriprep.bids import sidecar_for
from cnapfmriprep.config import SeriesRule, load_config
from cnapfmriprep.errors import ValidationError
from cnapfmriprep.ingest import (
    _apply_phase_encoding_direction,
    _convert_plan_to_bids,
)


def _epi_rule(direction: str | None) -> SeriesRule:
    return SeriesRule(
        name="bold",
        kind="bold_with_norf",
        match={"SeriesDescription": "BOLD"},
        task="demo",
        b0_identifier="pepolar",
        phase_encoding_direction=direction,
    )


def test_phase_encoding_direction_override_fills_missing_metadata() -> None:
    metadata = {"TotalReadoutTime": 0.03}
    _apply_phase_encoding_direction(
        metadata,
        _epi_rule("j-"),
        sidecar=Path("converted.json"),
    )
    assert metadata["PhaseEncodingDirection"] == "j-"


def test_phase_encoding_direction_override_accepts_matching_metadata() -> None:
    metadata = {"PhaseEncodingDirection": "j-"}
    _apply_phase_encoding_direction(
        metadata,
        _epi_rule("j-"),
        sidecar=Path("converted.json"),
    )
    assert metadata["PhaseEncodingDirection"] == "j-"


def test_phase_encoding_direction_override_rejects_conflict() -> None:
    metadata = {"PhaseEncodingDirection": "j"}
    with pytest.raises(ValidationError, match="dcm2niix wrote 'j'"):
        _apply_phase_encoding_direction(
            metadata,
            _epi_rule("j-"),
            sidecar=Path("converted.json"),
        )


def test_bids_conversion_writes_overrides_to_bold_and_fieldmaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    converted: list[tuple[Path, Path]] = []
    for name, volumes in (("bold", 5), ("ap", 1), ("pa", 1)):
        image = tmp_path / f"{name}.nii.gz"
        nb.Nifti1Image(
            np.zeros((3, 4, 80, volumes), dtype="float32"),
            np.eye(4),
        ).to_filename(image)
        metadata = sidecar_for(image)
        metadata.write_text(
            json.dumps(
                {
                    "MRAcquisitionType": "3D",
                    "RepetitionTime": 0.0664,
                    "ParallelReductionFactorOutOfPlane": 2,
                    "EffectiveEchoSpacing": 0.000333333,
                    "TotalReadoutTime": 0.0796667,
                }
            )
        )
        converted.append((image, metadata))

    converted_iter = iter(converted)
    monkeypatch.setattr(
        "cnapfmriprep.ingest._convert_one_series",
        lambda *args, **kwargs: next(converted_iter),
    )

    rules = [
        SeriesRule(
            name="bold",
            kind="bold_with_norf",
            match={"SeriesDescription": "BOLD"},
            task="demo",
            acquisition="hires",
            run=1,
            b0_identifier="pepolar",
            phase_encoding_direction="j-",
        ),
        SeriesRule(
            name="ap",
            kind="fmap_epi",
            match={"SeriesDescription": "AP"},
            acquisition="bold",
            direction="AP",
            b0_identifier="pepolar",
            phase_encoding_direction="j-",
        ),
        SeriesRule(
            name="pa",
            kind="fmap_epi",
            match={"SeriesDescription": "PA"},
            acquisition="bold",
            direction="PA",
            b0_identifier="pepolar",
            phase_encoding_direction="j",
        ),
    ]
    plan = [
        {
            "rule": rule.model_dump(mode="json"),
            "rule_name": rule.name,
            "assigned_run": 1 if rule.kind == "bold_with_norf" else None,
        }
        for rule in rules
    ]
    config = load_config(Path(__file__).parents[1] / "config" / "example_study.yaml")
    staging = tmp_path / "staging"
    _convert_plan_to_bids(
        plan,
        staging,
        tmp_path / "conversion",
        config=config,
        subject="001",
        session="01",
    )

    session = staging / "sub-001" / "ses-01"
    bold_json = session / "func" / "sub-001_ses-01_task-demo_acq-hires_run-01_bold.json"
    noise = (
        session
        / "func"
        / "sub-001_ses-01_task-demo_acq-hires_run-01_mod-bold_noRF.nii.gz"
    )
    ap_json = session / "fmap" / "sub-001_ses-01_acq-bold_dir-AP_epi.json"
    pa_json = session / "fmap" / "sub-001_ses-01_acq-bold_dir-PA_epi.json"
    bold_metadata = json.loads(bold_json.read_text())
    noise_metadata = json.loads(sidecar_for(noise).read_text())
    assert bold_metadata["PhaseEncodingDirection"] == "j-"
    assert bold_metadata["RepetitionTime"] == pytest.approx(2.656)
    assert bold_metadata["NORDICNoiseFile"] == noise.relative_to(staging).as_posix()
    assert noise_metadata["TaskName"] == "demo"
    assert noise_metadata["RepetitionTime"] == pytest.approx(2.656)
    assert nb.load(noise).header.get_zooms()[3] == pytest.approx(2.656)
    assert json.loads(ap_json.read_text())["PhaseEncodingDirection"] == "j-"
    assert json.loads(ap_json.read_text())["RepetitionTime"] == pytest.approx(2.656)
    assert json.loads(pa_json.read_text())["PhaseEncodingDirection"] == "j"
    assert json.loads(pa_json.read_text())["RepetitionTime"] == pytest.approx(2.656)
