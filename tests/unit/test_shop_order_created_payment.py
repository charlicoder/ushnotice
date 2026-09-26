"""
tests/unit/test_shop_order_created_payment.py
─────────────────────────────────────────────
Unit tests for ShopOrderCreatedHandler payment record creation.
Verifies that reference_id, track_id, country, payment_id, transaction_id,
invoice_id, transaction_date, payment_gateway, transaction_status, and created_by
are correctly extracted from the SQS message and forwarded to ushbooknpay.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.events.handlers.base import HandlerContext
from app.events.handlers.shop.shop_order_created import ShopOrderCreatedHandler
from app.events.schemas.envelope import EventEnvelope


def _make_shop_order_envelope(data: dict) -> EventEnvelope:
    return EventEnvelope(
        event_id=uuid.uuid4(),
        event_type="shop.order_created",
        version=1,
        occurred_at=datetime.now(tz=timezone.utc),
        source="ushbooknpay",
        data=data,
    )


@pytest.mark.asyncio
async def test_shop_order_created_extracts_and_forwards_all_payment_fields():
    """Verify that ShopOrderCreatedHandler extracts all transaction identifiers and forwards them."""
    order_id = str(uuid.uuid4())
    customer_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    payload_data = {
        "order_id": order_id,
        "order_number": "ORD-260925001",
        "customer_id": customer_id,
        "customer_name": "Mamunur Rashid",
        "customer_phone": "+96541028983",
        "customer_data": {
            "id": customer_id,
            "name": "Mamunur Rashid",
            "phone": "+96541028983",
        },
        "delivery_address": "Kuwait City",
        "public_token": "token_abc123",
        "tracking_code": "123456",
        "total_amount": "27.990",
        "currency": "KWD",
        "payment_status": "success",
        "payment_through": "ushspa",
        "payment_type": "ushspa",
        "payment_provider": "MyFatoorah",
        "payment_method": "KNET",
        "order_requested_by_user": user_id,
        "payment_url": "https://demo.MyFatoorah.com/checkout",
        "payment_data": {
            "invoiceId": "7205938",
            "status": "Paid",
            "isPaid": True,
            "data": {
                "InvoiceId": 7205938,
                "InvoiceStatus": "Paid",
                "InvoiceTransactions": [
                    {
                        "TransactionDate": "2026-09-25T14:30:43.7433333",
                        "PaymentGateway": "KNET",
                        "ReferenceId": "626810000600",
                        "TrackId": "25-09-2026_3843308",
                        "TransactionId": "626810011804085",
                        "PaymentId": "100626810000007146",
                        "TransactionStatus": "Succss",
                        "Country": "Kuwait",
                    }
                ],
            },
        },
        "items": [
            {
                "product_id": str(uuid.uuid4()),
                "product_name": "Argan Oil",
                "quantity": 1,
                "unit_price": "27.990",
                "line_total": "27.990",
            }
        ],
    }

    envelope = _make_shop_order_envelope(payload_data)
    handler = ShopOrderCreatedHandler()

    generated_payment_id = str(uuid.uuid4())
    mock_response = {
        "success": True,
        "data": {
            "id": generated_payment_id,
            "customer_id": customer_id,
            "total_amount": "27.990",
            "status": "success",
        },
    }

    with patch("app.events.handlers.shop.shop_order_created.UshBookNPayClient") as mock_client_cls, \
         patch("app.events.handlers.shop.shop_order_created.logger") as mock_logger, \
         patch("app.events.handlers.shop.shop_order_created.NotificationService") as mock_notif_svc_cls:

        mock_client = mock_client_cls.return_value
        mock_client.create_shop_order_payment = AsyncMock(return_value=mock_response)

        mock_notif_svc = mock_notif_svc_cls.return_value
        mock_notif_svc.send = AsyncMock()

        ctx = MagicMock(spec=HandlerContext)
        await handler.handle(envelope, ctx)

        mock_client.create_shop_order_payment.assert_called_once()
        call_kwargs = mock_client.create_shop_order_payment.call_args.kwargs

        assert call_kwargs["order_id"] == order_id
        assert call_kwargs["customer_id"] == customer_id
        assert call_kwargs["total_amount"] == "27.990"
        assert call_kwargs["reference_id"] == "626810000600"
        assert call_kwargs["track_id"] == "25-09-2026_3843308"
        assert call_kwargs["country"] == "Kuwait"
        assert call_kwargs["payment_id"] == "100626810000007146"
        assert call_kwargs["transaction_id"] == "626810011804085"
        assert call_kwargs["invoice_id"] == "7205938"
        assert call_kwargs["transaction_date"] == "2026-09-25T14:30:43.7433333"
        assert call_kwargs["payment_gateway"] == "KNET"
        assert call_kwargs["transaction_status"] == "Succss"
        assert call_kwargs["created_by"] == user_id
        assert call_kwargs["payment_url"] == "https://demo.MyFatoorah.com/checkout"
        assert call_kwargs["payment_data"]["invoiceId"] == "7205938"

        # Verify payment_id in info log is the UUID returned by ushbooknpay, not "?"
        info_calls = [c for c in mock_logger.info.call_args_list if c.args and c.args[0] == "shop_order_payment_record_created"]
        assert len(info_calls) == 1
        assert info_calls[0].kwargs["payment_id"] == generated_payment_id
