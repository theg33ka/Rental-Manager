import os
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest


class CashMigrationTests(unittest.TestCase):
    def migrate(self, path, revision):
        env = {**os.environ, "RENTAL_MANAGER_DATABASE_URL": f"sqlite:///{path.as_posix()}", "RENTAL_MANAGER_ENV": "development"}
        result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", revision], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_clean_and_existing_database_upgrade_preserves_profile(self):
        with tempfile.TemporaryDirectory() as root:
            clean = Path(root) / "clean.db"
            self.migrate(clean, "head")
            with closing(sqlite3.connect(clean)) as db:
                self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone()[0], "20261001_01")
                self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE name='cash_payment_requests'").fetchone())
            existing = Path(root) / "existing.db"
            self.migrate(existing, "20260828_01")
            # Начальная ревизия использует текущую metadata; возвращаем только новые поля к старой схеме на тестовой БД.
            with closing(sqlite3.connect(existing)) as db:
                db.execute("DROP TABLE cash_payment_requests")
                db.execute("ALTER TABLE payment_profiles DROP COLUMN ip_payment_method")
                db.execute("ALTER TABLE payment_profiles DROP COLUMN personal_payment_method")
                columns = db.execute("PRAGMA table_info(payment_profiles)").fetchall()
                values = {row[1]: '' for row in columns if row[1] not in {'id', 'active', 'created_at', 'updated_at'}}
                values.update(name='Existing profile', ip_recipient_account='KEEP')
                values.update(active=1, created_at='2026-09-01', updated_at='2026-09-01')
                db.execute(f"INSERT INTO payment_profiles ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", list(values.values()))
                db.commit()
            self.migrate(existing, "head")
            self.migrate(existing, "head")
            with closing(sqlite3.connect(existing)) as db:
                row = db.execute("SELECT ip_recipient_account, ip_payment_method, personal_payment_method FROM payment_profiles WHERE name='Existing profile'").fetchone()
                self.assertEqual(row, ('KEEP', 'transfer', 'transfer'))
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
