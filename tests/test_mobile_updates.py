from __future__ import annotations

import asyncio
import hashlib
import json
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from unittest.mock import patch

from rental_manager import main
from rental_manager.services import mobile_updates


def binary_manifest(
    version_code: int = 9,
    version_name: str = "0.1.8",
    package_name: str = "ru.rentalmanager.mobile",
    utf8: bool = True,
) -> bytes:
    strings = [
        mobile_updates.ANDROID_NAMESPACE, "manifest", "package", package_name, "versionCode",
        "versionName", version_name, "uses-sdk", "minSdkVersion", "a" * 140,
    ]
    offsets: list[int] = []
    encoded = bytearray()

    def length(value: int) -> bytes:
        if utf8:
            return bytes([value]) if value < 128 else bytes([(value >> 8) | 0x80, value & 0xFF])
        return struct.pack("<H", value)

    for string in strings:
        offsets.append(len(encoded))
        raw = string.encode("utf-8" if utf8 else "utf-16le")
        encoded.extend(length(len(string)))
        if utf8:
            encoded.extend(length(len(raw)))
        encoded.extend(raw + (b"\0" if utf8 else b"\0\0"))
    encoded.extend(b"\0" * (-len(encoded) % 4))
    pool_size = 28 + len(offsets) * 4 + len(encoded)
    pool = struct.pack("<HHI5I", 1, 28, pool_size, len(offsets), 0, 0x100 if utf8 else 0, 28 + len(offsets) * 4, 0)
    pool += b"".join(struct.pack("<I", offset) for offset in offsets) + encoded

    def attribute(namespace: int, name: int, value_type: int, value: int) -> bytes:
        return struct.pack("<IIIHBBI", namespace, name, 0xFFFFFFFF, 8, 0, value_type, value)

    def element(name: int, attributes: list[bytes]) -> bytes:
        return (
            struct.pack("<HHIII", 0x0102, 16, 36 + 20 * len(attributes), 1, 0xFFFFFFFF)
            + struct.pack("<II6H", 0xFFFFFFFF, name, 20, 20, len(attributes), 0, 0, 0)
            + b"".join(attributes)
        )

    body = pool + element(1, [
        attribute(0xFFFFFFFF, 2, 3, 3), attribute(0, 4, 0x10, version_code), attribute(0, 5, 3, 6),
    ]) + element(7, [attribute(0, 8, 0x10, 23)])
    return struct.pack("<HHI", 3, 8, len(body) + 8) + body


class MobileUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "release.apk"

    def tearDown(self) -> None:
        mobile_updates._read_release.cache_clear()
        self.temp.cleanup()

    def get(self, url: str) -> SimpleNamespace:
        async def request() -> SimpleNamespace:
            parts = urlsplit(url)
            messages: list[dict] = []
            sent_request = False

            async def receive() -> dict:
                nonlocal sent_request
                if not sent_request:
                    sent_request = True
                    return {"type": "http.request", "body": b"", "more_body": False}
                await asyncio.Event().wait()
                return {"type": "http.disconnect"}

            async def send(message: dict) -> None:
                messages.append(message)

            await main.app({
                "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                "method": "GET", "path": parts.path, "raw_path": parts.path.encode(), "root_path": "",
                "query_string": parts.query.encode(), "headers": [], "http_version": "1.1",
                "scheme": "http", "server": ("test", 80), "client": ("127.0.0.1", 1234),
            }, receive, send)
            start = next(message for message in messages if message["type"] == "http.response.start")
            content = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
            return SimpleNamespace(
                status_code=start["status"], headers={key.decode(): value.decode() for key, value in start["headers"]},
                content=content, json=lambda: json.loads(content),
            )

        return asyncio.run(request())

    def write_apk(self, **kwargs) -> bytes:
        with zipfile.ZipFile(self.path, "w") as archive:
            archive.writestr("AndroidManifest.xml", binary_manifest(**kwargs))
            archive.writestr("classes.dex", b"synthetic test fixture")
        return self.path.read_bytes()

    def test_metadata_describes_served_apk_not_newer_source_manifest(self) -> None:
        artifact = self.write_apk()
        (self.path.parent / "AndroidManifest.xml").write_text('<manifest versionCode="999"/>')
        metadata = mobile_updates.mobile_update_metadata(self.path)
        digest = hashlib.sha256(artifact).hexdigest()
        self.assertEqual(metadata, {
            "available": True, "version_code": 9, "version_name": "0.1.8",
            "package_name": "ru.rentalmanager.mobile", "min_sdk": 23,
            "download_url": f"/mobile-app.apk?sha256={digest}", "sha256": digest, "size_bytes": len(artifact),
        })

    def test_utf16_manifest_and_non_ascii_version_name(self) -> None:
        self.write_apk(utf8=False, version_name="0.1.8-тест")
        self.assertEqual(mobile_updates.mobile_update_metadata(self.path)["version_name"], "0.1.8-тест")

    def test_missing_artifact_is_unavailable(self) -> None:
        self.assertEqual(mobile_updates.mobile_update_metadata(self.path), {"available": False})

    def test_invalid_and_truncated_artifacts_are_unavailable(self) -> None:
        for content in [b"not an apk", b"PK\x03\x04"]:
            with self.subTest(content=content):
                self.path.write_bytes(content)
                self.assertEqual(mobile_updates.mobile_update_metadata(self.path), {"available": False})

    def test_missing_and_malformed_manifests_are_unavailable(self) -> None:
        for name, content in [
            ("other.txt", b"absent manifest"), ("AndroidManifest.xml", b"bad manifest"),
            ("AndroidManifest.xml", binary_manifest()[:-1]),
        ]:
            with self.subTest(name=name, size=len(content)):
                with zipfile.ZipFile(self.path, "w") as archive:
                    archive.writestr(name, content)
                self.assertEqual(mobile_updates.mobile_update_metadata(self.path), {"available": False})

    def test_other_package_and_invalid_versions_are_unavailable(self) -> None:
        for kwargs in [{"package_name": "com.other.app"}, {"version_code": 0}, {"version_name": ""}]:
            with self.subTest(kwargs=kwargs):
                self.write_apk(**kwargs)
                self.assertEqual(mobile_updates.mobile_update_metadata(self.path), {"available": False})

    def test_cache_refreshes_when_artifact_is_replaced(self) -> None:
        self.write_apk()
        first = mobile_updates.mobile_update_metadata(self.path)
        self.write_apk(version_code=10, version_name="0.1.9")
        second = mobile_updates.mobile_update_metadata(self.path)
        self.assertEqual(second["version_code"], 10)
        self.assertNotEqual(first["sha256"], second["sha256"])
        self.path.unlink()
        self.assertEqual(mobile_updates.mobile_update_metadata(self.path), {"available": False})

    def test_cached_metadata_cannot_be_mutated_by_caller(self) -> None:
        self.write_apk()
        metadata = mobile_updates.mobile_update_metadata(self.path)
        metadata["version_code"] = 999
        self.assertEqual(mobile_updates.mobile_update_metadata(self.path)["version_code"], 9)

    def test_public_metadata_and_apk_download_require_no_session(self) -> None:
        artifact = self.write_apk()
        with patch.object(main, "MOBILE_APK_PATH", self.path), patch.object(main, "SessionLocal") as sessions:
            response = self.get("/api/mobile-update")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            metadata = response.json()
            download = self.get(metadata["download_url"])
            self.assertEqual(download.status_code, 200)
            self.assertEqual(download.content, artifact)
            self.assertEqual(download.headers["cache-control"], "no-store")
            sessions.assert_not_called()

    def test_missing_apk_endpoint_is_404_and_metadata_is_unavailable(self) -> None:
        with patch.object(main, "MOBILE_APK_PATH", self.path):
            self.assertEqual(self.get("/api/mobile-update").json(), {"available": False})
            self.assertEqual(self.get("/mobile-app.apk").status_code, 404)

    def test_old_download_url_is_rejected_after_release_changes(self) -> None:
        self.write_apk()
        old_release = mobile_updates.mobile_update_metadata(self.path)
        self.write_apk(version_code=10, version_name="0.1.9")
        with patch.object(main, "MOBILE_APK_PATH", self.path):
            response = self.get(old_release["download_url"])
            self.assertEqual(response.status_code, 409)
            self.assertEqual(self.get("/api/mobile-update").json()["version_code"], 10)

    def test_update_path_does_not_make_other_api_paths_public(self) -> None:
        with patch.object(main, "SessionLocal"), patch.object(main, "find_session", return_value=None):
            self.assertEqual(self.get("/api/mobile-update/private").status_code, 401)
            self.assertEqual(self.get("/api/settings").status_code, 401)


if __name__ == "__main__":
    unittest.main()
