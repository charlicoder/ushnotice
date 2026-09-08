"""
Pagination utilities for dashboard list endpoints.

Provides:
- :class:`PaginationParams` — FastAPI query-parameter dependency.
- :class:`PaginatedResponse` — Generic JSON envelope for paginated results.
"""
from __future__ import annotations

from typing import Annotated, Generic, TypeVar

from fastapi import Depends, Query
from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginationParams:
    """FastAPI dependency for extracting ``page`` and ``page_size`` from query params.

    Usage::

        @router.get("/items")
        async def list_items(
            pagination: PaginationParams = Depends(),
        ) -> PaginatedResponse[ItemSchema]:
            offset = pagination.offset
            limit = pagination.page_size
            ...
    """

    def __init__(
        self,
        page: Annotated[int, Query(ge=1, description="Page number, 1-indexed.")] = 1,
        page_size: Annotated[
            int, Query(ge=1, le=200, description="Items per page (max 200).")
        ] = 20,
    ) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        """SQL ``OFFSET`` value corresponding to the current page."""
        return (self.page - 1) * self.page_size


class PaginatedResponse(BaseModel, Generic[T]):
    """Standard paginated response envelope.

    Attributes:
        items: The items for the current page.
        total: Total number of items across all pages.
        page: Current page number (1-indexed).
        page_size: Number of items per page.
        pages: Total number of pages.
    """

    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    pages: int = Field(ge=0)

    @classmethod
    def build(
        cls,
        items: list[T],
        total: int,
        pagination: PaginationParams,
    ) -> "PaginatedResponse[T]":
        """Construct a paginated response from items and pagination params."""
        pages = max(1, -(-total // pagination.page_size))  # ceiling division
        return cls(
            items=items,
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
            pages=pages,
        )
