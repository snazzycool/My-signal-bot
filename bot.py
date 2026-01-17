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
PAIRS = ["EUR/USD", "GBP/USD", "BTC/USD", "ETH/USD", "XAU/USD", "AUD/USD", "USD/JPY", "USD/CAD", "GBP/JPY", "BNB/USD"]
TIMEFRAMES = ["1 minute", "5 minutes", "15 minutes", "1 hour"]
RISKS = ["Low Risk", "Minimum Risk", "High Risk"]

# --- DATABASE LOGIC (ADVANCED TRACKING) ---
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
    # Updated table to include Entry, SL, TP, and dynamic Outcome
    conn.execute('''CREATE TABLE IF NOT EXISTS trades 
                    (pair TEXT, risk TEXT, entry REAL, sl REAL, tp REAL, outcome TEXT, time TEXT)''')
    conn.close()

def log_trade(pair, risk, entry, sl, tp, outcome):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?)", 
                     (pair, risk, entry, sl, tp, outcome, datetime.now().strftime("%Y-%m-%d %H:%M")))

# --- DYNAMIC MARKET SNIPER ENGINE ---
def analyze_market(symbol, tf, risk_level):
    try:
        api_tf = "1min" if "1" in tf else "5min" if "5" in tf else "15min" if "15" in tf else "1h"
        ts = td.time_series(symbol=symbol, interval=api_tf, outputsize=100).as_pandas()
        curr = ts['close'].iloc[-1]
        
        # 1. DYNAMIC LEVELS (S/R and Liquidity Swings)
        recent_low = ts['low'].iloc[-25:].min()
        recent_high = ts['high'].iloc[-25:].max()
        
        # Set dynamic TP/SL based on structure
        sl = recent_low - (curr * 0.0003) # SL below sweep low
        tp = recent_high * 0.9998        # TP before major resistance
        
        # Calculate RR Ratio
        risk = abs(curr - sl)
        reward = abs(tp - curr)
        rr = reward / risk if risk > 0 else 0

        # 2. LOW RISK LOGIC (ICT: Liquidity + FVG)
        if risk_level == "Low Risk":
            grab = ts['low'].iloc[-1] < ts['low'].iloc[-15:-1].min() and curr > ts['low'].iloc[-15:-1].min()
            fvg = ts['low'].iloc[-1] > ts['high'].iloc[-3]
            ema_200 = ts['close'].ewm(span=200).mean().iloc[-1]
            
            if grab and fvg and curr > ema_200 and rr >= 1.2:
                return f"🛡️ **BUY NOW**\nEntry: {curr}\nSL: {sl:.5f}\nTP: {tp:.5f}\nRR: 1:{rr:.1f}", curr, sl, tp

        # 3. HIGH RISK LOGIC (Aggressive S/R)
        elif risk_level == "High Risk":
            if curr <= recent_low * 1.002 and rr >= 1.5:
                return f"🔥 **BUY NOW**\nEntry: {curr}\nSL: {sl:.5f}\nTP: {tp:.5f}\nRR: 1:{rr:.1f}", curr, sl, tp
        
        return None, None, None, None
    except: return None, None, None, None

# --- TRADE VERIFIER (BACKGROUND JOB) ---
async def verify_trades(context: ContextTypes.DEFAULT_TYPE):
    with sqlite3.connect(DB_PATH) as conn:
        pending = conn.execute("SELECT rowid, pair, sl, tp FROM trades WHERE outcome = 'Pending'").fetchall()
    
    for rowid, pair, sl, tp in pending:
        try:
            # Fetch latest 1min price to check if hit SL/TP
            price = td.time_series(symbol=pair, interval="1min", outputsize=1).as_pandas()['close'].iloc[-1]
            outcome = None
            if price >= tp: outcome = "✅ WIN"
            elif price <= sl: outcome = "❌ LOSS"
            
            if outcome:
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute("UPDATE trades SET outcome = ? WHERE rowid = ?", (outcome, rowid))
        except: continue

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [["🎯 Get Signal", "🤖 Auto Signal"], ["📜 History", "⚙️ Settings"], ["❓ Help"]]
    await update.message.reply_text("MAIN MENU", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return MENU

async def gs_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🏠 Main Menu": return await start(update, context)
    risk, pair, tf = update.message.text, context.user_data['p'], context.user_data['t']
    await update.message.reply_text(f"🔍 Deep Scanning {pair}...")
    msg, entry, sl, tp = analyze_market(pair, tf, risk)
    
    if msg:
        log_trade(pair, risk, entry, sl, tp, "Pending") # Log as Pending for the Verifier
        await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text("⚠️ No setup found for immediate entry. Try again in 30 mins.")
    return await start(update, context)

async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT pair, outcome, time FROM trades ORDER BY time DESC LIMIT 5").fetchall()
    txt = "📜 **SIGNAL HISTORY**\n\n" + "\n".join([f"• {r[0]} | {r[1]} ({r[2]})" for r in rows]) if rows else "No history."
    await update.message.reply_text(txt, parse_mode="Markdown")
    return MENU

# (Standard Pair/TF/Risk/Auto Handlers same as before)
# ...

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Start the Verifier (Runs every 30 mins)
    app.job_queue.run_repeating(verify_trades, interval=1800, first=10)
    
    # ... (Setup ConversationHandler and App as before)
    # app.run_polling()
