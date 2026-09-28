"""add troca discovery config

Revision ID: f866fdfc6df4
Revises: c04a992df0d5
Create Date: 2026-09-28 15:19:43.031606

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f866fdfc6df4"
down_revision: str | Sequence[str] | None = "c04a992df0d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("troca_config", sa.Column("schedule_sync_mode", sa.String(), server_default="ocpp", nullable=False))
    op.add_column("troca_config", sa.Column("ocpp_connector_name", sa.String(), nullable=True))
    op.add_column("troca_config", sa.Column("ocpp_version", sa.String(), nullable=True))
    op.add_column("troca_config", sa.Column("ocpp_station_name", sa.String(), nullable=True))
    op.add_column("troca_config", sa.Column("ocpp_evse_nb", sa.Integer(), nullable=True))
    op.add_column("troca_config", sa.Column("evse_id", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("troca_config", "evse_id")
    op.drop_column("troca_config", "ocpp_evse_nb")
    op.drop_column("troca_config", "ocpp_station_name")
    op.drop_column("troca_config", "ocpp_version")
    op.drop_column("troca_config", "ocpp_connector_name")
    op.drop_column("troca_config", "schedule_sync_mode")
