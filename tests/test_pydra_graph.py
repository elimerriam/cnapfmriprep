from pathlib import Path

from seventprep.config import load_config
from seventprep.pydra_workflows import build_run_workflow, build_session_workflow


def test_graph_builds(tmp_path: Path) -> None:
    config = load_config(Path(__file__).parents[1] / "config" / "example_study.yaml")
    workflow = build_run_workflow(
        name="test_graph",
        cache_dir=tmp_path / "cache",
        raw_bold="/tmp/raw_bold.nii.gz",
        raw_bold_json="/tmp/raw_bold.json",
        no_rf_file="/tmp/no_rf.nii.gz",
        fmap_files=["/tmp/ap.nii.gz", "/tmp/pa.nii.gz"],
        fmap_jsons=["/tmp/ap.json", "/tmp/pa.json"],
        run_work_dir=tmp_path / "work",
        resolved_config=config.model_dump(mode="json"),
    )
    assert workflow.nordic.name == "nordic"
    assert workflow.topup.name == "topup"
    assert workflow.field.name == "field"
    assert workflow.motion.name == "motion"
    assert workflow.qc.name == "qc"


def test_session_graph_shares_topup_and_reference(tmp_path: Path) -> None:
    config = load_config(Path(__file__).parents[1] / "config" / "example_study.yaml")
    runs = []
    for index in range(3):
        runs.append(
            {
                "raw_bold": f"/tmp/run-{index + 1:02d}_bold.nii.gz",
                "raw_bold_json": f"/tmp/run-{index + 1:02d}_bold.json",
                "no_rf_file": f"/tmp/run-{index + 1:02d}_noRF.nii.gz",
                "fmap_files": ["/tmp/ap.nii.gz", "/tmp/pa.nii.gz"],
                "fmap_jsons": ["/tmp/ap.json", "/tmp/pa.json"],
            }
        )
    workflow, plan = build_session_workflow(
        name="test_session_graph",
        cache_dir=tmp_path / "cache",
        runs=runs,
        session_work_dir=tmp_path / "work",
        resolved_config=config.model_dump(mode="json"),
        reference_index=0,
        shared_topup=True,
        shared_motion_reference=True,
    )
    assert plan["run_count"] == 3
    assert plan["reference_index"] == 0
    assert workflow.topup_shared.name == "topup_shared"
    assert not hasattr(workflow, "topup_001")
    assert workflow.motion_000.name == "motion_000"
    assert workflow.motion_001.name == "motion_001"
    assert workflow.motion_002.name == "motion_002"
