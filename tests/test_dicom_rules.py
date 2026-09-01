import pytest

from cnapfmriprep.config import SeriesRule
from cnapfmriprep.dicom import match_series
from cnapfmriprep.errors import ValidationError


def _row(uid: str, number: int, description: str) -> dict:
    return {
        "SeriesInstanceUID": uid,
        "SeriesNumber": str(number),
        "AcquisitionTime": f"120{number:03d}.000000",
        "SeriesDescription": description,
        "ProtocolName": description,
        "SequenceName": "epfid",
        "ImageType": "ORIGINAL\\PRIMARY\\M\\ND",
        "NumberOfFiles": 10,
        "Files": [f"/{uid}.dcm"],
    }


def test_exact_rule_match() -> None:
    rows = [_row("1", 1, "BOLD_MAG"), _row("2", 2, "TOPUP_AP")]
    rule = SeriesRule(
        name="bold",
        kind="bold_with_norf",
        match={"SeriesDescription": "^BOLD_MAG$", "ImageType": "M"},
        task="demo",
        run=1,
        b0_identifier="b0",
    )
    plan = match_series(rows, [rule])
    assert plan[0]["series"]["SeriesInstanceUID"] == "1"
    assert plan[0]["assigned_run"] == 1


def test_ambiguous_rule_fails_without_auto_run() -> None:
    rows = [_row("1", 1, "BOLD_A"), _row("2", 2, "BOLD_B")]
    rule = SeriesRule(
        name="bold",
        kind="bold_with_norf",
        match={"SeriesDescription": "BOLD"},
        task="demo",
        b0_identifier="b0",
    )
    with pytest.raises(ValidationError, match="expected 1 match"):
        match_series(rows, [rule])


def test_auto_run_assigns_all_matches_in_series_order() -> None:
    rows = [
        _row("30", 30, "RETINO_NORDIC"),
        _row("10", 10, "RETINO_NORDIC"),
        _row("20", 20, "RETINO_NORDIC"),
    ]
    rule = SeriesRule(
        name="retino_runs",
        kind="bold_with_norf",
        match={"SeriesDescription": "(?i)retino"},
        task="retinotopy",
        run="auto",
        run_start=1,
        run_sort_by="SeriesNumber",
        b0_identifier="pepolar_session",
        expected_matches="one_or_more",
    )
    plan = match_series(rows, [rule])
    assert [item["series"]["SeriesNumber"] for item in plan] == ["10", "20", "30"]
    assert [item["assigned_run"] for item in plan] == [1, 2, 3]


def test_multiple_bold_matches_require_auto_run() -> None:
    with pytest.raises(ValueError, match="must use run: auto"):
        SeriesRule(
            name="retino_runs",
            kind="bold_with_norf",
            match={"SeriesDescription": "RETINO"},
            task="retinotopy",
            b0_identifier="pepolar_session",
            expected_matches=10,
        )


def test_overlapping_auto_rules_cannot_assign_same_bids_run() -> None:
    rows = [_row("1", 10, "RETINO_A"), _row("2", 20, "RETINO_B")]
    first = SeriesRule(
        name="first",
        kind="bold_with_norf",
        match={"SeriesDescription": "RETINO_A"},
        task="retinotopy",
        acquisition="hires",
        run="auto",
        run_start=1,
        b0_identifier="pepolar_session",
    )
    second = SeriesRule(
        name="second",
        kind="bold_with_norf",
        match={"SeriesDescription": "RETINO_B"},
        task="retinotopy",
        acquisition="hires",
        run="auto",
        run_start=1,
        b0_identifier="pepolar_session",
    )
    with pytest.raises(ValidationError, match="both assign BIDS"):
        match_series(rows, [first, second])
