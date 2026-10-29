import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from rental_manager.security.key_backup import settings_key_backup


class KeyBackupTests(unittest.TestCase):
    def test_file_copy_is_verified_without_changing_key_and_retry_keeps_copy(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'RENTAL_MANAGER_SETTINGS_ENCRYPTION_KEY': ''}):
            source = Path(folder) / 'settings.key'
            original = Fernet.generate_key() + b'\n'
            source.write_bytes(original)
            with patch('rental_manager.security.key_backup._key_file_path', return_value=source):
                self.assertFalse(settings_key_backup()['verified'])
                self.assertTrue(settings_key_backup(create=True)['verified'])
                self.assertTrue(settings_key_backup(create=True)['verified'])
                self.assertEqual(source.read_bytes(), original)
                copies = list((source.parent / 'key-backups').iterdir())
                self.assertEqual(len(copies), 1)
                self.assertEqual(copies[0].read_bytes(), original)
                copies[0].write_bytes(b'corrupt')
                with self.assertRaises(RuntimeError):
                    settings_key_backup(create=True)
                self.assertEqual(source.read_bytes(), original)

    def test_first_install_does_not_create_or_replace_key(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'RENTAL_MANAGER_SETTINGS_ENCRYPTION_KEY': ''}):
            source = Path(folder) / 'absent.key'
            with patch('rental_manager.security.key_backup._key_file_path', return_value=source):
                self.assertEqual(settings_key_backup(create=True), {'mode': 'not_initialized', 'verified': False})
                self.assertFalse(source.exists())

    def test_deployment_secret_does_not_get_written_to_disk_or_response(self):
        configured = Fernet.generate_key().decode()
        with patch.dict(os.environ, {'RENTAL_MANAGER_SETTINGS_ENCRYPTION_KEY': configured}), \
             patch('rental_manager.security.key_backup._key_file_path') as path:
            result = settings_key_backup(create=True)
            self.assertEqual(result, {'mode': 'deployment_secret_store', 'verified': True})
            self.assertNotIn(configured, str(result))
            path.assert_not_called()
