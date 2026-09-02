"""Tests for portable, side-effect-free installation diagnostics."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cnapfmriprep import __version__
from cnapfmriprep.diagnostics import collect_diagnostics, render_shell_setup


def _fake_versions(name: str) -> str:
    if name == "cnapfmriprep":
        return __version__
    if name == "pydra":
        return "0.25.0"
    return "1.0"


def _fake_import(name: str) -> SimpleNamespace:
    if name == "pydra":
        return SimpleNamespace(
            __file__="/fake/pydra/__init__.py",
            Submitter=object(),
            Workflow=object(),
            mark=object(),
        )
    return SimpleNamespace(__file__=f"/fake/{name.replace('.', '/')}.py")


def _healthy_tree(tmp_path: Path) -> tuple[dict[str, str], dict[str, str]]:
    fsldir = tmp_path / "fsl"
    config_dir = fsldir / "etc" / "flirtsch"
    config_dir.mkdir(parents=True)
    (config_dir / "b02b0_1.cnf").write_text("# test\n", encoding="utf-8")

    nordic = tmp_path / "NORDIC_Raw"
    nordic.mkdir()
    (nordic / "NIFTI_NORDIC.m").write_text("% test\n", encoding="utf-8")

    environment = {
        "PATH": "/fake/bin",
        "FSLDIR": str(fsldir),
        "FSLOUTPUTTYPE": "NIFTI_GZ",
        "NORDIC_ROOT": str(nordic),
        "CONDA_PREFIX": str(tmp_path / "conda"),
    }
    programs = {
        name: f"/fake/bin/{name}"
        for name in (
            "dcm2niix",
            "topup",
            "antsRegistration",
            "antsApplyTransforms",
            "matlab",
            "bids-validator-deno",
        )
    }
    return environment, programs


def test_render_tcsh_setup_is_copyable_and_side_effect_free(tmp_path: Path) -> None:
    fsldir = tmp_path / "FSL With Spaces"
    commands = render_shell_setup(
        "tcsh",
        fsldir=fsldir,
        conda_prefix="/opt/conda/envs/cnap",
    )

    assert f"setenv FSLDIR '{fsldir}'" in commands
    assert 'source "${FSLDIR}/etc/fslconf/fsl.csh"' in commands
    assert 'setenv PATH "/opt/conda/envs/cnap/bin:${FSLDIR}/bin:${PATH}"' in commands
    assert commands.endswith("rehash")
    assert not fsldir.exists()


def test_render_shell_setup_rejects_unknown_shell() -> None:
    with pytest.raises(ValueError, match="tcsh"):
        render_shell_setup("powershell", fsldir=None, conda_prefix=None)


def test_collect_diagnostics_reports_a_healthy_environment(tmp_path: Path) -> None:
    environment, programs = _healthy_tree(tmp_path)

    def fake_which(name: str, *, path: str | None = None) -> str | None:
        assert path == environment["PATH"]
        return programs.get(name)

    report = collect_diagnostics(
        environment=environment,
        which=fake_which,
        distribution_version=_fake_versions,
        import_module=_fake_import,
    )

    assert report["ok"] is True
    assert report["failures"] == []
    assert report["fsl"]["ok"] is True
    assert report["nordic"]["ok"] is True
    assert report["pydra"]["ok"] is True
    assert report["matlab_license"]["status"] == "not_run"


def test_collect_diagnostics_finds_conda_ants_missing_from_path(tmp_path: Path) -> None:
    environment, programs = _healthy_tree(tmp_path)
    conda_bin = Path(environment["CONDA_PREFIX"]) / "bin"
    conda_bin.mkdir(parents=True)
    for name in ("antsRegistration", "antsApplyTransforms"):
        (conda_bin / name).write_text("#!/bin/sh\n", encoding="utf-8")
        programs.pop(name)

    def fake_which(name: str, *, path: str | None = None) -> str | None:
        assert path == environment["PATH"]
        return programs.get(name)

    report = collect_diagnostics(
        environment=environment,
        which=fake_which,
        distribution_version=_fake_versions,
        import_module=_fake_import,
    )

    assert report["ok"] is False
    assert report["executables"]["antsRegistration"]["conda_candidate"] == str(
        (conda_bin / "antsRegistration").resolve()
    )
    assert any("active Conda bin directory" in item for item in report["recommendations"])


def test_collect_diagnostics_detects_unsourced_fsl_environment(tmp_path: Path) -> None:
    environment, programs = _healthy_tree(tmp_path)
    environment.pop("FSLOUTPUTTYPE")

    def fake_which(name: str, *, path: str | None = None) -> str | None:
        return programs.get(name)

    report = collect_diagnostics(
        environment=environment,
        which=fake_which,
        distribution_version=_fake_versions,
        import_module=_fake_import,
    )

    assert report["fsl"]["ok"] is False
    assert "fsl:FSLOUTPUTTYPE" in report["failures"]
    assert any("Source FSL's shell configuration" in item for item in report["recommendations"])


def test_collect_diagnostics_rejects_incompatible_pydra(tmp_path: Path) -> None:
    environment, programs = _healthy_tree(tmp_path)

    def fake_which(name: str, *, path: str | None = None) -> str | None:
        return programs.get(name)

    def incompatible_version(name: str) -> str:
        return "1.0a9" if name == "pydra" else _fake_versions(name)

    report = collect_diagnostics(
        environment=environment,
        which=fake_which,
        distribution_version=incompatible_version,
        import_module=_fake_import,
    )

    assert report["pydra"]["ok"] is False
    assert "pydra" in report["failures"]
    assert any("pydra==0.25.0" in item for item in report["recommendations"])


def test_matlab_license_probe_is_opt_in_and_bounded(tmp_path: Path) -> None:
    environment, programs = _healthy_tree(tmp_path)
    calls: list[tuple[list[str], int]] = []

    def fake_which(name: str, *, path: str | None = None) -> str | None:
        assert path == environment["PATH"]
        return programs.get(name)

    def fake_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, int(kwargs["timeout"])))
        return subprocess.CompletedProcess(command, 0, "CNAPFMRIPREP_LICENSE_OK\n", "")

    report = collect_diagnostics(
        environment=environment,
        which=fake_which,
        runner=fake_runner,
        check_matlab_license=True,
        distribution_version=_fake_versions,
        import_module=_fake_import,
    )

    assert report["matlab_license"]["status"] == "passed"
    assert calls == [(["/fake/bin/matlab", "-batch", "disp('CNAPFMRIPREP_LICENSE_OK')"], 90)]
