"""Initial schema for ushnotice microservice.

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-08-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. events
    op.create_table(
        'events',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('event_id', sa.String(length=36), nullable=False),
        sa.Column('event_type', sa.String(length=100), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('source', sa.String(length=100), nullable=False),
        sa.Column('correlation_id', sa.String(length=36), nullable=True),
        sa.Column('causation_id', sa.String(length=36), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='RECEIVED'),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('sqs_message_id', sa.String(length=100), nullable=True),
        sa.Column('sqs_receipt_handle', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_events_event_id', 'events', ['event_id'])
    op.create_index('ix_events_event_type', 'events', ['event_type'])
    op.create_index('ix_events_source', 'events', ['source'])
    op.create_index('ix_events_correlation_id', 'events', ['correlation_id'])
    op.create_index('ix_events_status', 'events', ['status'])
    op.create_index('ix_events_event_type_status', 'events', ['event_type', 'status'])
    op.create_index('ix_events_received_at', 'events', ['received_at'])

    # 2. event_processings (idempotency)
    op.create_table(
        'event_processings',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('event_id', sa.String(length=36), nullable=False),
        sa.Column('handler_name', sa.String(length=100), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='PENDING'),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', 'handler_name', name='uq_event_processing')
    )
    op.create_index('ix_event_processings_event_id', 'event_processings', ['event_id'])
    op.create_index('ix_event_processings_status', 'event_processings', ['status'])

    # 3. notifications
    op.create_table(
        'notifications',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('event_id', sa.String(length=36), nullable=False),
        sa.Column('customer_id', sa.String(length=36), nullable=True),
        sa.Column('booking_id', sa.String(length=36), nullable=True),
        sa.Column('channel', sa.String(length=20), nullable=False),
        sa.Column('recipient', sa.String(length=320), nullable=False),
        sa.Column('recipient_name', sa.String(length=255), nullable=True),
        sa.Column('template_name', sa.String(length=100), nullable=True),
        sa.Column('subject', sa.String(length=500), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='CREATED'),
        sa.Column('provider', sa.String(length=50), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('correlation_id', sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_notifications_event_id', 'notifications', ['event_id'])
    op.create_index('ix_notifications_customer_id', 'notifications', ['customer_id'])
    op.create_index('ix_notifications_booking_id', 'notifications', ['booking_id'])
    op.create_index('ix_notifications_channel', 'notifications', ['channel'])
    op.create_index('ix_notifications_status', 'notifications', ['status'])
    op.create_index('ix_notifications_provider', 'notifications', ['provider'])
    op.create_index('ix_notifications_created_at', 'notifications', ['created_at'])
    op.create_index('ix_notifications_correlation_id', 'notifications', ['correlation_id'])
    op.create_index('ix_notifications_channel_status', 'notifications', ['channel', 'status'])

    # 4. notification_status_history
    op.create_table(
        'notification_status_history',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('notification_id', sa.String(length=36), nullable=False),
        sa.Column('from_status', sa.String(length=20), nullable=True),
        sa.Column('to_status', sa.String(length=20), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['notification_id'], ['notifications.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_notification_status_history_notification_id', 'notification_status_history', ['notification_id'])

    # 5. notification_attempts
    op.create_table(
        'notification_attempts',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('notification_id', sa.String(length=36), nullable=False),
        sa.Column('provider', sa.String(length=50), nullable=False),
        sa.Column('attempt_number', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='PENDING'),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('provider_message_id', sa.String(length=255), nullable=True),
        sa.Column('provider_response', sa.JSON(), nullable=True),
        sa.Column('error_code', sa.String(length=50), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('is_retryable', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['notification_id'], ['notifications.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_notification_attempts_notification_id', 'notification_attempts', ['notification_id'])
    op.create_index('ix_notification_attempts_provider', 'notification_attempts', ['provider'])
    op.create_index('ix_notification_attempts_status', 'notification_attempts', ['status'])

    # 6. api_requests
    op.create_table(
        'api_requests',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('notification_id', sa.String(length=36), nullable=True),
        sa.Column('event_id', sa.String(length=36), nullable=True),
        sa.Column('service', sa.String(length=100), nullable=False),
        sa.Column('method', sa.String(length=10), nullable=False),
        sa.Column('path', sa.String(length=500), nullable=False),
        sa.Column('request_body', sa.JSON(), nullable=True),
        sa.Column('response_status', sa.Integer(), nullable=True),
        sa.Column('response_body', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='PENDING'),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('request_id', sa.String(length=36), nullable=True),
        sa.Column('correlation_id', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['notification_id'], ['notifications.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_api_requests_notification_id', 'api_requests', ['notification_id'])
    op.create_index('ix_api_requests_event_id', 'api_requests', ['event_id'])
    op.create_index('ix_api_requests_service', 'api_requests', ['service'])
    op.create_index('ix_api_requests_created_at', 'api_requests', ['created_at'])


def downgrade() -> None:
    op.drop_table('api_requests')
    op.drop_table('notification_attempts')
    op.drop_table('notification_status_history')
    op.drop_table('notifications')
    op.drop_table('event_processings')
    op.drop_table('events')
