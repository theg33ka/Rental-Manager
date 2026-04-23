from __future__ import annotations

import unittest

from rental_manager.database import normalize_database_url


class DatabaseUrlTests(unittest.TestCase):
    def test_keeps_sqlite_url_as_is(self) -> None:
        self.assertEqual(normalize_database_url("sqlite:///data/app.db"), "sqlite:///data/app.db")

    def test_upgrades_postgresql_url_to_psycopg_driver(self) -> None:
        self.assertEqual(
            normalize_database_url("postgresql://user:pass@host:5432/db"),
            "postgresql+psycopg://user:pass@host:5432/db",
        )

    def test_upgrades_legacy_postgres_url(self) -> None:
        self.assertEqual(
            normalize_database_url("postgres://user:pass@host:5432/db"),
            "postgresql+psycopg://user:pass@host:5432/db",
        )


if __name__ == "__main__":
    unittest.main()
