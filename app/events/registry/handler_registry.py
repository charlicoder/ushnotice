"""
Event handler registry — the Open/Closed core of the event-driven system.

New event handlers can be added by:
1. Creating a new handler class in ``app/events/handlers/``
2. Registering it with :func:`register_handler` or in :func:`build_default_registry`

The router and consumer NEVER need modification when adding new event types.
This enforces the Open/Closed Principle at the framework level.
"""
from __future__ import annotations

from app.core.exceptions import UnknownEventTypeError
from app.core.logging import get_logger
from app.events.handlers.base import EventHandler

logger = get_logger(__name__)


class HandlerRegistry:
    """Maps event type strings to handler instances.

    Supports multiple handlers per event type (e.g. send SMS AND email
    for the same event).

    Usage::

        registry = HandlerRegistry()
        registry.register(UserRegisteredHandler())
        handlers = registry.resolve("user.registered")
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = {}

    def register(self, handler: EventHandler) -> None:
        """Register a handler for its declared event type.

        Args:
            handler: A handler instance satisfying the :class:`EventHandler` protocol.

        Raises:
            TypeError: If *handler* does not satisfy the EventHandler Protocol.
        """
        if not isinstance(handler, EventHandler):
            raise TypeError(
                f"{handler!r} does not satisfy the EventHandler Protocol. "
                f"Missing: event_type (str) or handle (coroutine method)."
            )
        event_type = handler.event_type.lower()
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)
        logger.debug(
            "Handler registered",
            event_type=event_type,
            handler=type(handler).__name__,
        )

    def resolve(self, event_type: str) -> list[EventHandler]:
        """Return all handlers for the given event type.

        Args:
            event_type: The event type to resolve (case-insensitive).

        Returns:
            List of registered handlers.

        Raises:
            UnknownEventTypeError: If no handler is registered for this event type.
        """
        normalised = event_type.lower()
        handlers = self._handlers.get(normalised)
        if not handlers:
            raise UnknownEventTypeError(event_type)
        return handlers

    def has_handler(self, event_type: str) -> bool:
        """Return ``True`` if at least one handler is registered for *event_type*."""
        return event_type.lower() in self._handlers

    def registered_event_types(self) -> list[str]:
        """Return all registered event type strings, sorted."""
        return sorted(self._handlers.keys())


def build_default_registry() -> HandlerRegistry:
    """Construct and return the application handler registry.

    All handlers are imported and registered here.  The registry is
    constructed at startup and shared for the lifetime of the process.

    To add a new handler:
    1. Create ``app/events/handlers/<category>/<event_name>.py``
    2. Add an import and ``registry.register(...)`` call below.
    """
    registry = HandlerRegistry()

    # ── Auth / User handlers ──────────────────────────────────────────────────
    from app.events.handlers.auth.user_registered import UserRegisteredHandler
    from app.events.handlers.auth.password_reset import PasswordResetHandler
    from app.events.handlers.auth.user_verified import UserVerifiedHandler
    from app.events.handlers.auth.password_changed import PasswordChangedHandler
    from app.events.handlers.auth.customer_delete import CustomerDeleteHandler
    from app.events.handlers.auth.email_verification import EmailVerificationHandler
    from app.events.handlers.auth.customer_new_created import CustomerCreatedHandler, CustomerNewCreatedHandler

    # Register under both dot-notation and underscore aliases
    # (some upstream services publish event types with underscores instead of dots)
    for _h in [
        UserRegisteredHandler(),
        PasswordResetHandler(),
        UserVerifiedHandler(),
        PasswordChangedHandler(),
        CustomerDeleteHandler(),
        EmailVerificationHandler(),
        CustomerNewCreatedHandler(),
        CustomerCreatedHandler(),
    ]:
        registry.register(_h)
        # Register underscore alias (e.g. "user_registered" for "user.registered")
        _alias_type = _h.event_type.replace(".", "_")
        if _alias_type != _h.event_type:
            _alias = _h.__class__()
            _alias.event_type = _alias_type
            registry.register(_alias)

    # ── Booking handlers ──────────────────────────────────────────────────────
    from app.events.handlers.booking.booking_confirmed import BookingConfirmedHandler
    from app.events.handlers.booking.booking_requested import BookingRequestedHandler  # handles booking.created
    from app.events.handlers.booking.payment_pending import BookingPaymentPendingHandler
    from app.events.handlers.booking.payment_failed import BookingPaymentFailedHandler
    from app.events.handlers.booking.reschedule_request import RescheduleRequestHandler
    from app.events.handlers.booking.booking_payment_status_success import BookingPaymentStatusSuccessHandler

    for _h in [
        BookingConfirmedHandler(),
        BookingRequestedHandler(),   # event_type = "booking.created"
        BookingPaymentPendingHandler(),
        BookingPaymentFailedHandler(),
        RescheduleRequestHandler(),
        BookingPaymentStatusSuccessHandler(),
    ]:
        registry.register(_h)
        _alias_type = _h.event_type.replace(".", "_")
        if _alias_type != _h.event_type:
            _alias = _h.__class__()
            _alias.event_type = _alias_type
            registry.register(_alias)

    # ── Payment handlers ──────────────────────────────────────────────────────
    from app.events.handlers.payment.payment_success import PaymentSuccessHandler
    from app.events.handlers.payment.payment_failed import PaymentFailedHandler
    from app.events.handlers.payment.payment_refunded import PaymentRefundedHandler
    from app.events.handlers.payment.payment_pending import PaymentPendingHandler

    for _h in [
        PaymentSuccessHandler(),
        PaymentFailedHandler(),
        PaymentRefundedHandler(),
        PaymentPendingHandler(),
    ]:
        registry.register(_h)
        _alias_type = _h.event_type.replace(".", "_")
        if _alias_type != _h.event_type:
            _alias = _h.__class__()
            _alias.event_type = _alias_type
            registry.register(_alias)

    # ── Loyalty handlers ──────────────────────────────────────────────────────
    from app.events.handlers.loyalty.loyalty_rewarded import LoyaltyRewardedHandler
    from app.events.handlers.loyalty.loyalty_redeemed import LoyaltyRedeemedHandler

    for _h in [
        LoyaltyRewardedHandler(),
        LoyaltyRedeemedHandler(),
    ]:
        registry.register(_h)
        _alias_type = _h.event_type.replace(".", "_")
        if _alias_type != _h.event_type:
            _alias = _h.__class__()
            _alias.event_type = _alias_type
            registry.register(_alias)

    # ── Voucher handlers ──────────────────────────────────────────────────────
    from app.events.handlers.voucher.voucher_active import VoucherActiveHandler
    from app.events.handlers.voucher.voucher_redeemed import VoucherRedeemedHandler

    for _h in [
        VoucherActiveHandler(),
        VoucherRedeemedHandler(),
    ]:
        registry.register(_h)
        _alias_type = _h.event_type.replace(".", "_")
        if _alias_type != _h.event_type:
            _alias = _h.__class__()
            _alias.event_type = _alias_type
            registry.register(_alias)

    logger.info(
        "Handler registry built",
        event_types=registry.registered_event_types(),
        total=len(registry.registered_event_types()),
    )
    return registry
