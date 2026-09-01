from pathlib import Path
from types import SimpleNamespace

import cloudpickle
import pytest

from seventprep.cache import recover_interrupted_pydra_cache
from seventprep.errors import ValidationError


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

