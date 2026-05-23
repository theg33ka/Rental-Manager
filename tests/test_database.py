from __future__ import annotations

import unittest

from rental_manager.database import DEFAULT_DATABASE_URL, configured_database_url, normalize_database_url


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

    def test_prefers_project_specific_database_url(self) -> None:
        self.assertEqual(
            configured_database_url(
                {
                    "DATABASE_URL": "postgresql://standard",
                    "RENTAL_MANAGER_DATABASE_URL": "postgresql://specific",
                }
            ),
            "postgresql://specific",
        )

    def test_falls_back_to_standard_database_url(self) -> None:
        self.assertEqual(
            configured_database_url({"DATABASE_URL": "postgresql://standard"}),
            "postgresql://standard",
        )

    def test_uses_sqlite_default_without_env(self) -> None:
        self.assertEqual(configured_database_url({}), DEFAULT_DATABASE_URL)


if __name__ == "__main__":
    unittest.main()
