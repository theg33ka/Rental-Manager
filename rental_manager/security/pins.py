from __future__ import annotations

import hashlib
import os
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


PIN_HASH_PREFIX = "$argon2id$"
PIN_SETTING_KEYS = {
    "owner": "panel_owner_pin_hash",
    "guest": "panel_guest_pin_hash",
}
PIN_ENV_KEYS = {
    "owner": "PANEL_OWNER_PIN",
    "guest": "PANEL_GUEST_PIN",
}
LEGACY_PIN_SETTING_KEYS = {
    "owner": "panel_owner_pin_code",
    "guest": "panel_guest_pin_code",
}
COMPROMISED_PIN_DIGESTS = {
    "d59a23c3feff6c21bbd651244d14c5639d3aa704751d4ce7aaa481712a18456d",
    "cbfad02f9ed2a8d1e08d8f74f5303e9eb93637d47f82ab6f1c15871cf8dd0481",
}

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16)


def is_pin_hash(value: str) -> bool:
    return str(value or "").startswith(PIN_HASH_PREFIX)


def pin_is_compromised(pin: str) -> bool:
    digest = hashlib.sha256(str(pin or "").encode("utf-8")).hexdigest()
    return digest in COMPROMISED_PIN_DIGESTS


def hash_pin(pin: str) -> str:
    normalized = str(pin or "").strip()
    if len(normalized) < 4 or len(normalized) > 64:
        raise ValueError("PIN-код должен содержать от 4 до 64 символов.")
    if pin_is_compromised(normalized):
        raise ValueError("Этот PIN-код был опубликован ранее и больше не может использоваться.")
    return _hasher.hash(normalized)


def verify_pin(pin_hash: str, candidate: str) -> bool:
    if not is_pin_hash(pin_hash):
        return False
    try:
        return bool(_hasher.verify(pin_hash, str(candidate or "").strip()))
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def pin_hash_needs_rehash(pin_hash: str) -> bool:
    try:
        return is_pin_hash(pin_hash) and _hasher.check_needs_rehash(pin_hash)
    except InvalidHashError:
        return False


@lru_cache(maxsize=4)
def environment_pin_hash(role: str, raw_value: str) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    if is_pin_hash(value):
        return value
    return hash_pin(value)


def configured_environment_pin_hash(role: str) -> str:
    env_name = PIN_ENV_KEYS[role]
    return environment_pin_hash(role, os.environ.get(env_name, ""))
