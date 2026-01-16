import os
import requests
import datetime
import asyncio
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ================= KEEP ALIVE =================
web = Flask("")

@web.route("/")
def home():
    return "Lazy ORB + ICT Bot Online"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    web.run(host="0.0.0.0", port=port)

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_KEY = "9935ca70e0f842569acc2790803c1e0c"

PAIRS = ["EURUSD","GBPUSD","USDJPY","XAUUSD","BTCUSD"]
TIMEFRAMES = {
    "1M":"1min",
    "5M":"5min",
    "15M":"15min",
    "1H":"1h"
}

AUTO_MODE = False
RISK_MODE = "Medium"
CHAT_ID = None

# ================= SESSION =================
def session():
    h = datetime.datetime.utcnow().hour
    if 7 <= h <= 10: return "London"
    if 13 <= h <= 16: return "New York"
    return None

# ================= DATA =================
def get_data(pair, tf):
    url = f"https://api.twelvedata.com/time_series?symbol={pair}&interval={tf}&apikey={API_KEY}&outputsize=60"
    r = requests.get(url).json()
    return r.get("values",[])

# ================= ORB =================
def orb_range(data):
    # first 15 minutes range
    candles = data[-3:]
    highs = [float(c["high"]) for c in candles]
    lows  = [float(c["low"]) for c in candles]
    return max(highs), min(lows)

def orb_break(data):
    hi, lo = orb_range(data)
    last = float(data[0]["close"])
    if last > hi: return "BUY"
    if last < lo: return "SELL"
    return None

# ================= ICT =================
def liquidity_grab(c):
    wick = abs(float(c["high"]) - float(c["low"]))
    body = abs(float(c["open"]) - float(c["close"]))
    return wick > body * 2

def fvg(data):
    a,b,c = data[2],data[1],data[0]
    return float(c["low"]) > float(a["high"]) or float(c["high"]) < float(a["low"])

def trend(data):
    closes=[float(c["close"]) for c in data[:20]]
    fast=sum(closes[:10])/10
    slow=sum(closes[10:20])/10
    return "BUY" if fast>slow else "SELL"

# ================= SCORE =================
def score(orb,liq,fvg,tr):
    s=0
    if orb: s+=30
    if liq: s+=25
    if fvg: s+=25
    if tr: s+=20
    return s

# ================= MAIN ANALYSIS =================
def analyze(pair,tf,risk):
    if not session(): return None
    
    d=get_data(pair,tf)
    if not d: return None
    
    orb=orb_break(d)
    if not orb: return None
    
    liq=liquidity_grab(d[0])
    gap=fvg(d)
    tr=trend(d)
    
    s=score(orb,liq,gap,tr)
    
    limits={"Minimum":80,"Medium":65,"High":40}
    if s < limits[risk]: return None
    
    return f"""
🔥 ORB + ICT SIGNAL

PAIR: {pair}
TF: {tf}
SESSION: {session()}
TYPE: {orb}
ENTRY: {d[0]['close']}
WIN RATE: {s}%
RISK: {risk}

LOGIC:
• ORB Breakout
• Liquidity Sweep
• FVG
• Trend Filter
"""

# ================= TELEGRAM =================
MENU=[
["Signal"],
["Auto Signal"],
["Settings"],
["Help"]
]

async def start(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    global CHAT_ID
    CHAT_ID=update.effective_chat.id
    await update.message.reply_text(
        "Lazy ORB + ICT Bot Ready",
        reply_markup=ReplyKeyboardMarkup(MENU,resize_keyboard=True)
    )

async def menu(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    global AUTO_MODE,RISK_MODE
    txt=update.message.text
    
    if txt=="Signal":
        await update.message.reply_text("Send: EURUSD 5M")
        
    elif txt=="Auto Signal":
        AUTO_MODE=not AUTO_MODE
        await update.message.reply_text(
            f"AUTO MODE: {'ON' if AUTO_MODE else 'OFF'}"
        )
        if AUTO_MODE:
            asyncio.create_task(auto_scan(ctx))
            
    elif txt=="Settings":
        await update.message.reply_text(
            "Risk: Minimum | Medium | High\nType: risk medium"
        )
        
    elif txt.lower().startswith("risk"):
        RISK_MODE=txt.split()[1].capitalize()
        await update.message.reply_text(f"Risk set to {RISK_MODE}")
        
    elif txt=="Help":
        await update.message.reply_text(
            "Manual: PAIR TF\nExample: EURUSD 5M"
        )
        
    else:
        try:
            p,t=txt.split()
            res=analyze(p.upper(),TIMEFRAMES[t.upper()],RISK_MODE)
            await update.message.reply_text(res if res else "No setup found")
        except:
            await update.message.reply_text("Format: EURUSD 5M")

# ================= AUTO MODE =================
async def auto_scan(ctx):
    global AUTO_MODE
    while AUTO_MODE:
        for p in PAIRS:
            for tf in TIMEFRAMES.values():
                r=analyze(p,tf,RISK_MODE)
                if r:
                    await ctx.bot.send_message(chat_id=CHAT_ID,text=r)
                    await asyncio.sleep(60)
        await asyncio.sleep(30)

# ================= MAIN =================
def main():
    import threading
    threading.Thread(target=run_web,daemon=True).start()
    
    app=Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start",start))
    app.add_handler(MessageHandler(filters.TEXT,menu))
    
    print("BOT RUNNING...")
    app.run_polling()

if __name__=="__main__":
    main()
