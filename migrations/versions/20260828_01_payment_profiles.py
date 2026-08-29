"""Add reusable payment profiles with object defaults and apartment overrides.

Revision ID: 20260828_01
Revises: 20260729_01
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260828_01"
down_revision: str | None = "20260729_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "payment_profiles" not in inspector.get_table_names():
        op.create_table(
            "payment_profiles",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("ip_recipient_name", sa.String(length=180), nullable=False, server_default=""),
            sa.Column("ip_recipient_inn", sa.String(length=20), nullable=False, server_default=""),
            sa.Column("ip_recipient_ogrnip", sa.String(length=20), nullable=False, server_default=""),
            sa.Column("ip_recipient_account", sa.String(length=34), nullable=False, server_default=""),
            sa.Column("ip_recipient_bank", sa.String(length=180), nullable=False, server_default=""),
            sa.Column("ip_recipient_bik", sa.String(length=20), nullable=False, server_default=""),
            sa.Column("ip_recipient_correspondent_account", sa.String(length=34), nullable=False, server_default=""),
            sa.Column("ip_recipient_bank_inn", sa.String(length=20), nullable=False, server_default=""),
            sa.Column("ip_recipient_bank_kpp", sa.String(length=20), nullable=False, server_default=""),
            sa.Column("personal_recipient_name", sa.String(length=180), nullable=False, server_default=""),
            sa.Column("personal_recipient_phone", sa.String(length=60), nullable=False, server_default=""),
            sa.Column("personal_recipient_bank", sa.String(length=180), nullable=False, server_default=""),
            sa.Column("notes", sa.Text(), nullable=False, server_default=""),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("name", name="uq_payment_profiles_name"),
        )
    inspector = sa.inspect(bind)
    if "payment_profile_id" not in {column["name"] for column in inspector.get_columns("rental_objects")}:
        with op.batch_alter_table("rental_objects") as batch:
            batch.add_column(sa.Column("payment_profile_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                "fk_rental_objects_payment_profile_id",
                "payment_profiles",
                ["payment_profile_id"],
                ["id"],
                ondelete="SET NULL",
            )
    inspector = sa.inspect(bind)
    if "payment_profile_id" not in {column["name"] for column in inspector.get_columns("apartments")}:
        with op.batch_alter_table("apartments") as batch:
            batch.add_column(sa.Column("payment_profile_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                "fk_apartments_payment_profile_id",
                "payment_profiles",
                ["payment_profile_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table_name in ("apartments", "rental_objects"):
        if table_name not in inspector.get_table_names():
            continue
        if "payment_profile_id" not in {column["name"] for column in inspector.get_columns(table_name)}:
            continue
        foreign_key = next(
            (
                item
                for item in inspector.get_foreign_keys(table_name)
                if item.get("constrained_columns") == ["payment_profile_id"]
            ),
            None,
        )
        with op.batch_alter_table(table_name) as batch:
            if foreign_key and foreign_key.get("name"):
                batch.drop_constraint(str(foreign_key["name"]), type_="foreignkey")
            batch.drop_column("payment_profile_id")
    if "payment_profiles" in sa.inspect(bind).get_table_names():
        op.drop_table("payment_profiles")
