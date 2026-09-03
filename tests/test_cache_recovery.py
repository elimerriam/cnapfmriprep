from pathlib import Path
from types import SimpleNamespace

import cloudpickle
import pytest

from cnapfmriprep.cache import recover_interrupted_pydra_cache
from cnapfmriprep.errors import ValidationError
from cnapfmriprep.job import WorkDirectoryLease


def _write_result(entry: Path, *, output: object, errored: bool = False) -> None:
    entry.mkdir(parents=True)
    (entry / "_result.pklz").write_bytes(
        cloudpickle.dumps(SimpleNamespace(output=output, errored=errored))
    )


def test_invalid_empty_result_is_quarantined_and_valid_result_is_retained(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "pydra-cache"
    invalid = cache / "FunctionTask_invalid"
    valid = cache / "FunctionTask_valid"
    _write_result(invalid, output=None)
    _write_result(valid, output={"ok": True})

    result = recover_interrupted_pydra_cache(cache)

    assert len(result["recovered"]) == 1
    assert not invalid.exists()
    assert valid.is_dir()
    backup = Path(result["backup_dir"])
    assert (backup / "FunctionTask_invalid" / "_result.pklz").is_file()
    assert (backup / "recovery_report.json").is_file()


def test_active_cache_lock_refuses_recovery(tmp_path: Path) -> None:
    cache = tmp_path / "pydra-cache"
    cache.mkdir()
    (cache / "active.lock").write_text("locked")
    with pytest.raises(ValidationError, match="Do not start a second process"):
        recover_interrupted_pydra_cache(cache)


def test_stale_pid_lock_is_quarantined_without_discarding_valid_cache(tmp_path: Path) -> None:
    cache = tmp_path / "pydra-cache"
    valid = cache / "FunctionTask_valid"
    _write_result(valid, output={"ok": True})
    lock = cache / "FunctionTask_interrupted.lock"
    lock.write_text("999999999\n\nstart-token\n")

    result = recover_interrupted_pydra_cache(cache)

    assert valid.is_dir()
    assert not lock.exists()
    assert len(result["recovered_locks"]) == 1
    assert Path(result["recovered_locks"][0]["backup"]).is_file()


def test_manual_recovery_refuses_active_work_directory(tmp_path: Path) -> None:
    cache = tmp_path / "pydra-cache"
    with WorkDirectoryLease(tmp_path):
        with pytest.raises(ValidationError, match="active or uncertain job"):
            recover_interrupted_pydra_cache(cache)
