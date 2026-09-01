from pathlib import Path

from cnapfmriprep.config import load_config
from cnapfmriprep.execution import (
    apply_execution_profile,
    choose_auto_profile,
    resolve_execution_mapping,
)


def test_auto_profile_is_conservative() -> None:
    assert choose_auto_profile(memory_gb=24, cpu_count=12) == "laptop"
    assert choose_auto_profile(memory_gb=64, cpu_count=12) == "workstation"
    assert choose_auto_profile(memory_gb=256, cpu_count=32) == "server"


def test_yaml_profile_preserves_explicit_overrides() -> None:
    resolved = resolve_execution_mapping(
        {"profile": "workstation", "volume_workers": 1, "show_progress": False}
    )
    assert resolved["pydra_plugin"] == "cf"
    assert resolved["n_procs"] == 2
    assert resolved["volume_workers"] == 1
    assert resolved["show_progress"] is False
    assert resolved["resolved_profile"] == "workstation"


def test_command_line_profile_replaces_resource_settings() -> None:
    root = Path(__file__).parents[1]
    config = load_config(root / "config" / "example_study.yaml")
    overridden = apply_execution_profile(config, "server")
    assert overridden.execution.profile == "server"
    assert overridden.execution.resolved_profile == "server"
    assert overridden.execution.pydra_plugin == "cf"
    assert overridden.execution.n_procs == 4
    assert overridden.execution.volume_workers == 4
    assert overridden.execution.show_progress is True

