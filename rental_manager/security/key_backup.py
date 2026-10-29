"""Проверяемая копия существующего ключа перед запуском миграций."""
from __future__ import annotations

import hashlib
import hmac
import json
import os

from cryptography.fernet import Fernet

from rental_manager.security.secrets import ENCRYPTION_ENV, _key_file_path


def settings_key_backup(*, create: bool = False) -> dict[str, str | bool]:
    configured = os.environ.get(ENCRYPTION_ENV, '').strip()
    if configured:
        Fernet(configured.encode('ascii'))
        return {'mode': 'deployment_secret_store', 'verified': True}
    source = _key_file_path()
    if not source.is_file():
        return {'mode': 'not_initialized', 'verified': False}
    original = source.read_bytes()
    Fernet(original.strip())
    directory = source.parent / 'key-backups'
    target = directory / (hashlib.sha256(original).hexdigest() + '.key')
    if create:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, 'wb') as output:
                output.write(original)
                output.flush()
                os.fsync(output.fileno())
    verified = target.is_file() and hmac.compare_digest(original, target.read_bytes())
    if create and not verified:
        raise RuntimeError('Settings key backup verification failed; migrations were not started')
    return {'mode': 'persistent_file', 'verified': verified}


if __name__ == '__main__':
    print(json.dumps(settings_key_backup(create=True)))
