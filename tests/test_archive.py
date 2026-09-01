from __future__ import annotations

import io
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from cnapfmriprep.archive import safe_extract_archive, safe_extract_tgz
from cnapfmriprep.errors import ValidationError

_MEMBER = "XNAT/scans/1/resources/DICOM/files/a.dcm"


def test_safe_tgz_extracts(tmp_path: Path) -> None:
    archive = tmp_path / "safe.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        payload = b"dicom-ish"
        info = tarfile.TarInfo(_MEMBER)
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    result = safe_extract_archive(archive, tmp_path / "out")
    assert result["archive_format"] == "tar"
    assert result["members"] == 1
    assert (tmp_path / "out" / _MEMBER).read_bytes() == b"dicom-ish"


def test_safe_zip_extracts(tmp_path: Path) -> None:
    archive = tmp_path / "xnat-download.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr(_MEMBER, b"dicom-ish")
    result = safe_extract_archive(archive, tmp_path / "out")
    assert result["archive_format"] == "zip"
    assert result["members"] == 1
    assert result["bytes"] == len(b"dicom-ish")
    assert (tmp_path / "out" / _MEMBER).read_bytes() == b"dicom-ish"


def test_zip_with_explicit_directory_entries_extracts(tmp_path: Path) -> None:
    archive = tmp_path / "xnat-directories.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("XNAT/", b"")
        zip_file.writestr("XNAT/scans/", b"")
        zip_file.writestr(_MEMBER, b"dicom-ish")
    result = safe_extract_archive(archive, tmp_path / "out")
    assert result["archive_format"] == "zip"
    assert result["members"] == 1
    assert (tmp_path / "out" / _MEMBER).read_bytes() == b"dicom-ish"


def test_archive_path_traversal_is_rejected_for_tar(tmp_path: Path) -> None:
    archive = tmp_path / "bad.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        payload = b"bad"
        info = tarfile.TarInfo("../../escape")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    with pytest.raises(ValidationError, match="Unsafe archive path"):
        safe_extract_archive(archive, tmp_path / "out")


@pytest.mark.parametrize("member", ["../../escape", "..\\..\\escape", "/absolute/file"])
def test_archive_path_traversal_is_rejected_for_zip(tmp_path: Path, member: str) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr(member, b"bad")
    with pytest.raises(ValidationError, match="Unsafe archive path"):
        safe_extract_archive(archive, tmp_path / "out")


def test_zip_symlink_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "link.zip"
    info = zipfile.ZipInfo("XNAT/link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr(info, "target")
    with pytest.raises(ValidationError, match="links are not allowed"):
        safe_extract_archive(archive, tmp_path / "out")


def test_invalid_archive_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "not-an-archive.zip"
    archive.write_text("not really a zip")
    with pytest.raises(ValidationError, match="Unsupported archive format"):
        safe_extract_archive(archive, tmp_path / "out")


def test_legacy_function_name_accepts_zip(tmp_path: Path) -> None:
    archive = tmp_path / "xnat.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr(_MEMBER, b"content")
    result = safe_extract_tgz(archive, tmp_path / "out")
    assert result["archive_format"] == "zip"


def test_archive_detection_uses_contents_not_extension(tmp_path: Path) -> None:
    archive = tmp_path / "xnat-download.dat"
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr(_MEMBER, b"content")
    result = safe_extract_archive(archive, tmp_path / "out")
    assert result["archive_format"] == "zip"


def test_duplicate_zip_member_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr(_MEMBER, b"first")
        with pytest.warns(UserWarning, match="Duplicate name"):
            zip_file.writestr(_MEMBER, b"second")
    with pytest.raises(ValidationError, match="Duplicate or conflicting"):
        safe_extract_archive(archive, tmp_path / "out")
