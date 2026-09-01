"""Safe extraction of XNAT ZIP and tar archives."""

from __future__ import annotations

import hashlib
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from .errors import ValidationError


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Return the SHA-256 digest of a file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_name(name: str) -> Path:
    """Convert an archive member name into a safe relative filesystem path."""
    if not name or "\x00" in name:
        raise ValidationError(f"Unsafe archive path: {name!r}")

    # ZIP files normally use POSIX separators, but replacing backslashes also
    # protects against archives created by tools that stored Windows-style paths.
    normalized = name.replace("\\", "/")
    posix = PurePosixPath(normalized)
    if posix.is_absolute() or any(part == ".." for part in posix.parts):
        raise ValidationError(f"Unsafe archive path: {name!r}")

    parts = tuple(part for part in posix.parts if part not in {"", "."})
    # Some tar writers emit a harmless root-directory member named ".".
    if not parts:
        return Path(".")
    if len(parts[0]) == 2 and parts[0][1] == ":":
        raise ValidationError(f"Unsafe archive path contains a drive prefix: {name!r}")
    return Path(*parts)


def _safe_target(destination: Path, member_name: str) -> tuple[Path, Path]:
    relative = _safe_relative_name(member_name)
    target = (destination / relative).resolve()
    try:
        target.relative_to(destination)
    except ValueError as error:
        raise ValidationError(f"Unsafe archive path: {member_name!r}") from error
    return relative, target


def _copy_member(source: BinaryIO, target: Path) -> None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
    except OSError as error:
        raise ValidationError(f"Could not extract archive member to {target}: {error}") from error


def _register_member(
    seen: dict[Path, str],
    relative: Path,
    member_type: str,
    original_name: str,
) -> None:
    previous = seen.get(relative)
    if previous is None:
        seen[relative] = member_type
        return
    if previous == member_type == "directory":
        return
    raise ValidationError(f"Duplicate or conflicting archive member: {original_name!r}")


def _extract_zip(source: Path, destination: Path) -> tuple[int, int]:
    members = 0
    total_bytes = 0
    seen: dict[Path, str] = {}

    try:
        archive = zipfile.ZipFile(source, mode="r")
    except (zipfile.BadZipFile, OSError) as error:
        raise ValidationError(f"Could not open ZIP archive {source}: {error}") from error

    with archive:
        for member in archive.infolist():
            if member.is_dir() and member.filename.replace('\\', '/').rstrip('/') == '.':
                continue
            relative, target = _safe_target(destination, member.filename)
            unix_mode = (member.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(unix_mode)

            if member.flag_bits & 0x1:
                raise ValidationError(
                    f"Encrypted ZIP members are not supported: {member.filename!r}"
                )
            if file_type == stat.S_IFLNK:
                raise ValidationError(f"Archive links are not allowed: {member.filename!r}")
            if file_type in {stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO, stat.S_IFSOCK}:
                raise ValidationError(
                    f"Special archive member is not allowed: {member.filename!r}"
                )

            is_directory = member.is_dir() or file_type == stat.S_IFDIR
            if is_directory:
                _register_member(seen, relative, "directory", member.filename)
                try:
                    target.mkdir(parents=True, exist_ok=True)
                except OSError as error:
                    raise ValidationError(
                        f"Could not create archive directory {target}: {error}"
                    ) from error
                continue

            # ZIPs created on DOS/Windows commonly have no Unix file-type bits.
            # Accept those as regular files, but reject any explicit unknown type.
            if file_type not in {0, stat.S_IFREG}:
                raise ValidationError(f"Unsupported archive member: {member.filename!r}")

            _register_member(seen, relative, "file", member.filename)
            try:
                with archive.open(member, mode="r") as extracted:
                    _copy_member(extracted, target)
            except (RuntimeError, OSError, zipfile.BadZipFile) as error:
                raise ValidationError(
                    f"Could not read ZIP member {member.filename!r}: {error}"
                ) from error
            members += 1
            total_bytes += int(member.file_size)

    return members, total_bytes


def _extract_tar(source: Path, destination: Path) -> tuple[int, int]:
    members = 0
    total_bytes = 0
    seen: dict[Path, str] = {}

    try:
        archive = tarfile.open(source, mode="r:*")
    except (tarfile.TarError, OSError) as error:
        raise ValidationError(f"Could not open tar archive {source}: {error}") from error

    with archive:
        for member in archive:
            if member.isdir() and member.name.replace('\\', '/').rstrip('/') == '.':
                continue
            relative, target = _safe_target(destination, member.name)
            if member.issym() or member.islnk():
                raise ValidationError(f"Archive links are not allowed: {member.name!r}")
            if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
                raise ValidationError(f"Special archive member is not allowed: {member.name!r}")
            if member.isdir():
                _register_member(seen, relative, "directory", member.name)
                try:
                    target.mkdir(parents=True, exist_ok=True)
                except OSError as error:
                    raise ValidationError(
                        f"Could not create archive directory {target}: {error}"
                    ) from error
                continue
            if not member.isfile():
                raise ValidationError(f"Unsupported archive member: {member.name!r}")

            _register_member(seen, relative, "file", member.name)
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValidationError(f"Could not read archive member: {member.name!r}")
            with extracted:
                _copy_member(extracted, target)
            members += 1
            total_bytes += int(member.size)

    return members, total_bytes


def safe_extract_archive(archive: str | Path, output_dir: str | Path) -> dict[str, Any]:
    """Safely extract an XNAT ZIP or tar archive.

    Archive type is detected from file contents rather than solely from the
    filename extension. Only regular files and directories are extracted;
    links, special files, encrypted ZIP members, path traversal, and conflicting
    duplicate members are rejected.
    """
    source = Path(archive).expanduser().resolve()
    if not source.is_file():
        raise ValidationError(f"Archive does not exist: {source}")
    destination = Path(output_dir).expanduser().resolve()
    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ValidationError(
            f"Could not create archive output directory {destination}: {error}"
        ) from error

    if zipfile.is_zipfile(source):
        archive_format = "zip"
        members, total_bytes = _extract_zip(source, destination)
    elif tarfile.is_tarfile(source):
        archive_format = "tar"
        members, total_bytes = _extract_tar(source, destination)
    else:
        raise ValidationError(
            f"Unsupported archive format for {source}. Expected an XNAT .zip, .tgz, "
            ".tar.gz, or other readable tar archive."
        )

    if members == 0:
        raise ValidationError(f"Archive contains no regular files: {source}")
    return {
        "archive": str(source),
        "archive_format": archive_format,
        "sha256": sha256_file(source),
        "output_dir": str(destination),
        "members": members,
        "bytes": total_bytes,
    }


def safe_extract_tgz(archive: str | Path, output_dir: str | Path) -> dict[str, Any]:
    """Backward-compatible alias for :func:`safe_extract_archive`.

    New code should call ``safe_extract_archive``. The alias now also accepts
    ZIP input so older callers do not fail solely because XNAT returned a ZIP.
    """
    return safe_extract_archive(archive, output_dir)
