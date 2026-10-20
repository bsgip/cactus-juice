"""add acudc satec reading

Revision ID: a3c1d7e9b5f2
Revises: f866fdfc6df4
Create Date: 2026-10-20 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3c1d7e9b5f2"
down_revision: str | Sequence[str] | None = "f866fdfc6df4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Previously always sampled - now only by SATEC (AC) meters, an AcuDC (DC) meter leaves them NULL
AC_COLUMNS = (
    "total_kw",
    "total_kvar",
    "total_kva",
    "total_pf",
    "total_pf_lag",
    "total_pf_lead",
    "kw_import",
    "kw_export",
    "kvar_import",
    "kvar_export",
    "v_avg_ln",
    "v_avg_ll",
    "i_avg",
    "frequency",
    "frequency_mhz",
    "i_neutral",
    "v_unbalance",
    "i_unbalance",
)

DC_COLUMNS = (
    "v_source",
    "i_ch1",
    "i_ch2",
    "i_sum",
    "kw_ch1",
    "kw_ch2",
    "kw_sum",
    "v_ripple",
    "v_load_ch1",
    "v_load_ch2",
)


def upgrade() -> None:
    """Upgrade schema."""
    for col in AC_COLUMNS:
        op.alter_column("satec_reading", col, existing_type=sa.DOUBLE_PRECISION(), nullable=True)
    for col in DC_COLUMNS:
        op.add_column("satec_reading", sa.Column(col, sa.DOUBLE_PRECISION(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # DC readings can't satisfy the restored NOT NULL constraints - they have to go
    op.execute("DELETE FROM satec_reading WHERE total_kw IS NULL")
    for col in reversed(DC_COLUMNS):
        op.drop_column("satec_reading", col)
    for col in AC_COLUMNS:
        op.alter_column("satec_reading", col, existing_type=sa.DOUBLE_PRECISION(), nullable=False)
