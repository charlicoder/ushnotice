# `ushnotice` API Specification

Base Path: `/unotice/v1` or `/v1`

## Authentication
Internal endpoints require the header:
```http
USHSPA-TOKEN: <configured_token>
```

---

## Endpoints

### 1. Health & Liveness
- `GET /v1/health`
  - Response (200 OK):
    ```json
    {
      "status": "healthy",
      "service": "ushnotice",
      "version": "1.0.0",
      "environment": "production",
      "checks": {
        "database": "connected",
        "redis": "connected"
      }
    }
    ```
- `GET /v1/ping`
  - Response (200 OK): `{"ping": "pong"}`

---

### 2. Notifications Management
- `GET /v1/notifications`
  - Query parameters:
    - `page` (int, default: 1)
    - `page_size` (int, default: 20)
    - `channel` (string: `sms`, `whatsapp`, `email`)
    - `status` (string: `CREATED`, `SENT`, `DELIVERED`, `FAILED`, `RETRYING`)
    - `customer_id` (UUID)
    - `booking_id` (UUID)
    - `date_from` (ISO 8601)
    - `date_to` (ISO 8601)
  - Response: Paginated envelope containing masked recipient details.

- `GET /v1/notifications/{id}`
  - Response: Detailed notification object including all `attempts` and `status_history`.

- `POST /v1/notifications/{id}/resend`
  - Dispatches the notification in the background via FastAPI `BackgroundTasks`.

---

### 3. Events Audit
- `GET /v1/events`
  - Query parameters: `page`, `page_size`, `event_type`, `status`, `source`, `date_from`, `date_to`
- `GET /v1/events/{id}`
  - View event envelope and processing state.

---

### 4. Delivery Failures & Monitoring
- `GET /v1/delivery-failures`
  - Returns paginated list of failed notifications with provider error codes and retry counts.

---

### 5. Statistics
- `GET /v1/statistics`
  - Returns delivery rate percentages and status/channel count distributions cached in Redis.
