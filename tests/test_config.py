from pathlib import Path

import pytest

from cnapfmriprep.config import SeriesRule, load_config


def test_example_config_loads() -> None:
    root = Path(__file__).parents[1]
    config = load_config(root / "config" / "example_study.yaml")
    assert config.ingest.trailing_no_rf_volumes == 2
    assert config.nordic.magnitude_only is True
    assert config.nordic.noise_volume_last == 2
    assert config.resampling.jacobian_modulation is False
    assert config.multi_run.shared_topup is True
    assert config.multi_run.shared_motion_reference is True
    assert config.multi_run.reference_run == 1
    assert config.ingest.series_rules[0].run == "auto"
    assert config.ingest.series_rules[0].expected_matches == "one_or_more"
    assert config.ingest.series_rules[0].phase_encoding_direction is None
    assert len(config.ingest.series_rules) == 3
    assert config.execution.profile == "laptop"
    assert config.execution.resolved_profile == "laptop"
    assert config.execution.pydra_plugin == "serial"
    assert config.execution.volume_workers == 1


def test_phase_encoding_direction_override_is_validated() -> None:
    rule = SeriesRule(
        name="bold",
        kind="bold_with_norf",
        match={"SeriesDescription": "BOLD"},
        task="demo",
        b0_identifier="pepolar",
        phase_encoding_direction="j-",
    )
    assert rule.phase_encoding_direction == "j-"

    with pytest.raises(ValueError, match="phase_encoding_direction"):
        SeriesRule(
            name="anat",
            kind="anat",
            match={"SeriesDescription": "T1"},
            phase_encoding_direction="j",
        )
