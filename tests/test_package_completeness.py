from pathlib import Path


def test_required_source_modules_are_present() -> None:
    package = Path(__file__).parents[1] / "src" / "seventprep"
    required = {
        "archive.py",
        "bids.py",
        "cache.py",
        "cli.py",
        "config.py",
        "derivatives.py",
        "dicom.py",
        "errors.py",
        "ingest.py",
        "motion.py",
        "nordic.py",
        "preprocess.py",
        "progress.py",
        "pydra_workflows.py",
        "qc.py",
        "topup.py",
        "transforms.py",
        "utils.py",
        "execution.py",
        "setup_assistant.py",
    }
    missing = sorted(name for name in required if not (package / name).is_file())
    assert not missing, f"Missing package modules: {missing}"
