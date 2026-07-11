"""Reset panel PIN hashes for emergency access.

Revision ID: 20260711_02
Revises: 20260711_01
Create Date: 2026-07-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260711_02"
down_revision: str | None = "20260711_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


EMERGENCY_PIN_HASHES = {
    "panel_owner_pin_hash": "$argon2id$v=19$m=65536,t=3,p=2$tJiYRfaNxCkPmxtkhwGCRg$VPoQCyx0dfSph6XBTRpsxjtz9qpuTwcyNKa+GbeASt4",
    "panel_guest_pin_hash": "$argon2id$v=19$m=65536,t=3,p=2$0PQkqJVv2VK4qRow/umsyQ$m3nSZloS6JwXE32C4YxB5xagHYwZ1M2gTEtra7EncjU",
}


def upgrade() -> None:
    bind = op.get_bind()
    app_settings = sa.table(
        "app_settings",
        sa.column("key", sa.String(120)),
        sa.column("value", sa.Text()),
    )
    for key, pin_hash in EMERGENCY_PIN_HASHES.items():
        result = bind.execute(app_settings.update().where(app_settings.c.key == key).values(value=pin_hash))
        if not result.rowcount:
            bind.execute(app_settings.insert().values(key=key, value=pin_hash))


def downgrade() -> None:
    pass
