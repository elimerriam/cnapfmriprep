import pytest
from pydra import Submitter, Workflow, mark


@mark.task
def _dict_task(value: int) -> dict:
    return {"value": value + 1}


def test_concurrent_futures_submitter_executes_dict_output(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYDRA_HASH_CACHE", str(tmp_path / "hash-cache"))
    workflow = Workflow(
        name="pydra_smoke",
        cache_dir=str(tmp_path / "cache"),
        input_spec=["cnapfmriprep_context"],
        cnapfmriprep_context="pydra_smoke",
    )
    workflow.add(_dict_task(name="increment", value=4))
    workflow.set_output([("result", workflow.increment.lzout.out)])
    try:
        with Submitter(plugin="cf", n_procs=2) as submitter:
            submitter(workflow)
    except PermissionError as error:
        if "Operation not permitted" in str(error):
            pytest.skip("ProcessPoolExecutor is unavailable in this sandbox")
        raise
    result = workflow.result()
    assert result.output.result == {"value": 5}
