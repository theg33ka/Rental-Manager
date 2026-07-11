from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


ENCRYPTED_PREFIX = "enc:v1:"
ENCRYPTION_ENV = "RENTAL_MANAGER_SETTINGS_ENCRYPTION_KEY"


def _fernet() -> Fernet:
    key = os.environ.get(ENCRYPTION_ENV, "").strip()
    if not key:
        raise RuntimeError(f"Для сохранения секретов через интерфейс задайте {ENCRYPTION_ENV}.")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise RuntimeError(f"{ENCRYPTION_ENV} должен быть корректным ключом Fernet.") from exc


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
