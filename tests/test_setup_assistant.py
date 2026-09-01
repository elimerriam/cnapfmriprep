from pathlib import Path

import pytest

from seventprep.config import load_config
from seventprep.errors import ValidationError
from seventprep.setup_assistant import build_generated_config, suggest_role, write_generated_config


def _row(uid: str, number: int, description: str, files: int) -> dict[str, str | int]:
    return {
        "SeriesInstanceUID": uid,
        "SeriesNumber": str(number),
        "AcquisitionTime": f"120{number:03d}.000000",
        "SeriesDescription": description,
        "ProtocolName": description,
        "SequenceName": "epfid",
        "ImageType": "ORIGINAL\\PRIMARY\\M\\ND",
        "NumberOfFiles": files,
    }


def _payload(rows: list[dict[str, str | int]]) -> dict:
    return {
        "dataset_name": "Variable session",
        "task": "retinotopy",
        "acquisition": "hires7T",
        "b0_identifier": "pepolar_session01",
        "bold_phase_encoding_direction": "j-",
        "ap_phase_encoding_direction": "j-",
        "pa_phase_encoding_direction": "j",
        "roles": {
            str(rows[0]["SeriesInstanceUID"]): "fmap_ap",
            str(rows[1]["SeriesInstanceUID"]): "fmap_pa",
            str(rows[2]["SeriesInstanceUID"]): "bold",
            str(rows[3]["SeriesInstanceUID"]): "bold",
            str(rows[4]["SeriesInstanceUID"]): "bold",
        },
        "reference_uid": str(rows[3]["SeriesInstanceUID"]),
        "confirmed": True,
    }


def test_role_suggestions_use_name_and_volume_count() -> None:
    assert suggest_role(_row("1", 1, "ret_run01", 420)) == "bold"
    assert suggest_role(_row("2", 2, "ret_blipRev_PA", 15)) == "fmap_pa"
    assert suggest_role(_row("3", 3, "MPRAGE_T1w", 176)) == "anat"


def test_generated_config_handles_variable_run_names_count_and_reference(
    tmp_path: Path,
) -> None:
    rows = [
        _row("1.2.3.10", 10, "normal reference", 15),
        _row("1.2.3.11", 11, "reverse reference", 15),
        _row("1.2.3.22", 22, "left wedge", 400),
        _row("1.2.3.35", 35, "right wedge renamed", 410),
        _row("1.2.3.49", 49, "eccentricity", 390),
    ]
    root = Path(__file__).parents[1]
    output = tmp_path / "my_study.yaml"
    write_generated_config(
        root / "config" / "example_study.yaml",
        output,
        rows,
        _payload(rows),
    )
    config = load_config(output)

    bold = config.ingest.series_rules[0]
    assert bold.run == "auto"
    assert bold.expected_matches == 3
    assert bold.match["SeriesInstanceUID"].startswith("^(?:")
    assert config.multi_run.reference_run == 2
    assert config.multi_run.reference_task == "retinotopy"
    assert config.ingest.series_rules[1].phase_encoding_direction == "j-"
    assert config.ingest.series_rules[2].phase_encoding_direction == "j"


def test_generation_requires_review_confirmation() -> None:
    rows = [
        _row("1", 1, "AP", 10),
        _row("2", 2, "PA", 10),
        _row("3", 3, "BOLD", 100),
        _row("4", 4, "BOLD", 100),
        _row("5", 5, "BOLD", 100),
    ]
    payload = _payload(rows)
    payload["confirmed"] = False
    with pytest.raises(ValidationError, match="confirm"):
        build_generated_config({}, rows, payload)

