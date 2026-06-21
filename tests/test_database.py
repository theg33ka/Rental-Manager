from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import SQLAlchemyError

from rental_manager.database import (
    DEFAULT_DATABASE_URL,
    configured_database_url,
    ensure_postgres_database_exists,
    normalize_database_url,
    postgres_database_bootstrap_target,
    quote_postgres_identifier,
)


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

    def test_postgres_bootstrap_target_uses_maintenance_database(self) -> None:
        target = postgres_database_bootstrap_target("postgresql+psycopg://user:pass@host:5432/rent_db")

        self.assertIsNotNone(target)
        assert target is not None
        maintenance_url, database_name = target
        self.assertEqual(maintenance_url.database, "postgres")
        self.assertEqual(database_name, "rent_db")

    def test_postgres_bootstrap_target_skips_sqlite(self) -> None:
        self.assertIsNone(postgres_database_bootstrap_target("sqlite:///data/app.db"))

    def test_quotes_postgres_identifier(self) -> None:
        self.assertEqual(quote_postgres_identifier('rent"_db'), '"rent""_db"')

    @patch("rental_manager.database.create_engine")
    def test_ensure_postgres_database_exists_creates_missing_database(self, create_engine_mock: MagicMock) -> None:
        connection = MagicMock()
        connection.scalar.return_value = None
        engine = MagicMock()
        engine.connect.return_value.__enter__.return_value = connection
        create_engine_mock.return_value = engine

        ensure_postgres_database_exists("postgresql+psycopg://user:pass@host:5432/rent_db")

        create_engine_mock.assert_called_once()
        self.assertEqual(create_engine_mock.call_args.kwargs["isolation_level"], "AUTOCOMMIT")
        self.assertEqual(str(connection.execute.call_args.args[0]), 'CREATE DATABASE "rent_db"')
        engine.dispose.assert_called_once()

    @patch("rental_manager.database.create_engine")
    def test_ensure_postgres_database_exists_does_not_create_existing_database(self, create_engine_mock: MagicMock) -> None:
        connection = MagicMock()
        connection.scalar.return_value = 1
        engine = MagicMock()
        engine.connect.return_value.__enter__.return_value = connection
        create_engine_mock.return_value = engine

        ensure_postgres_database_exists("postgresql+psycopg://user:pass@host:5432/rent_db")

        connection.execute.assert_not_called()
        engine.dispose.assert_called_once()

    @patch("rental_manager.database.create_engine")
    def test_ensure_postgres_database_exists_is_best_effort(self, create_engine_mock: MagicMock) -> None:
        engine = MagicMock()
        engine.connect.side_effect = SQLAlchemyError("maintenance database unavailable")
        create_engine_mock.return_value = engine

        ensure_postgres_database_exists("postgresql+psycopg://user:pass@host:5432/rent_db")

        engine.dispose.assert_called_once()


if __name__ == "__main__":
    unittest.main()
