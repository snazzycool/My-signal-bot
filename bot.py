import asyncio
import logging
import os
from datetime import datetime

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

from config import BOT_TOKEN, PAIRS
from database import Database
from market_data import MarketData
from strategy import StrategyEngine
from utils import format_signal_message

logger = logging.getLogger(__name__)

MAIN_MENU = 0

# -----------------------------------------------------------------------
# FIX: Load the admin/owner chat ID from the environment variable.
# Only messages from this ID will register the chat and receive signals.
# Set ADMIN_CHAT_ID in your Render environment variables.
# To find your chat ID: message @userinfobot on Telegram.
# -----------------------------------------------------------------------
_ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))


class TradingBot:

    def __init__(
        self,
        config,
        db: Database,
        market_data: MarketData,
        strategy: StrategyEngine,
    ):
        self.config = config
        self.db = db
        self.market_data = market_data
        self.strategy = strategy
        self.app = None
        self.scanning_running = False
        self.start_time = datetime.utcnow()
        self.last_scan_time = None

    # ------------------------------------------------------------------
    # /start command — shows the main menu
    # ------------------------------------------------------------------

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # FIX: Register ONLY the authorised admin chat, not everyone
        if _ADMIN_CHAT_ID and update.effective_chat.id != _ADMIN_CHAT_ID:
            await update.message.reply_text(
                "⛔ Unauthorised. This is a private bot."
            )
            return ConversationHandler.END

        # Persist the admin chat so signals can be delivered after restart
        await self.db.add_chat(update.effective_chat.id)

        keyboard = [
            [KeyboardButton("▶️ Start Auto Signals")],
            [KeyboardButton("⛔ Stop Auto Signals")],
            [KeyboardButton("📜 View History")],
            [KeyboardButton("📊 Performance")],
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text(
            "🤖 *Trading Bot*\n\nChoose an option:",
            reply_markup=reply_markup,
            parse_mode='Markdown',
        )
        return MAIN_MENU

    # ------------------------------------------------------------------
    # Menu dispatcher
    # ------------------------------------------------------------------

    async def handle_menu(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        text = update.message.text
        if text == "▶️ Start Auto Signals":
            return await self.start_auto(update, context)
        elif text == "⛔ Stop Auto Signals":
            return await self.stop_auto(update, context)
        elif text == "📜 View History":
            return await self.view_history(update, context)
        elif text == "📊 Performance":
            return await self.view_performance(update, context)
        else:
            await update.message.reply_text("Use the menu buttons.")
            return MAIN_MENU

    # ------------------------------------------------------------------
    # Auto signal controls
    # ------------------------------------------------------------------

    async def start_auto(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        if self.scanning_running:
            await update.message.reply_text("Auto signals already running.")
            return MAIN_MENU

        self.scanning_running = True

        context.job_queue.run_repeating(
            self.scan_markets,
            interval=300,
            first=10,
            name="scan_job",
        )
        context.job_queue.run_repeating(
            self.verify_trades,
            interval=1800,
            first=30,
            name="verify_job",
        )

        await update.message.reply_text(
            "✅ Auto signals started. Scanning every 5 minutes."
        )
        return MAIN_MENU

    async def stop_auto(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        self.scanning_running = False

        for job in context.job_queue.jobs():
            if job.name in ("scan_job", "verify_job"):
                job.schedule_removal()

        await update.message.reply_text("⛔ Auto signals stopped.")
        return MAIN_MENU

    # ------------------------------------------------------------------
    # Background job: market scanner
    # ------------------------------------------------------------------

    async def scan_markets(self, context: ContextTypes.DEFAULT_TYPE):
        if not self.scanning_running:
            return

        self.last_scan_time = datetime.utcnow()
        tasks = [
            self.market_data.fetch_multitimeframe(pair)
            for pair in self.config.PAIRS
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for pair, data in zip(self.config.PAIRS, results):
            if isinstance(data, Exception) or data["htf"] is None or data["entry"] is None:
                logger.warning(f"Skipping {pair} due to data error")
                continue

            signal = await self.strategy.generate_signal(
                pair, data["htf"], data["entry"]
            )
            if signal:
                trade_id = await self.db.log_trade(
                    pair,
                    signal['direction'],
                    signal['entry'],
                    signal['sl'],
                    signal['tp'],
                    signal['partial_tp'],
                    signal['score'],
                )
                if trade_id > 0:
                    active_chats = await self._get_active_chats()
                    for chat_id in active_chats:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=format_signal_message(signal, pair),
                            parse_mode='Markdown',
                        )
                    logger.info(
                        f"Signal sent for {pair} (score={signal['score']})"
                    )
                else:
                    logger.error(f"Failed to log trade for {pair}")

    # ------------------------------------------------------------------
    # Background job: trade outcome verifier
    # ------------------------------------------------------------------

    async def verify_trades(self, context: ContextTypes.DEFAULT_TYPE):
        trades = await self.db.get_pending_trades()

        for trade in trades:
            try:
                price_data = await self.market_data.fetch_ohlcv(
                    trade['pair'], "1min", output_size=1
                )
                if price_data is None or price_data.empty:
                    continue

                current_price = price_data['close'].iloc[-1]

                # Partial TP notification
                if (
                    not trade['partial_notified']
                    and trade['partial_tp'] is not None
                ):
                    partial_hit = (
                        trade['direction'] == 'BUY'
                        and current_price >= trade['partial_tp']
                    ) or (
                        trade['direction'] == 'SELL'
                        and current_price <= trade['partial_tp']
                    )
                    if partial_hit:
                        await self.db.mark_partial_notified(trade['id'])
                        active_chats = await self._get_active_chats()
                        for chat_id in active_chats:
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=(
                                    f"🎯 *Partial TP hit* for {trade['pair']} "
                                    f"({trade['direction']}) at "
                                    f"{trade['partial_tp']:.5f}\n"
                                    f"1:1 RR achieved."
                                ),
                                parse_mode='Markdown',
                            )

                # Full TP / SL check
                if trade['direction'] == 'BUY':
                    if current_price >= trade['tp']:
                        outcome = 'WIN'
                    elif current_price <= trade['sl']:
                        outcome = 'LOSS'
                    else:
                        continue
                else:
                    if current_price <= trade['tp']:
                        outcome = 'WIN'
                    elif current_price >= trade['sl']:
                        outcome = 'LOSS'
                    else:
                        continue

                await self.db.update_trade_outcome(trade['id'], outcome)
                active_chats = await self._get_active_chats()
                for chat_id in active_chats:
                    icon = "✅" if outcome == "WIN" else "❌"
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"{icon} Trade *{outcome}* — "
                            f"{trade['pair']} ({trade['direction']})"
                        ),
                        parse_mode='Markdown',
                    )
                logger.info(f"Trade {trade['id']} marked as {outcome}")

            except Exception as e:
                logger.error(f"Error verifying trade {trade['id']}: {e}")

    # ------------------------------------------------------------------
    # History & performance
    # ------------------------------------------------------------------

    async def view_history(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        trades = await self.db.get_recent_trades(limit=10)
        if not trades:
            await update.message.reply_text("No trades yet.")
        else:
            lines = [
                f"{t['timestamp'][:16]} | {t['pair']} | "
                f"{t['direction']} | {t['status']} | Score:{t['score']}"
                for t in trades
            ]
            await update.message.reply_text(
                "📜 *Recent Trades*\n\n" + "\n".join(lines),
                parse_mode='Markdown',
            )
        return MAIN_MENU

    async def view_performance(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        total, wins, losses, win_rate = await self.db.get_performance()
        pair_perf = await self.db.get_pair_performance()

        msg = (
            f"📊 *Performance*\n"
            f"Total Trades: {total}\n"
            f"Wins: {wins}\n"
            f"Losses: {losses}\n"
            f"Win Rate: {win_rate:.1f}%\n\n"
            f"*Per Pair:*\n"
        )
        if pair_perf:
            for pair, (w, l) in pair_perf.items():
                total_pair = w + l
                rate = (w / total_pair * 100) if total_pair > 0 else 0
                msg += f"{pair}: {w}W/{l}L ({rate:.1f}%)\n"
        else:
            msg += "No completed trades yet."

        await update.message.reply_text(msg, parse_mode='Markdown')
        return MAIN_MENU

    # ------------------------------------------------------------------
    # /status command
    # ------------------------------------------------------------------

    async def status_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        uptime = datetime.utcnow() - self.start_time
        active_chats = len(await self._get_active_chats())
        last_scan = (
            self.last_scan_time.isoformat() if self.last_scan_time else "Never"
        )
        kill_zone_active = "Yes" if self.strategy._is_kill_zone() else "No"

        msg = (
            f"🤖 *Bot Status*\n"
            f"Uptime: {str(uptime).split('.')[0]}\n"
            f"Active chats: {active_chats}\n"
            f"Auto scanning: {'ON' if self.scanning_running else 'OFF'}\n"
            f"Last scan: {last_scan}\n"
            f"Kill zone active: {kill_zone_active}\n"
            f"Session filter: {'ON' if self.config.ENABLE_SESSION_FILTER else 'OFF'}"
        )
        await update.message.reply_text(msg, parse_mode='Markdown')

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_active_chats(self) -> list:
        return await self.db.get_all_chats()

    async def fallback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        await update.message.reply_text("Use the menu buttons.")
        return MAIN_MENU

    # ------------------------------------------------------------------
    # Application bootstrap
    # ------------------------------------------------------------------

    async def run_async(self):
        self.app = Application.builder().token(BOT_TOKEN).build()

        # FIX: Removed the catch-all register_chat handler that was silently
        # adding every random user to the chats table. Chat registration
        # now happens only inside the /start handler after the admin check.

        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("start", self.start)],
            states={
                MAIN_MENU: [
                    MessageHandler(
                        filters.Regex(
                            r"^(▶️ Start Auto Signals|⛔ Stop Auto Signals"
                            r"|📜 View History|📊 Performance)$"
                        ),
                        self.handle_menu,
                    ),
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND, self.fallback
                    ),
                ]
            },
            fallbacks=[CommandHandler("start", self.start)],
        )

        self.app.add_handler(conv_handler)
        self.app.add_handler(CommandHandler("status", self.status_command))

        logger.info("Initializing application...")
        await self.app.initialize()
        await self.app.start()

        logger.info("Waiting 5 seconds for previous instance to terminate...")
        await asyncio.sleep(5)

        logger.info("Starting polling...")
        await self.app.updater.start_polling()

        # Keep running until cancelled
        await asyncio.Event().wait()
