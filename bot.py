import os
import sqlite3
import asyncio
import pandas as pd
from datetime import datetime
from twelvedata import TDClient
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    filters, ContextTypes, ConversationHandler
)

# --- CONFIGURATION ---
API_KEY = "9935ca70e0f842569acc2790803c1e0c"
BOT_TOKEN = os.environ.get("BOT_TOKEN") 
td = TDClient(apikey=API_KEY)

# Conversation States
MENU, SELECT_PAIR, SELECT_TF, SELECT_RISK, AUTO_RISK, AUTO_ACTIVE = range(6)

# Data Lists
PAIRS = ["EUR/USD", "GBP/USD", "BTC/USD", "ETH/USD", "XAU/USD", "AUD/USD", "USD/JPY", "USD/CAD", "GBP/JPY", "BNB/USD"]
TIMEFRAMES = ["1 minute", "5 minutes", "15 minutes", "1 hour"]
RISKS = ["Low Risk", "Minimum Risk", "High Risk"]

# --- UPDATED DATABASE LOGIC (SAFE PATH) ---
def get_db_path():
    # Try the persistent volume first, fallback to local if permission is denied
    if os.path.exists("/data") or os.access("/", os.W_OK):
        try:
            if not os.path.exists("/data"): os.makedirs("/data")
            return "/data/history.db"
        except PermissionError:
            return "history.db"
    return "history.db"

DB_PATH = get_db_path()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS trades (pair TEXT, risk TEXT, outcome TEXT, time TEXT)")
    conn.close()

def log_trade(pair, risk, outcome):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO trades VALUES (?,?,?,?)", (pair, risk, outcome, datetime.now().strftime("%Y-%m-%d %H:%M")))

# --- ICT & DEEP LOGIC ENGINE ---
def analyze_market(symbol, tf, risk):
    try:
        api_tf = "1min" if "1" in tf else "5min" if "5" in tf else "15min" if "15" in tf else "1h"
        ts = td.time_series(symbol=symbol, interval=api_tf, outputsize=100).as_pandas()
        curr = ts['close'].iloc[-1]
        support, resistance = ts['low'].min(), ts['high'].max()
        
        # 1. HIGH RISK: Support/Resistance Retest
        if risk == "High Risk":
            if curr <= support * 1.002: return f"🔥 HIGH RISK BUY: {symbol} @ {curr}\nS/R Retest"
            if curr >= resistance * 0.998: return f"🔥 HIGH RISK SELL: {symbol} @ {curr}\nS/R Retest"
            return None

        # 2. TREND FILTER (EMA 200)
        ema_200 = ts['close'].ewm(span=200).mean().iloc[-1]
        is_bullish = curr > ema_200

        # 3. MINIMUM RISK: S/R + Trend
        if risk == "Minimum Risk":
            if curr <= support * 1.002 and is_bullish:
                return f"⚖️ MIN RISK BUY: {symbol} @ {curr}\nS/R + Trend Aligned"
            return None

        # 4. LOW RISK: Liquidity Grab + FVG (The Sniper)
        if risk == "Low Risk":
            swing_low = ts['low'].iloc[-16:-1].min()
            grab_success = ts['low'].iloc[-1] < swing_low and curr > swing_low
            fvg_gap = ts['low'].iloc[-1] > ts['high'].iloc[-3]
            
            if grab_success and fvg_gap and is_bullish:
                return f"🛡️ LOW RISK BUY: {symbol} @ {curr}\nICT: Liquidity Grab + FVG + Trend"

        return None
    except Exception as e:
        return None

# --- INTERFACE HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [["🎯 Get Signal", "🤖 Auto Signal"], ["📜 History", "⚙️ Settings"], ["❓ Help"]]
    await update.message.reply_text("MAIN MENU", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return MENU

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❓ **HELP**\nLow Risk uses ICT Liquidity Sweeps and FVGs.\nHigh Risk uses S/R Retests.", parse_mode="Markdown")
    return MENU

async def gs_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [PAIRS[i:i+2] for i in range(0, 10, 2)] + [["🏠 Main Menu"]]
    await update.message.reply_text("Select Pair:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return SELECT_PAIR

async def gs_tf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🏠 Main Menu": return await start(update, context)
    context.user_data['p'] = update.message.text
    kb = [TIMEFRAMES[0:2], TIMEFRAMES[2:4], ["🏠 Main Menu"]]
    await update.message.reply_text("Select Timeframe:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return SELECT_TF

async def gs_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🏠 Main Menu": return await start(update, context)
    context.user_data['t'] = update.message.text
    kb = [RISKS, ["🏠 Main Menu"]]
    await update.message.reply_text("Select Risk:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return SELECT_RISK

async def gs_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🏠 Main Menu": return await start(update, context)
    risk, pair, tf = update.message.text, context.user_data['p'], context.user_data['t']
    await update.message.reply_text(f"🔍 Deep Scanning {pair}...")
    res = analyze_market(pair, tf, risk)
    log_trade(pair, risk, "Signal" if res else "No Signal")
    await update.message.reply_text(res if res else "⚠️ No setup found for these strict parameters.")
    return await start(update, context)

# AUTO SIGNAL FLOW
async def auto_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [RISKS, ["🏠 Main Menu"]]
    await update.message.reply_text("AUTO MODE: Select Risk", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return AUTO_RISK

async def auto_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🏠 Main Menu": return await start(update, context)
    risk = update.message.text
    kb = [["🛑 STOP AUTO SIGNAL"]]
    await update.message.reply_text(f"🤖 AUTO SIGNAL ACTIVE ({risk})", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    context.job_queue.run_repeating(auto_task, interval=300, chat_id=update.effective_chat.id, name=str(update.effective_chat.id), data=risk)
    return AUTO_ACTIVE

async def auto_task(context: ContextTypes.DEFAULT_TYPE):
    for p in PAIRS[:5]:
        res = analyze_market(p, "1h", context.job.data)
        if res: await context.bot.send_message(context.job.chat_id, res)
        await asyncio.sleep(12)

async def auto_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for j in context.job_queue.get_jobs_by_name(str(update.effective_chat.id)): j.schedule_removal()
    await update.message.reply_text("Auto Signal Stopped.")
    return await start(update, context)

async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT pair, risk, outcome FROM trades ORDER BY time DESC LIMIT 5").fetchall()
    txt = "📜 **HISTORY**\n\n" + "\n".join([f"• {r[0]} | {r[1]} | {r[2]}" for r in rows]) if rows else "Empty."
    await update.message.reply_text(txt, parse_mode="Markdown")
    return MENU

# --- RUNNER ---
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MENU: [MessageHandler(filters.Regex("^🎯 Get Signal$"), gs_pair),
                   MessageHandler(filters.Regex("^🤖 Auto Signal$"), auto_start),
                   MessageHandler(filters.Regex("^📜 History$"), show_history),
                   MessageHandler(filters.Regex("^❓ Help$"), show_help)],
            SELECT_PAIR: [MessageHandler(filters.TEXT & ~filters.COMMAND, gs_tf)],
            SELECT_TF: [MessageHandler(filters.TEXT & ~filters.COMMAND, gs_risk)],
            SELECT_RISK: [MessageHandler(filters.TEXT & ~filters.COMMAND, gs_final)],
            AUTO_RISK: [MessageHandler(filters.TEXT & ~filters.COMMAND, auto_run)],
            AUTO_ACTIVE: [MessageHandler(filters.Regex("^🛑 STOP AUTO SIGNAL$"), auto_stop)]
        },
        fallbacks=[CommandHandler("start", start)]
    )
    app.add_handler(conv)
    app.run_polling()

if __name__ == "__main__": main()
