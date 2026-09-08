"""
Service tests for FastAPI endpoints (/health, /notifications, /events, /statistics).
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.security import get_ushspa_token


@pytest.mark.asyncio
async def test_health_endpoint(async_client: AsyncClient) -> None:
    response = await async_client.get("/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


@pytest.mark.asyncio
async def test_ping_endpoint(async_client: AsyncClient) -> None:
    response = await async_client.get("/v1/ping")
    assert response.status_code == 200
    assert response.json() == {"ping": "pong"}


@pytest.mark.asyncio
async def test_notifications_unauthorized(async_client: AsyncClient) -> None:
    response = await async_client.get("/v1/notifications")
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_notifications_authorized(async_client: AsyncClient) -> None:
    token = get_ushspa_token()
    response = await async_client.get(
        "/v1/notifications",
        headers={"USHSPA-TOKEN": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_statistics_endpoint(async_client: AsyncClient) -> None:
    token = get_ushspa_token()
    response = await async_client.get(
        "/v1/statistics",
        headers={"USHSPA-TOKEN": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert "total_notifications" in data
    assert "by_status" in data
