"""Small Telegram helpers shared by handlers."""

from __future__ import annotations

import logging

from telegram.error import BadRequest

log = logging.getLogger(__name__)

NOT_MODIFIED = "message is not modified"


def is_not_modified(exc: BaseException) -> bool:
    return isinstance(exc, BadRequest) and NOT_MODIFIED in str(exc).lower()


async def answer_safely(query, text: str | None = None, show_alert: bool = False) -> None:
    """Acknowledge a callback query. A stale query must never break the handler."""
    try:
        await query.answer(text, show_alert=show_alert)
    except Exception as exc:  # noqa: BLE001 - "Query is too old" is common
        log.debug("callback answer failed: %s", exc)


async def edit_text_safely(query, text: str, reply_markup=None) -> bool:
    """Edit the callback message, tolerating 'nothing changed'.

    Returns True when the API call actually rewrote the message.
    """
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
        return True
    except Exception as exc:  # noqa: BLE001
        if is_not_modified(exc):
            return False
        log.warning("edit_message_text failed: %s", exc)
        raise
