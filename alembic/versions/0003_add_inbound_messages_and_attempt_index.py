"""Add inbound_messages table and provider_message_id index on notification_attempts.

Revision ID: 0003_inbound_messages
Revises: 0002_set_db_timezone_kuwait
Create Date: 2026-11-02
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_inbound_messages"
down_revision: Union[str, None] = "0002_set_db_timezone_kuwait"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create inbound_messages table
    op.create_table(
        "inbound_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False, server_default="whatsapp"),
        sa.Column("provider", sa.String(length=50), nullable=False, server_default="meta"),
        sa.Column("provider_message_id", sa.String(length=255), nullable=False),
        sa.Column("from_number", sa.String(length=50), nullable=False),
        sa.Column("to_number_id", sa.String(length=50), nullable=True),
        sa.Column("sender_name", sa.String(length=255), nullable=True),
        sa.Column("message_type", sa.String(length=50), nullable=False, server_default="text"),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("is_processed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_inbound_messages_provider_message_id",
        "inbound_messages",
        ["provider_message_id"],
        unique=True,
    )
    op.create_index("ix_inbound_messages_from_number", "inbound_messages", ["from_number"])
    op.create_index(
        "ix_inbound_messages_channel_received_at",
        "inbound_messages",
        ["channel", "received_at"],
    )

    # 2. Add index on notification_attempts.provider_message_id
    op.create_index(
        "ix_notification_attempts_provider_message_id",
        "notification_attempts",
        ["provider_message_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_attempts_provider_message_id",
        table_name="notification_attempts",
    )
    op.drop_index("ix_inbound_messages_channel_received_at", table_name="inbound_messages")
    op.drop_index("ix_inbound_messages_from_number", table_name="inbound_messages")
    op.drop_index("ix_inbound_messages_provider_message_id", table_name="inbound_messages")
    op.drop_table("inbound_messages")
