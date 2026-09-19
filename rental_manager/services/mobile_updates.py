from __future__ import annotations

import hashlib
import os
import struct
import zipfile
import zlib
from functools import lru_cache
from pathlib import Path
from typing import Any


ANDROID_NAMESPACE = "http://schemas.android.com/apk/res/android"
MOBILE_PACKAGE = "ru.rentalmanager.mobile"
NO_STRING = 0xFFFFFFFF


def _string_pool(chunk: bytes, header_size: int) -> list[str]:
    count, style_count, flags, start, style_start = struct.unpack_from("<5I", chunk, 8)
    if header_size < 28 or header_size + (count + style_count) * 4 > start or start > len(chunk):
        raise ValueError("Invalid Android string pool")
    end = style_start or len(chunk)
    if end < start or end > len(chunk):
        raise ValueError("Invalid Android string data")
    strings: list[str] = []
    utf8 = bool(flags & 0x100)

    def length_at(offset: int) -> tuple[int, int]:
        if utf8:
            value = chunk[offset]
            if value & 0x80:
                return ((value & 0x7F) << 8) | chunk[offset + 1], offset + 2
            return value, offset + 1
        value = struct.unpack_from("<H", chunk, offset)[0]
        if value & 0x8000:
            return ((value & 0x7FFF) << 16) | struct.unpack_from("<H", chunk, offset + 2)[0], offset + 4
        return value, offset + 2

    for index in range(count):
        offset = start + struct.unpack_from("<I", chunk, header_size + index * 4)[0]
        if not start <= offset < end:
            raise ValueError("Invalid Android string offset")
        length, offset = length_at(offset)
        if utf8:
            length, offset = length_at(offset)
        byte_length = length if utf8 else length * 2
        terminator_size = 1 if utf8 else 2
        if offset + byte_length + terminator_size > end:
            raise ValueError("Truncated Android string")
        if chunk[offset + byte_length : offset + byte_length + terminator_size] != b"\0" * terminator_size:
            raise ValueError("Unterminated Android string")
        strings.append(chunk[offset : offset + byte_length].decode("utf-8" if utf8 else "utf-16le"))
    return strings


def _manifest_metadata(data: bytes) -> dict[str, Any]:
    document_type, header_size, document_size = struct.unpack_from("<HHI", data)
    if document_type != 0x0003 or header_size < 8 or document_size != len(data):
        raise ValueError("Invalid binary Android manifest")
    strings: list[str] = []
    manifest: dict[tuple[str, str], str | int] = {}
    sdk: dict[tuple[str, str], str | int] = {}
    offset = header_size
    while offset < document_size:
        chunk_type, chunk_header, chunk_size = struct.unpack_from("<HHI", data, offset)
        if chunk_header < 8 or chunk_size < chunk_header or offset + chunk_size > document_size:
            raise ValueError("Invalid Android XML chunk")
        chunk = data[offset : offset + chunk_size]
        if chunk_type == 0x0001:
            strings = _string_pool(chunk, chunk_header)
        elif chunk_type == 0x0102:
            _, name_index, attribute_start, attribute_size, count = struct.unpack_from("<IIHHH", chunk, chunk_header)
            if attribute_start < 20 or attribute_size < 20:
                raise ValueError("Invalid Android XML attributes")
            name = strings[name_index]
            attributes: dict[tuple[str, str], str | int] = {}
            for index in range(count):
                attribute_offset = chunk_header + attribute_start + index * attribute_size
                if attribute_offset + attribute_size > chunk_size:
                    raise ValueError("Truncated Android XML attributes")
                namespace_index, key_index, _, value_size, _, value_type, value = struct.unpack_from(
                    "<IIIHBBI", chunk, attribute_offset
                )
                if value_size != 8:
                    raise ValueError("Invalid Android XML value")
                namespace = "" if namespace_index == NO_STRING else strings[namespace_index]
                if value_type == 0x03:
                    attributes[(namespace, strings[key_index])] = strings[value]
                elif value_type in {0x10, 0x11}:
                    attributes[(namespace, strings[key_index])] = value
            if name == "manifest":
                manifest = attributes
            elif name == "uses-sdk":
                sdk = attributes
        offset += chunk_size
    version_code = manifest.get((ANDROID_NAMESPACE, "versionCode"))
    version_name = manifest.get((ANDROID_NAMESPACE, "versionName"))
    package_name = manifest.get(("", "package"))
    min_sdk = sdk.get((ANDROID_NAMESPACE, "minSdkVersion"), 1)
    if (
        not isinstance(version_code, int)
        or not 0 < version_code <= 2_100_000_000
        or not isinstance(version_name, str)
        or not version_name.strip()
        or len(version_name) > 80
        or package_name != MOBILE_PACKAGE
        or not isinstance(min_sdk, int)
        or not 1 <= min_sdk <= 10_000
        or manifest.get((ANDROID_NAMESPACE, "versionCodeMajor"), 0) != 0
    ):
        raise ValueError("Unsupported mobile release metadata")
    return {
        "version_code": version_code,
        "version_name": version_name,
        "package_name": package_name,
        "min_sdk": min_sdk,
    }


def _identity(stat: os.stat_result) -> tuple[int, int, int, int, int]:
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@lru_cache(maxsize=4)
def _read_release(path: Path, identity: tuple[int, int, int, int, int]) -> dict[str, Any]:
    with path.open("rb") as artifact:
        opened_identity = _identity(os.fstat(artifact.fileno()))
        if opened_identity[:4] != identity[:4]:
            raise ValueError("Mobile release changed while opening")
        with zipfile.ZipFile(artifact) as archive:
            manifest = archive.getinfo("AndroidManifest.xml")
            if manifest.file_size > 1024 * 1024:
                raise ValueError("Android manifest is too large")
            metadata = _manifest_metadata(archive.read(manifest))
        artifact.seek(0)
        digest = hashlib.file_digest(artifact, "sha256").hexdigest()
        if _identity(os.fstat(artifact.fileno())) != opened_identity:
            raise ValueError("Mobile release changed while reading")
    return {
        "available": True,
        **metadata,
        "download_url": f"/mobile-app.apk?sha256={digest}",
        "sha256": digest,
        "size_bytes": identity[2],
    }


def mobile_update_metadata(path: Path) -> dict[str, Any]:
    """Read version and checksum from the artifact that the download route serves."""
    try:
        identity = _identity(path.stat())
        metadata = _read_release(path, identity)
        if _identity(path.stat()) != identity:
            return {"available": False}
        return dict(metadata)
    except (OSError, ValueError, KeyError, IndexError, struct.error, zipfile.BadZipFile, zlib.error, RuntimeError):
        return {"available": False}
