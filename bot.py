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

# --- DATABASE LOGIC ---
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
    conn.execute('''CREATE TABLE IF NOT EXISTS trades 
                    (pair TEXT, risk TEXT, entry REAL, sl REAL, tp REAL, outcome TEXT, time TEXT)''')
    conn.close()

def log_trade(pair, risk, entry, sl, tp, outcome):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?)", 
                     (pair, risk, entry, sl, tp, outcome, datetime.now().strftime("%Y-%m-%d %H:%M")))

# --- STRATEGY ENGINE ---
def analyze_market(symbol, tf, risk_level):
    try:
        api_tf = "1min" if "1" in tf else "5min" if "5" in tf else "15min" if "15" in tf else "1h"
        ts = td.time_series(symbol=symbol, interval=api_tf, outputsize=100).as_pandas()
        curr = ts['close'].iloc[-1]
        
        recent_low = ts['low'].iloc[-25:].min()
        recent_high = ts['high'].iloc[-25:].max()
        
        sl = recent_low - (curr * 0.0003)
        tp = recent_high * 0.9998
        
        risk = abs(curr - sl)
        reward = abs(tp - curr)
        rr = reward / risk if risk > 0 else 0

        if risk_level == "Low Risk":
            grab = ts['low'].iloc[-1] < ts['low'].iloc[-15:-1].min() and curr > ts['low'].iloc[-15:-1].min()
            fvg = ts['low'].iloc[-1] > ts['high'].iloc[-3]
            ema_200 = ts['close'].ewm(span=200).mean().iloc[-1]
            
            if grab and fvg and curr > ema_200 and rr >= 1.2:
                return f"🛡️ **BUY NOW**\nEntry: {curr}\nSL: {sl:.5f}\nTP: {tp:.5f}\nRR: 1:{rr:.1f}", curr, sl, tp

        elif risk_level == "High Risk":
            if curr <= recent_low * 1.002 and rr >= 1.5:
                return f"🔥 **BUY NOW**\nEntry: {curr}\nSL: {sl:.5f}\nTP: {tp:.5f}\nRR: 1:{rr:.1f}", curr, sl, tp
        
        return None, None, None, None
    except: return None, None, None, None

# --- HANDLERS ---
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

# --- MAIN RUNNER ---
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MENU: [MessageHandler(filters.Regex("^🎯 Get Signal$"), gs_pair)],
            SELECT_PAIR: [MessageHandler(filters.TEXT & ~filters.COMMAND, gs_tf)],
            SELECT_TF: [MessageHandler(filters.TEXT & ~filters.COMMAND, gs_risk)],
            SELECT_RISK: [MessageHandler(filters.TEXT & ~filters.COMMAND, gs_final)],
        },
        fallbacks=[CommandHandler("start", start)]
    )
    
    app.add_handler(conv)
    print("Bot is starting polling...")
    app.run_polling() # THIS MUST BE ACTIVE TO STAY ONLINE

if __name__ == "__main__":
    main()
        
