"""
Telegram Bot integration for EchoSpeak v5.4.0.

Polls for incoming messages on a Telegram bot, routes them through the
EchoSpeak agent pipeline, and sends the response back.

Dependencies:
    pip install python-telegram-bot

Config:
    ALLOW_TELEGRAM_BOT=true
    TELEGRAM_BOT_TOKEN=<BotFather token>
    TELEGRAM_ALLOWED_USERS=username1,username2   (optional — empty = allow all)
    TELEGRAM_AUTO_CONFIRM=true                   (auto-approve all tool actions)
"""

from __future__ import annotations

import asyncio
import threading
from typing import Optional, Any

from loguru import logger


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_telegram_bot: Optional["TelegramBotManager"] = None


def get_telegram_bot() -> Optional["TelegramBotManager"]:
    return _telegram_bot


def set_telegram_bot(bot: "TelegramBotManager") -> None:
    global _telegram_bot
    _telegram_bot = bot


# ---------------------------------------------------------------------------
# TelegramBotManager
# ---------------------------------------------------------------------------

class TelegramBotManager:
    """
    Manages a Telegram bot that routes messages through the EchoSpeak pipeline.

    Usage:
        bot = TelegramBotManager(agent=agent)
        bot.start()   # Non-blocking — runs polling in a background thread
        bot.stop()
    """

    def __init__(self, agent: Any) -> None:
        from config import config

        self._agent = agent
        self._token = getattr(config, "telegram_bot_token", "")
        self._allowed_users = list(getattr(config, "telegram_allowed_users", []))
        self._auto_confirm = getattr(config, "telegram_auto_confirm", True)

        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._application = None  # telegram.ext.Application
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the Telegram bot in a background thread."""
        if self._running:
            logger.debug("TelegramBotManager: already running")
            return
        if not self._token:
            logger.warning("TelegramBotManager: TELEGRAM_BOT_TOKEN not set — skipping")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            name="echospeak-telegram",
            daemon=True,
        )
        self._thread.start()
        logger.info("TelegramBotManager started")

    def stop(self) -> None:
        """Stop the Telegram bot cleanly."""
        self._running = False
        if self._application and self._loop:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._application.stop(), self._loop
                )
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("TelegramBotManager stopped")

    @property
    def is_running(self) -> bool:
        return self._running and bool(self._thread) and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Heartbeat integration
    # ------------------------------------------------------------------

    def send_heartbeat(self, text: str) -> None:
        """Personal updates go only to the explicitly configured owner."""
        self.send_owner(text)

    def send_owner(self, text: str, *, on_receipt=None) -> bool:
        if not self._application or not self._loop:
            return False
        try:
            from config import config
            owner = str(getattr(config, "telegram_owner_id", "") or "").strip()
            if not owner:
                return False

            async def _send():
                bot = self._application.bot
                return await bot.send_message(chat_id=owner, text=text)

            future = asyncio.run_coroutine_threadsafe(_send(), self._loop)
            if on_receipt:
                future.add_done_callback(on_receipt)
            return True
        except Exception as exc:
            logger.debug(f"TelegramBotManager: send_heartbeat error — {exc}")
            return False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Background thread: creates an event loop + starts polling."""
        try:
            from telegram import Update
            from telegram.ext import (
                ApplicationBuilder,
                CommandHandler,
                MessageHandler,
                ContextTypes,
                filters,
            )
        except ImportError:
            logger.error(
                "TelegramBotManager: python-telegram-bot not installed. "
                "Run: pip install python-telegram-bot"
            )
            self._running = False
            return

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        # Build application
        app = ApplicationBuilder().token(self._token).build()
        self._application = app

        # ----- Handlers -----

        async def _check_auth(update: Update) -> bool:
            """Check if user is allowed. Returns True if allowed."""
            if not self._allowed_users:
                return True  # No allowlist = allow all
            user = update.effective_user
            if user is None:
                return False
            user_id_str = str(user.id)
            if user_id_str == str(getattr(config, "telegram_owner_id", "")):
                return True
            for allowed in self._allowed_users:
                if allowed.strip() == user_id_str:
                    return True
            return False

        async def _start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            """Handle /start command."""
            if not await _check_auth(update):
                await update.message.reply_text("⛔ You are not authorized to use this bot.")
                return
            await update.message.reply_text(
                "👋 Hey! I'm **EchoSpeak** — your AI companion.\n\n"
                "Send me any message and I'll respond through my full agent pipeline.\n\n"
                "Commands:\n"
                "/status — Check my status\n"
                "/help — Show this message",
                parse_mode="Markdown",
            )

        async def _status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            """Handle /status command."""
            if not await _check_auth(update):
                return
            from config import config
            provider = getattr(config, "provider", "unknown")
            model = getattr(config, "model", "unknown")
            tools_count = len(self._agent.tools) if hasattr(self._agent, "tools") else 0
            await update.message.reply_text(
                f"🟢 **EchoSpeak Online**\n"
                f"Provider: `{provider}`\n"
                f"Model: `{model}`\n"
                f"Tools: {tools_count}\n"
                f"Telegram: ✅ Connected",
                parse_mode="Markdown",
            )

        async def _help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            """Handle /help command."""
            if not await _check_auth(update):
                return
            await update.message.reply_text(
                "🔧 **EchoSpeak Telegram Bot**\n\n"
                "Just send me any message — I process it through my full "
                "agent pipeline with all tools, memory, and skills.\n\n"
                "I can:\n"
                "• Search the web\n"
                "• Read/write files\n"
                "• Control Discord\n"
                "• Read/send emails\n"
                "• Run terminal commands\n"
                "• Remember things about you\n"
                "• And more!\n\n"
                "Commands: /start, /status, /help",
                parse_mode="Markdown",
            )

        async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            """Handle incoming text messages — route through agent pipeline."""
            if not await _check_auth(update):
                await update.message.reply_text("⛔ Not authorized.")
                return

            text = (update.message.text or "").strip()
            if not text:
                return

            user = update.effective_user
            username = user.username or str(user.id) if user else "telegram_user"
            logger.info(f"Telegram [{username}]: {text[:80]}...")

            # Send typing indicator
            await update.message.chat.send_action("typing")

            try:
                response_text, _ = self._agent.process_query(
                    text,
                    source="telegram",
                    thread_id=f"telegram_{update.effective_chat.id}_{user.id}",
                    # Stable authenticated IDs; allow-list membership is not owner authority.
                    caller_role=("owner" if str(user.id) == str(getattr(config, "telegram_owner_id", "")) and update.effective_chat.type == "private"
                                 else "trusted" if str(user.id) in self._allowed_users else "public"),
                )
            except Exception as exc:
                logger.error(f"Telegram agent error: {exc}")
                await update.message.reply_text(
                    f"❌ Agent error: {str(exc)[:200]}"
                )
                return

            if not response_text or not response_text.strip():
                response_text = "_(No response generated)_"

            # Telegram has a 4096 char limit per message
            if len(response_text) > 4000:
                # Split into chunks
                chunks = [response_text[i:i+4000] for i in range(0, len(response_text), 4000)]
                for chunk in chunks:
                    await update.message.reply_text(chunk)
            else:
                await update.message.reply_text(response_text)

        # Register handlers
        app.add_handler(CommandHandler("start", _start_cmd))
        app.add_handler(CommandHandler("status", _status_cmd))
        app.add_handler(CommandHandler("help", _help_cmd))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))

        # Run polling
        logger.info(f"Telegram bot polling started (allowed_users={self._allowed_users or 'ALL'})")
        try:
            self._loop.run_until_complete(app.initialize())
            self._loop.run_until_complete(app.start())
            self._loop.run_until_complete(
                app.updater.start_polling(drop_pending_updates=True)
            )
            # Keep running until stopped
            while self._running:
                self._loop.run_until_complete(asyncio.sleep(1))
        except Exception as exc:
            logger.error(f"Telegram bot error: {exc}")
        finally:
            try:
                self._loop.run_until_complete(app.updater.stop())
                self._loop.run_until_complete(app.stop())
                self._loop.run_until_complete(app.shutdown())
            except Exception:
                pass
            self._running = False
            logger.info("Telegram bot polling stopped")
