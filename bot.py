import os
import sqlite3
import logging
import asyncio
import pandas as pd
from datetime import datetime
from threading import Thread
from flask import Flask
from twelvedata import TDClient
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, ConversationHandler

# --- 1. CONFIGURATION & RENDER KEEP-ALIVE ---
API_KEY = "9935ca70e0f842569acc2790803c1e0c"
BOT_TOKEN = os.environ.get("BOT_TOKEN")  # Fetches from Render Env Vars

app_flask = Flask('')
@app_flask.route('/')
def home(): return "Bot is running..."

def run_flask():
    app_flask.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))

# --- 2. DATABASE LOGIC (HISTORY) ---
DB_NAME = "trade_history.db"

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS trades 
                        (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                         type TEXT, pair TEXT, risk TEXT, outcome TEXT, time TEXT)''')

def save_to_history(t_type, pair, risk, outcome):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("INSERT INTO trades (type, pair, risk, outcome, time) VALUES (?,?,?,?,?)",
                     (t_type, pair, risk, outcome, datetime.now().strftime("%Y-%m-%d %H:%M")))

# --- 3. TRADING STRATEGY (TWELVE DATA) ---
td = TDClient(apikey=API_KEY)

def analyze_market(symbol, interval, risk_level):
    try:
        ts = td.time_series(symbol=symbol, interval=interval, outputsize=100).as_pandas()
        curr = ts['close'].iloc[-1]
        support, resistance = ts['low'].min(), ts['high'].max()

        # HIGH RISK: Pure Support/Resistance Retest
        if risk_level == "High Risk":
            if curr <= support * 1.002: return f"🔥 BUY {symbol}\nS/R Retest Logic"
            if curr >= resistance * 0.998: return f"🔥 SELL {symbol}\nS/R Retest Logic"
            return None

        # LOW/MIN RISK: EMA + ICT/ORB Confluence
        ema_200 = ts['close'].ewm(span=200).mean().iloc[-1]
        orb_high = ts['high'].iloc[:15].max()
        
        if risk_level == "Low Risk":
            if curr <= support * 1.002 and curr > ema_200 and curr > orb_high:
                return f"🛡️ LOW RISK BUY {symbol}\nFull Confluence Met"
        elif risk_level == "Minimum Risk":
            if curr <= support * 1.002 and (curr > ema_200 or curr > orb_high):
                return f"⚖️ MIN RISK BUY {symbol}\nPartial Confluence Met"
        
        return None
    except Exception: return "Error fetching data."

# --- 4. TELEGRAM BOT LOGIC ---
MENU, SELECT_PAIR, SELECT_TF, SELECT_RISK, AUTO_MODE = range(5)
PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY", "BTC/USD", "ETH/USD", "AUD/USD", "USD/CAD", "NZD/USD", "EUR/JPY", "XAU/USD"]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🎯 Get Signal", callback_data="get_sig"), InlineKeyboardButton("🤖 Auto Signal", callback_data="auto_sig")],
        [InlineKeyboardButton("📜 History", callback_data="hist"), InlineKeyboardButton("⚙️ Settings", callback_data="set")],
        [InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    reply = InlineKeyboardMarkup(keyboard)
    msg = "📈 **MAIN MENU**\nSelect an option to begin:"
    if update.callback_query: await update.callback_query.edit_message_text(msg, reply_markup=reply, parse_mode="Markdown")
    else: await update.message.reply_text(msg, reply_markup=reply, parse_mode="Markdown")
    return MENU

# GET SIGNAL FLOW
async def pair_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    btns = [[InlineKeyboardButton(PAIRS[i], callback_data=f"p_{PAIRS[i]}"), InlineKeyboardButton(PAIRS[i+1], callback_data=f"p_{PAIRS[i+1]}")] for i in range(0, 10, 2)]
    btns.append([InlineKeyboardButton("⬅️ Back", callback_data="main_menu")])
    await update.callback_query.edit_message_text("SELECT PAIR:", reply_markup=InlineKeyboardMarkup(btns))
    return SELECT_PAIR

async def tf_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['p'] = update.callback_query.data.replace("p_", "")
    btns = [[InlineKeyboardButton(t, callback_data=f"t_{t}") for t in ["1min", "5min", "15min", "1h"]]]
    btns.append([InlineKeyboardButton("⬅️ Back", callback_data="get_sig")])
    await update.callback_query.edit_message_text(f"PAIR: {context.user_data['p']}\nCHOOSE TIMEFRAME:", reply_markup=InlineKeyboardMarkup(btns))
    return SELECT_TF

async def risk_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['t'] = update.callback_query.data.replace("t_", "")
    btns = [[InlineKeyboardButton(r, callback_data=f"r_{r}") for r in ["Low Risk", "Minimum Risk", "High Risk"]]]
    btns.append([InlineKeyboardButton("⬅️ Back", callback_data=f"p_{context.user_data['p']}")])
    await update.callback_query.edit_message_text("SELECT RISK:", reply_markup=InlineKeyboardMarkup(btns))
    return SELECT_RISK

async def execute_sig(update: Update, context: ContextTypes.DEFAULT_TYPE):
    risk = update.callback_query.data.replace("r_", "")
    await update.callback_query.edit_message_text("🔍 Scanning... please wait.")
    res = analyze_market(context.user_data['p'], context.user_data['t'], risk)
    
    msg = res if res else "⚠️ Market not stable for this risk. Try later."
    save_to_history("Manual", context.user_data['p'], risk, "Signal Sent" if res else "No Signal")
    
    btns = [[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]]
    await update.callback_query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(btns))
    return MENU

# AUTO SIGNAL FLOW
async def auto_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    btns = [[InlineKeyboardButton(r, callback_data=f"a_{r}") for r in ["Low Risk", "Minimum Risk", "High Risk"]]]
    btns.append([InlineKeyboardButton("⬅️ Back", callback_data="main_menu")])
    await update.callback_query.edit_message_text("🤖 AUTO MODE: Select Risk", reply_markup=InlineKeyboardMarkup(btns))
    return AUTO_MODE

async def auto_active(update: Update, context: ContextTypes.DEFAULT_TYPE):
    risk = update.callback_query.data.replace("a_", "")
    context.job_queue.run_repeating(auto_task, interval=600, chat_id=update.effective_chat.id, name=str(update.effective_chat.id), data=risk)
    btns = [[InlineKeyboardButton("🛑 OFF", callback_data="stop_a")]]
    await update.callback_query.edit_message_text(f"✅ AUTO ACTIVE ({risk})\nStatus: Scanning 10 pairs...", reply_markup=InlineKeyboardMarkup(btns))
    return AUTO_MODE

async def auto_task(context: ContextTypes.DEFAULT_TYPE):
    for p in PAIRS:
        res = analyze_market(p, "1h", context.job.data)
        if res: 
            await context.bot.send_message(context.job.chat_id, res)
            save_to_history("Auto", p, context.job.data, "Signal Sent")
        await asyncio.sleep(10) # API rate limit protection

async def auto_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for j in context.job_queue.get_jobs_by_name(str(update.effective_chat.id)): j.schedule_removal()
    return await start(update, context)

# HISTORY HANDLER
async def history_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with sqlite3.connect(DB_NAME) as conn:
        data = conn.execute("SELECT pair, outcome, time FROM trades ORDER BY id DESC LIMIT 5").fetchall()
    txt = "📜 **HISTORY (Last 5)**\n\n" + "\n".join([f"• {d[0]} | {d[1]} | {d[2]}" for d in data]) if data else "No history yet."
    await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]]), parse_mode="Markdown")
    return MENU

# --- 5. MAIN RUNNER ---
def main():
    init_db()
    Thread(target=run_flask).start() # Keep Render Alive
    
    app = Application.builder().token(BOT_TOKEN).build()
    handler = ConversationHandler(
        entry_points=[CommandHandler("start", start), CallbackQueryHandler(start, pattern="main_menu")],
        states={
            MENU: [CallbackQueryHandler(pair_menu, pattern="get_sig"), CallbackQueryHandler(auto_start, pattern="auto_sig"), CallbackQueryHandler(history_view, pattern="hist")],
            SELECT_PAIR: [CallbackQueryHandler(tf_menu, pattern="p_")],
            SELECT_TF: [CallbackQueryHandler(risk_menu, pattern="tf_")],
            SELECT_RISK: [CallbackQueryHandler(execute_sig, pattern="r_")],
            AUTO_MODE: [CallbackQueryHandler(auto_active, pattern="a_"), CallbackQueryHandler(auto_stop, pattern="stop_a")]
        },
        fallbacks=[CommandHandler("start", start)]
    )
    app.add_handler(handler)
    app.run_polling()

if __name__ == '__main__':
    main()
                    
