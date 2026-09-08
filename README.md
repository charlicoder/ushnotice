# USHSPA Notification Microservice (`ushnotice`)

`ushnotice` is an event-driven, multi-channel notification platform designed for the USHSPA ecosystem.

---

## 🌟 Key Features

1. **Multi-Channel Delivery**:
   - **SMS**: KWT SMS (v4.1 REST API) & Twilio fallback
   - **WhatsApp**: Meta Cloud Graph API (templates & text)
   - **Email**: Gmail SMTP with STARTTLS / aiosmtplib
2. **Event-Driven Architecture**:
   - AWS SQS consumer with async long-polling and batch dispatching
   - Clean separation between Consumer, Router, and Event Handlers (Open/Closed Principle)
   - Idempotency guard backed by `event_processings` table
3. **Background Tasks**:
   - Native FastAPI `BackgroundTasks` execution (no external Celery dependency)
4. **Inter-Service Communication**:
   - API Gateway routing via `API_GATEWAY_BASE_URL`
   - Injects `USHSPA-TOKEN` on all outbound requests
   - Preserves `X-Request-ID` and `X-Correlation-ID` for distributed tracing
5. **Observability & Audit**:
   - Structured JSON logging with `structlog`
   - Automatic redaction of sensitive credentials, tokens, and passwords
   - Complete audit trail of events, notifications, status history, attempts, and API calls
   - Live dashboard APIs with PII masking

---

## 🏗 Architecture Overview

```
[ AWS SQS Queue ]
       │
       ▼
 [ SQSConsumer ]
       │
       ▼
[ MessageProcessor ] ── (Parse & Validate Schema)
       │
       ▼
 [ EventRouter ] ── (Idempotency Guard & Handler Registry)
       │
       ├──► UserRegisteredHandler
       ├──► PasswordResetHandler
       ├──► BookingConfirmedHandler
       ├──► RescheduleRequestHandler ──► [ushauth] (Branch Contacts)
       │                              └──► [ushbooknpay] (Status Update)
       └──► PaymentSuccessHandler
              │
              ▼
    [ NotificationService ]
              │
    ┌─────────┼──────────┐
    ▼         ▼          ▼
[KWT SMS]  [Meta WA]  [Gmail]
```

---

## 🚀 Getting Started

### Prerequisites
- Python 3.12+
- PostgreSQL 15+
- Redis 7+
- AWS SQS Queue (or local simulator)

### Setup & Run
```bash
# 1. Clone & create virtualenv
python3.14 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -e ".[dev]"

# 3. Apply database migrations
alembic upgrade head

# 4. Start service
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 🧪 Testing

```bash
# Run unit and service test suite
pytest
```

---

## 📚 API Endpoints

All dashboard endpoints require the `USHSPA-TOKEN` header.

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/v1/health` | GET | Health and readiness check |
| `/v1/ping` | GET | Liveness probe |
| `/v1/notifications` | GET | List notifications (with filters & pagination) |
| `/v1/notifications/{id}` | GET | View notification details, attempts, and history |
| `/v1/notifications/{id}/resend` | POST | Resend notification in background |
| `/v1/events` | GET | List received events |
| `/v1/events/{id}` | GET | View event details |
| `/v1/delivery-failures` | GET | List failed notifications |
| `/v1/statistics` | GET | Aggregated delivery metrics |
