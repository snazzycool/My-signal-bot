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

# States & Lists
MENU, SELECT_PAIR, SELECT_TF, SELECT_RISK, AUTO_RISK, AUTO_ACTIVE = range(6)
PAIRS = ["EUR/USD", "GBP/USD", "BTC/USD", "ETH/USD", "XAU/USD", "AUD/USD", "USD/JPY", "USD/CAD", "GBP/JPY", "BNB/USD"]
TIMEFRAMES = ["1 minute", "5 minutes", "15 minutes", "1 hour"]
RISKS = ["Low Risk", "Minimum Risk", "High Risk"]

# --- DATABASE LOGIC (SAFE PATH + ADVANCED TRACKING) ---
def get_db_path():
    if os.path.exists("/data") or os.access("/", os.W_OK):
        try:
            if not os.path.exists("/data"): os.makedirs("/data")
            return "/data/history.db"
        except: return "history.db"
    return "history.db"

DB_PATH = get_db_path()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    # Ensure table has columns for Win/Loss tracking
    conn.execute('''CREATE TABLE IF NOT EXISTS trades 
                    (pair TEXT, risk TEXT, entry REAL, sl REAL, tp REAL, outcome TEXT, time TEXT)''')
    conn.close()

def log_trade(pair, risk, entry, sl, tp, outcome):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?)", 
                     (pair, risk, entry, sl, tp, outcome, datetime.now().strftime("%Y-%m-%d %H:%M")))

# --- STRATEGY ENGINE (DYNAMIC RR + SNIPER) ---
def analyze_market(symbol, tf, risk_level):
    try:
        api_tf = "1min" if "1" in tf else "5min" if "5" in tf else "15min" if "15" in tf else "1h"
        ts = td.time_series(symbol=symbol, interval=api_tf, outputsize=100).as_pandas()
        curr = ts['close'].iloc[-1]
        
        # Dynamic Market Structure
        recent_low = ts['low'].iloc[-25:].min()
        recent_high = ts['high'].iloc[-25:].max()
        
        # Dynamic SL/TP Math
        sl = recent_low - (curr * 0.0003)
        tp = recent_high * 0.9998
        
        risk_val = abs(curr - sl)
        reward_val = abs(tp - curr)
        rr = reward_val / risk_val if risk_val > 0 else 0

        # Strategy Filters
        ema_200 = ts['close'].ewm(span=200).mean().iloc[-1]
        is_bullish = curr > ema_200

        # LOW RISK (ICT)
        if risk_level == "Low Risk":
            grab = ts['low'].iloc[-1] < ts['low'].iloc[-15:-1].min() and curr > ts['low'].iloc[-15:-1].min()
            fvg = ts['low'].iloc[-1] > ts['high'].iloc[-3]
            if grab and fvg and is_bullish and rr >= 1.2:
                return f"🛡️ **BUY NOW (Low Risk)**\nEntry: {curr}\nSL: {sl:.5f}\nTP: {tp:.5f}\nRR: 1:{rr:.1f}", curr, sl, tp

        # MINIMUM RISK (Trend + S/R)
        elif risk_level == "Minimum Risk":
            if curr <= recent_low * 1.002 and is_bullish and rr >= 1.2:
                return f"⚖️ **BUY NOW (Min Risk)**\nEntry: {curr}\nSL: {sl:.5f}\nTP: {tp:.5f}\nRR: 1:{rr:.1f}", curr, sl, tp

        # HIGH RISK (Aggressive S/R)
        elif risk_level == "High Risk":
            if curr <= recent_low * 1.002 and rr >= 1.5:
                return f"🔥 **BUY NOW (High Risk)**\nEntry: {curr}\nSL: {sl:.5f}\nTP: {tp:.5f}\nRR: 1:{rr:.1f}", curr, sl, tp
        
        return None, None, None, None
    except: return None, None, None, None

# --- BACKGROUND TASKS ---
async def verify_trades(context: ContextTypes.DEFAULT_TYPE):
    """Checks 'Pending' trades to see if they hit TP or SL."""
    with sqlite3.connect(DB_PATH) as conn:
        pending = conn.execute("SELECT rowid, pair, sl, tp FROM trades WHERE outcome = 'Pending'").fetchall()
    for rowid, pair, sl, tp in pending:
        try:
            price = td.time_series(symbol=pair, interval="1min", outputsize=1).as_pandas()['close'].iloc[-1]
            outcome = None
            if price >= tp: outcome = "✅ WIN"
            elif price <= sl: outcome = "❌ LOSS"
            if outcome:
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute("UPDATE trades SET outcome = ? WHERE rowid = ?", (outcome, rowid))
        except: continue

async def auto_task(context: ContextTypes.DEFAULT_TYPE):
    """Scans markets automatically."""
    for p in PAIRS[:5]:
        msg, entry, sl, tp = analyze_market(p, "1h", context.job.data)
        if msg:
            log_trade(p, context.job.data, entry, sl, tp, "Pending")
            await context.bot.send_message(context.job.chat_id, msg, parse_mode="Markdown")
        await asyncio.sleep(15)

# --- INTERFACE HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [["🎯 Get Signal", "🤖 Auto Signal"], ["📜 History", "⚙️ Settings"], ["❓ Help"]]
    await update.message.reply_text("MAIN MENU", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
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
    msg, entry, sl, tp = analyze_market(pair, tf, risk)
    if msg:
        log_trade(pair, risk, entry, sl, tp, "Pending")
        await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text("⚠️ No setup found for immediate entry.")
    return await start(update, context)

async def auto_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [RISKS, ["🏠 Main Menu"]]
    await update.message.reply_text("AUTO MODE: Select Risk", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return AUTO_RISK

async def auto_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🏠 Main Menu": return await start(update, context)
    risk = update.message.text
    await update.message.reply_text(f"🤖 AUTO SIGNAL ACTIVE ({risk})", reply_markup=ReplyKeyboardMarkup([["🛑 STOP AUTO SIGNAL"]], resize_keyboard=True))
    context.job_queue.run_repeating(auto_task, interval=600, chat_id=update.effective_chat.id, name=str(update.effective_chat.id), data=risk)
    return AUTO_ACTIVE

async def auto_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for j in context.job_queue.get_jobs_by_name(str(update.effective_chat.id)): j.schedule_removal()
    await update.message.reply_text("Auto Signal Stopped.")
    return await start(update, context)

async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT pair, outcome, time FROM trades ORDER BY time DESC LIMIT 5").fetchall()
    txt = "📜 **HISTORY**\n\n" + "\n".join([f"• {r[0]} | {r[1]} ({r[2]})" for r in rows]) if rows else "Empty."
    await update.message.reply_text(txt, parse_mode="Markdown")
    return MENU

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❓ **HELP**\nLow Risk: ICT Sweeps + FVG\nHigh Risk: S/R Retests\nAll signals show current Entry, SL, and TP.", parse_mode="Markdown")
    return MENU

# --- MAIN RUNNER ---
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Background Job: Verify Win/Loss
    app.job_queue.run_repeating(verify_trades, interval=1800, first=10)
    
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

if __name__ == "__main__":
    main()
