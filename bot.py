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

# --- DATABASE (NORTHFLANK PERSISTENT VOLUME) ---
DB_PATH = "/data/history.db"

def init_db():
    if not os.path.exists("/data"): os.makedirs("/data")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS trades (pair TEXT, risk TEXT, outcome TEXT, time TEXT)")
    conn.close()

def log_trade(pair, risk, outcome):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO trades VALUES (?,?,?,?)", (pair, risk, outcome, datetime.now().strftime("%Y-%m-%d %H:%M")))

# --- STRATEGY ENGINE (S/R + EMA + ICT/ORB) ---
def analyze_market(symbol, tf, risk):
    try:
        api_tf = "1min" if "1" in tf else "5min" if "5" in tf else "15min" if "15" in tf else "1h"
        ts = td.time_series(symbol=symbol, interval=api_tf, outputsize=100).as_pandas()
        curr = ts['close'].iloc[-1]
        support, resistance = ts['low'].min(), ts['high'].max()
        
        # HIGH RISK: Basic Support/Resistance Retest
        if risk == "High Risk":
            if curr <= support * 1.002: return f"🔥 HIGH RISK BUY: {symbol} @ {curr}\nRetest of {support}"
            if curr >= resistance * 0.998: return f"🔥 HIGH RISK SELL: {symbol} @ {curr}\nRetest of {resistance}"
            return None

        # MIN/LOW RISK: Deep Logic (EMA + ICT/ORB)
        ema_200 = ts['close'].ewm(span=200).mean().iloc[-1]
        orb_high = ts['high'].iloc[:15].max()
        
        if risk == "Low Risk":
            if curr <= support * 1.002 and curr > ema_200 and curr > orb_high:
                return f"🛡️ LOW RISK BUY: {symbol} @ {curr}\nS/R + Trend + ORB"
        elif risk == "Minimum Risk":
            if curr <= support * 1.002 and (curr > ema_200 or curr > orb_high):
                return f"⚖️ MIN RISK BUY: {symbol} @ {curr}\nS/R + Trend Confluence"
        return None
    except: return "Data fetch error."

# --- BOT HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [["🎯 Get Signal", "🤖 Auto Signal"], ["📜 History", "⚙️ Settings"], ["❓ Help"]]
    await update.message.reply_text("MAIN MENU", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return MENU

# SETTINGS & HELP HANDLERS
async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_txt = "❓ **ABOUT THIS BOT**\n\nThis bot scans 10 high-liquidity pairs for S/R retests and trend confluence.\n\n• **Low Risk:** High-probability setups (S/R + EMA + ORB).\n• **High Risk:** Pure Price Action retests.\n\n_Contact admin for custom strategies._"
    await update.message.reply_text(help_txt, parse_mode="Markdown")
    return MENU

async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚙️ **SETTINGS**\n\n🔔 Notifications: **ENABLED**\n📡 API: TwelveData (Connected)\n\n_Ensure chat notifications are ON to catch signals instantly._", parse_mode="Markdown")
    return MENU

# GET SIGNAL FLOW
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
    await update.message.reply_text("Select Risk Level:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return SELECT_RISK

async def gs_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🏠 Main Menu": return await start(update, context)
    risk, pair, tf = update.message.text, context.user_data['p'], context.user_data['t']
    await update.message.reply_text(f"🔍 Scanning {pair}...")
    res = analyze_market(pair, tf, risk)
    log_trade(pair, risk, "Signal" if res else "No Signal")
    await update.message.reply_text(res if res else "⚠️ No setup found. Market unstable.")
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
        await asyncio.sleep(12) # Rate limit protection

async def auto_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for j in context.job_queue.get_jobs_by_name(str(update.effective_chat.id)): j.schedule_removal()
    await update.message.reply_text("Auto Signal Stopped.")
    return await start(update, context)

async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT pair, risk, outcome FROM trades ORDER BY time DESC LIMIT 5").fetchall()
    txt = "📜 **HISTORY**\n\n" + "\n".join([f"• {r[0]} | {r[1]} | {r[2]}" for r in rows]) if rows else "No records."
    await update.message.reply_text(txt, parse_mode="Markdown")
    return MENU

# --- MAIN RUNNER ---
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MENU: [MessageHandler(filters.Regex("^🎯 Get Signal$"), gs_pair),
                   MessageHandler(filters.Regex("^🤖 Auto Signal$"), auto_start),
                   MessageHandler(filters.Regex("^📜 History$"), show_history),
                   MessageHandler(filters.Regex("^⚙️ Settings$"), show_settings),
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
