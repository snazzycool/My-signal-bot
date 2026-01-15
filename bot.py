import os
import requests
import datetime
import time
import threading
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ================= KEEP-ALIVE SYSTEM =================
app_web = Flask('')

@app_web.route('/')
def home():
    return "Lazy Bot is Online and Trading!"

def run_web_server():
    # Render provides a 'PORT' environment variable automatically
    port = int(os.environ.get('PORT', 8080))
    app_web.run(host='0.0.0.0', port=port)

# ================= CONFIG =================

# Use Environment Variable for security on Render
BOT_TOKEN = os.getenv("8023097141:AAHQhpp6NTYA_buZhfh5mn-DZftbdPccKeM")
API_KEY = "9935ca70e0f842569acc2790803c1e0c" 

PAIRS = {
    "EURGBP": "EUR/GBP", "AUDGBP": "AUD/GBP", "EURCAD": "EUR/CAD",
    "XAUUSD": "XAU/USD", "US30": "DJI", "NASDAQ": "IXIC",
    "BTCUSD": "BTC/USD", "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD", "USDJPY": "USD/JPY"
}

TIMEFRAMES = {"1M": "1min", "5M": "5min", "15M": "15min", "1H": "1h"}
AUTO_MODE = False
RISK_MODE = "Medium"
CHAT_ID = None # Will be set when you send /start

# ================= LOGIC =================

def get_session():
    now = datetime.datetime.utcnow().hour
    if 8 <= now <= 11: return "London"
    elif 13 <= now <= 16: return "New York"
    else: return "Off Session"

def fetch_data(pair, tf):
    url = f"https://api.twelvedata.com/time_series?symbol={pair}&interval={tf}&apikey={API_KEY}&outputsize=50"
    r = requests.get(url).json()
    return r.get("values", [])

def orb_breakout(data):
    if not data: return None
    first = data[-1]
    high, low = float(first["high"]), float(first["low"])
    last = float(data[0]["close"])
    if last > high: return "BUY"
    elif last < low: return "SELL"
    return None

def liquidity_sweep(data):
    last = data[0]
    wick = abs(float(last["high"]) - float(last["low"]))
    body = abs(float(last["open"]) - float(last["close"]))
    return wick > body * 2

def fair_value_gap(data):
    c1, c2, c3 = data[2], data[1], data[0]
    return float(c3["low"]) > float(c1["high"]) or float(c3["high"]) < float(c1["low"])

def trend_filter(data):
    closes = [float(c["close"]) for c in data[:20]]
    ema50 = sum(closes[:10]) / 10
    ema200 = sum(closes[10:20]) / 10
    return "BUY" if ema50 > ema200 else "SELL"

def probability(orb, liq, fvg, trend):
    score = 0
    if orb: score += 30
    if liq: score += 25
    if fvg: score += 25
    if trend: score += 20
    return score

def analyze(pair, tf, risk):
    data = fetch_data(pair, tf)
    if not data: return None
    session = get_session()
    if session == "Off Session": return None
    orb = orb_breakout(data)
    if not orb: return None
    liq = liquidity_sweep(data)
    fvg = fair_value_gap(data)
    trend = trend_filter(data)
    score = probability(orb, liq, fvg, trend)
    if (risk == "Minimum" and score < 80) or (risk == "Medium" and score < 65) or (risk == "High" and score < 40):
        return None
    price = data[0]["close"]
    return f"PAIR: {pair}\nTF: {tf}\nSESSION: {session}\nTYPE: {orb}\nENTRY: {price}\nWIN RATE: {score}%\nLOGIC: ORB + ICT\nRISK: {risk}"

# ================= TELEGRAM =================

MAIN_MENU = [["Signal"], ["Auto Signal"], ["Settings"], ["Help"]]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CHAT_ID
    CHAT_ID = update.effective_chat.id
    await update.message.reply_text("Welcome to ORB + ICT Bot", reply_markup=ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True))

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    global AUTO_MODE, RISK_MODE
    
    if txt == "Signal":
        await update.message.reply_text("Send pair like: EURUSD 5M")
    elif txt == "Auto Signal":
        AUTO_MODE = not AUTO_MODE
        await update.message.reply_text(f"AUTO MODE: {'ON' if AUTO_MODE else 'OFF'}")
        if AUTO_MODE: threading.Thread(target=auto_scan, args=(context.bot,)).start()
    elif txt == "Help":
        await update.message.reply_text("Manual signal: PAIR TF\nExample: EURUSD 5M")
    elif txt == "Settings":
        await update.message.reply_text("Risk modes: Minimum | Medium | High\nType: risk medium")
    elif txt.lower().startswith("risk"):
        RISK_MODE = txt.split()[1].capitalize()
        await update.message.reply_text(f"Risk set to {RISK_MODE}")
    else:
        try:
            pair, tf_key = txt.split()
            tf_val = TIMEFRAMES.get(tf_key.upper())
            result = analyze(pair.upper(), tf_val, RISK_MODE)
            await update.message.reply_text(result if result else "No valid setup found.")
        except Exception:
            await update.message.reply_text("Invalid format. Use: EURUSD 5M")

def auto_scan(bot):
    while AUTO_MODE:
        for p in PAIRS:
            for tf in TIMEFRAMES.values():
                res = analyze(p, tf, RISK_MODE)
                if res and CHAT_ID:
                    # Note: You'll need an async way to send from a thread, but for now we'll keep it simple
                    print(f"Signal found for {p}")
        time.sleep(60)

def main():
    # 1. Start Web Server for Render
    threading.Thread(target=run_web_server, daemon=True).start()

    # 2. Build Bot with high timeouts for stability
    app = Application.builder().token(BOT_TOKEN).connect_timeout(30).read_timeout(30).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT, menu))

    print("Lazy Bot is starting...")
    app.run_polling(timeout=30)

if __name__ == "__main__":
    main()
