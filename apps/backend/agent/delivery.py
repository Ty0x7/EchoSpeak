"""Durable owner outbox. Transport acceptance and confirmed receipts differ."""
from __future__ import annotations

from agent.coordination_service import coordinator


def dispatch(identity: str) -> dict:
    from config import config
    from agent.lean import stop, outbound
    from agent import privacy
    coord = coordinator()
    row = next((r for r in coord.view("deliveries") if r["id"] == identity), None)
    if not row:
        raise KeyError("Delivery unavailable")
    if row["status"] in {"accepted", "confirmed", "unknown", "sending"}:
        return row
    if stop.paused():
        return row
    channel = row["channel"]
    if channel in {"discord", "telegram"}:
        expected = str(getattr(config, "discord_bot_owner_id" if channel == "discord" else "telegram_owner_id", "") or "")
        enabled = bool(getattr(config, "allow_discord_bot" if channel == "discord" else "allow_telegram_bot", False))
        if not enabled or not expected or expected != row["destination"] or privacy.mode() != "standard":
            return coord.delivery_state(identity, "failed", error="Owner route changed or privacy blocks external delivery")
        if outbound.take(channel):
            return coord.delivery_state(identity, "failed", error="Outbound rate limit; retry delivery later")
    coord.delivery_state(identity, "sending")
    attempted = False
    def receipt(future):
        try:
            value = future.result()
            coord.delivery_state(identity, "confirmed", receipt=str(getattr(value, "id", "") or getattr(value, "message_id", "") or "transport receipt"))
        except Exception as exc:
            coord.delivery_state(identity, "unknown", error=type(exc).__name__)
    try:
        if channel == "desktop":
            from agent import notifications
            attempted = True
            title, _, body = row["text"].partition("\n\n")
            notifications.push(title, body or row["text"], session_id=row["session_id"], ref=identity)
            return coord.delivery_state(identity, "accepted", receipt="Notification feed queued; OS display not confirmed")
        if channel == "chat":
            return coord.delivery_state(identity, "accepted", receipt="Lean persisted the result in the originating chat")
        if channel == "discord":
            from discord_bot import queue_discord_dm
            attempted = True
            accepted = queue_discord_dm(row["destination"], row["text"][:1990], on_receipt=receipt)
        elif channel == "telegram":
            from telegram_bot import get_telegram_bot
            bot = get_telegram_bot()
            if bot is None:
                return coord.delivery_state(identity, "failed", error="Telegram not connected")
            attempted = True
            accepted = bot.send_owner(row["text"][:3900], on_receipt=receipt)
        else:
            raise ValueError("Unsupported channel")
        if not accepted:
            return coord.delivery_state(identity, "failed", error="Transport unavailable; nothing queued")
        # Callback owns the terminal transport state; avoid overwriting a fast receipt.
        return next(r for r in coord.view("deliveries") if r["id"] == identity)
    except Exception as exc:
        return coord.delivery_state(identity, "unknown" if attempted else "failed", error=type(exc).__name__)


def queue_result(text, channels, *, label, agent_id, session_id, key, work_id=""):
    from config import config
    coord = coordinator()
    delivered = []
    for channel in sorted(set(channels)):
        if channel == "web":
            channel = "chat"
        destination = session_id if channel in {"desktop", "chat"} else str(getattr(config,
            "discord_bot_owner_id" if channel == "discord" else "telegram_owner_id", "") or "")
        if not destination:
            continue
        row = coord.enqueue(key=key, channel=channel, destination=destination, text=f"{label}\n\n{text}",
            agent_id=agent_id, work_id=work_id, session_id=session_id)
        state = dispatch(row["id"])
        if state["status"] in {"accepted", "confirmed"}:
            delivered.append(channel)
    return delivered


_last_prune = 0.0


def drain(limit=20):
    """Model-free outbox upkeep on the existing scheduler tick."""
    import time
    global _last_prune
    coord = coordinator()
    if time.time() - _last_prune > 3600:
        _last_prune = time.time()
        coord.prune()  # retention for finished work history (review R10)
    for row in coord.view("deliveries")[-limit:]:
        if row["status"] in {"queued", "failed"}:
            dispatch(row["id"])
        elif row["status"] == "sending" and time.time() - row.get("updated_at", row["created_at"]) > 600:
            coord.delivery_state(row["id"], "unknown", error="Receipt timeout; inspect destination")
