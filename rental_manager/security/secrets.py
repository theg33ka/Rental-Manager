from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


ENCRYPTED_PREFIX = "enc:v1:"
ENCRYPTION_ENV = "RENTAL_MANAGER_SETTINGS_ENCRYPTION_KEY"
ENCRYPTION_KEY_FILE_ENV = "RENTAL_MANAGER_SETTINGS_ENCRYPTION_KEY_FILE"
DEFAULT_KEY_FILE_NAME = "rental-manager-settings.key"


def _key_file_path() -> Path:
    configured = os.environ.get(ENCRYPTION_KEY_FILE_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    persistent_data = Path("/data")
    if os.name != "nt" and persistent_data.is_dir():
        return persistent_data / DEFAULT_KEY_FILE_NAME
    return Path(__file__).resolve().parents[2] / "data" / DEFAULT_KEY_FILE_NAME


def _read_or_create_file_key() -> bytes:
    key_path = _key_file_path()
    try:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            key = key_path.read_bytes().strip()
        except FileNotFoundError:
            generated = Fernet.generate_key()
            try:
                descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                key = key_path.read_bytes().strip()
            else:
                with os.fdopen(descriptor, "wb") as key_file:
                    key_file.write(generated + b"\n")
                key = generated
    except OSError as exc:
        raise RuntimeError(
            f"Не удалось подготовить ключ шифрования настроек в {key_path}. "
            f"Задайте {ENCRYPTION_ENV} или проверьте доступ к постоянному хранилищу."
        ) from exc
    if not key:
        raise RuntimeError(f"Файл ключа шифрования пуст: {key_path}")
    return key


def _fernet() -> Fernet:
    configured_key = os.environ.get(ENCRYPTION_ENV, "").strip()
    try:
        key = configured_key.encode("ascii") if configured_key else _read_or_create_file_key()
        return Fernet(key)
    except (ValueError, UnicodeEncodeError) as exc:
        source = ENCRYPTION_ENV if configured_key else str(_key_file_path())
        raise RuntimeError(f"Ключ шифрования настроек задан некорректно: {source}") from exc


def encrypt_secret(value: str) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    return ENCRYPTED_PREFIX + _fernet().encrypt(raw.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    raw = str(value or "")
    if not raw.startswith(ENCRYPTED_PREFIX):
        return raw
    try:
        return _fernet().decrypt(raw[len(ENCRYPTED_PREFIX) :].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise RuntimeError("Секретная настройка не может быть расшифрована текущим ключом.") from exc


def secret_is_encrypted(value: str) -> bool:
    return str(value or "").startswith(ENCRYPTED_PREFIX)
