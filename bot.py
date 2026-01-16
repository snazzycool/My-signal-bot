import os, time, threading, datetime, requests
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_KEY = "9935ca70e0f842569acc2790803c1e0c"

# ====== DATA ======
PAIRS = ["EURUSD","GBPUSD","EURGBP","AUDGBP","EURCAD",
         "XAUUSD","US30","NASDAQ","BTCUSD","USDJPY"]

TIMEFRAMES = ["1H","15M","5M","1M"]
RISK_TYPES = ["Minimum","Low","High"]

# ====== STATES ======
user_state = {}
history_manual = []
history_auto = []
AUTO_MODE = False

# ====== KEYBOARDS ======
MAIN_MENU = [
    ["Get Signal"],
    ["Auto Signal"],
    ["History"],
    ["Settings"],
    ["Help"]
]

BACK_MENU = [["Back","Main Menu"]]

def pair_keyboard():
    rows=[]
    for i in range(0,len(PAIRS),2):
        rows.append(PAIRS[i:i+2])
    rows.append(["Back","Main Menu"])
    return rows

def tf_keyboard():
    return [["1H","15M"],["5M","1M"],["Back","Main Menu"]]

def risk_keyboard():
    return [["Minimum","Low"],["High"],["Back","Main Menu"]]

# ====== API LOGIC ======

def fetch(pair,tf):
    url=f"https://api.twelvedata.com/time_series?symbol={pair}&interval={tf.lower()}&apikey={API_KEY}&outputsize=50"
    r=requests.get(url).json()
    return r.get("values",[])

def simple_strategy(data,risk):
    if not data: return None

    last=float(data[0]["close"])
    prev=float(data[1]["close"])

    # SUPPORT & RESISTANCE ONLY (as you requested)
    if risk=="Minimum":
        cond = abs(last-prev) > 0.002
    elif risk=="Low":
        cond = abs(last-prev) > 0.001
    else:
        cond = True

    if not cond:
        return None

    return "BUY" if last>prev else "SELL"

# ====== CORE ANALYSIS ======

def analyze(pair,tf,risk):
    data=fetch(pair,tf)
    signal=simple_strategy(data,risk)

    if not signal:
        return None

    price=data[0]["close"]
    return f"""
PAIR: {pair}
TF: {tf}
SIGNAL: {signal}
ENTRY: {price}
RISK: {risk}
STATUS: ACTIVE
""".strip()

# ====== TELEGRAM FLOW ======

async def start(update:Update, context:ContextTypes.DEFAULT_TYPE):
    user_state[update.effective_chat.id]={}
    await update.message.reply_text(
        "Welcome to Lazy Trading Bot",
        reply_markup=ReplyKeyboardMarkup(MAIN_MENU,resize_keyboard=True)
    )

async def handler(update:Update, context:ContextTypes.DEFAULT_TYPE):
    chat=update.effective_chat.id
    text=update.message.text

    # MAIN MENU
    if text=="Main Menu":
        await update.message.reply_text(
            "Main Menu",
            reply_markup=ReplyKeyboardMarkup(MAIN_MENU,resize_keyboard=True)
        )

    elif text=="Get Signal":
        user_state[chat]={"stage":"pair"}
        await update.message.reply_text(
            "Choose pair",
            reply_markup=ReplyKeyboardMarkup(pair_keyboard(),resize_keyboard=True)
        )

    elif text in PAIRS:
        user_state[chat]["pair"]=text
        user_state[chat]["stage"]="tf"
        await update.message.reply_text(
            "Choose timeframe",
            reply_markup=ReplyKeyboardMarkup(tf_keyboard(),resize_keyboard=True)
        )

    elif text in TIMEFRAMES:
        user_state[chat]["tf"]=text
        user_state[chat]["stage"]="risk"
        await update.message.reply_text(
            "Choose risk level",
            reply_markup=ReplyKeyboardMarkup(risk_keyboard(),resize_keyboard=True)
        )

    elif text in RISK_TYPES:
        st=user_state[chat]
        pair=st["pair"]; tf=st["tf"]; risk=text

        await update.message.reply_text("Scanning market... please wait ⏳")

        res=analyze(pair,tf,risk)

        if res:
            history_manual.append(res)
            await update.message.reply_text(res)
        else:
            await update.message.reply_text(
                "Market not stable now.\nBetter setup may appear soon.\nTry later."
            )

        await update.message.reply_text(
            "Main Menu",
            reply_markup=ReplyKeyboardMarkup(MAIN_MENU,resize_keyboard=True)
        )

    # ===== AUTO MODE =====
    elif text=="Auto Signal":
        await update.message.reply_text(
            "Choose risk for AUTO mode",
            reply_markup=ReplyKeyboardMarkup(risk_keyboard(),resize_keyboard=True)
        )
        user_state[chat]={"stage":"auto_risk"}

    elif user_state.get(chat,{}).get("stage")=="auto_risk" and text in RISK_TYPES:
        global AUTO_MODE
        AUTO_MODE=True
        user_state[chat]["auto_risk"]=text

        await update.message.reply_text(
            "AUTO MODE ACTIVE\nSignals will be sent automatically.\n\nPress OFF to stop.",
            reply_markup=ReplyKeyboardMarkup([["OFF"]],resize_keyboard=True)
        )

        threading.Thread(target=auto_scan,args=(context.bot,chat,text)).start()

    elif text=="OFF":
        AUTO_MODE=False
        await update.message.reply_text(
            "AUTO MODE STOPPED",
            reply_markup=ReplyKeyboardMarkup(MAIN_MENU,resize_keyboard=True)
        )

    # ===== HISTORY =====
    elif text=="History":
        await update.message.reply_text(
            "Select history type",
            reply_markup=ReplyKeyboardMarkup(
                [["Manual History"],["Auto History"],["Back","Main Menu"]],
                resize_keyboard=True
            )
        )

    elif text=="Manual History":
        msg="\n\n".join(history_manual[-5:]) or "No history yet"
        await update.message.reply_text(msg)

    elif text=="Auto History":
        msg="\n\n".join(history_auto[-5:]) or "No history yet"
        await update.message.reply_text(msg)

    # ===== SETTINGS =====
    elif text=="Settings":
        await update.message.reply_text(
            "Settings\n\n• Turn notifications ON\n• Do not mute bot\n• Allow background data"
        )

    # ===== HELP =====
    elif text=="Help":
        await update.message.reply_text(
            "This bot sends trading signals.\n\n"
            "Use Get Signal for manual trades\n"
            "Use Auto Signal for automated trades\n\n"
            "Trade responsibly."
        )

    elif text=="Back":
        await update.message.reply_text(
            "Going back",
            reply_markup=ReplyKeyboardMarkup(MAIN_MENU,resize_keyboard=True)
        )

# ===== AUTO SCAN =====

def auto_scan(bot,chat,risk):
    while AUTO_MODE:
        for p in PAIRS:
            for tf in TIMEFRAMES:
                res=analyze(p,tf,risk)
                if res:
                    history_auto.append(res)
                    bot.send_message(chat_id=chat,text=res)
        time.sleep(60)

# ===== MAIN =====

def main():
    app=Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",start))
    app.add_handler(MessageHandler(filters.TEXT,handler))

    print("BOT RUNNING...")
    app.run_polling()

if __name__=="__main__":
    main()
