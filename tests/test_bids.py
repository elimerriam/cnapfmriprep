from seventprep.bids import discover_bold_runs, semantic_validate


def test_semantic_validation_and_discovery(synthetic_bids) -> None:
    result = semantic_validate(synthetic_bids, subject="001", session="01")
    assert len(result["runs"]) == 1
    assert result["runs"][0]["functional_volumes"] == 7
    runs = discover_bold_runs(synthetic_bids, subject="001", session="01", task="demo", run=1)
    assert len(runs) == 1
    assert len(runs[0]["fieldmaps"]) == 2
    assert runs[0]["noise"].name.endswith("_noRF.nii.gz")
