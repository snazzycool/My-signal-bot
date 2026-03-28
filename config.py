import os
from dotenv import load_dotenv

load_dotenv()

# API Keys
BOT_TOKEN = os.getenv("BOT_TOKEN")
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")

# Trading pairs
PAIRS = [
    "EUR/USD", "GBP/USD", "BTC/USD", "ETH/USD", "XAU/USD",
    "AUD/USD", "USD/JPY", "USD/CAD", "GBP/JPY", "BNB/USD"
]

# Timeframes
HTF_TIMEFRAME = "1h"
ENTRY_TIMEFRAME = "5min"

# Indicator periods
EMA_FAST = 50
EMA_SLOW = 200
RSI_PERIOD = 14
ATR_PERIOD = 14

# Scoring
MIN_SCORE = 7
MIN_RR = 1.5

# Risk management
SL_MULTIPLIER = 1.5
TP_MULTIPLIER = 2.5

# Anti‑spam
SIGNAL_COOLDOWN_MINUTES = 60

# Kill Zones (UTC)
ENABLE_SESSION_FILTER = os.getenv("ENABLE_SESSION_FILTER", "false").lower() == "true"
LONDON_KILL_ZONE_START = 7
LONDON_KILL_ZONE_END = 10
NEW_YORK_KILL_ZONE_START = 13
NEW_YORK_KILL_ZONE_END = 16

# Entry model
ENTRY_MODEL = "EMA_BOUNCE"

# Minimum candle size (as % of ATR)
MIN_CANDLE_SIZE_ATR_RATIO = 0.5

# Loss streak pause
LOSS_STREAK_PAUSE = 3

# Volatility spike threshold
VOLATILITY_SPIKE_THRESHOLD = 2.0

# Rate limiting (free TwelveData: 8 calls per minute)
RATE_LIMIT_CALLS_PER_MINUTE = 8
MAX_CONCURRENT_REQUESTS = 5

# Spread filter
ESTIMATED_SPREAD_PIPS = {
    "EUR/USD": 0.2,
    "GBP/USD": 0.3,
    "BTC/USD": 2.0,
    "ETH/USD": 2.0,
    "XAU/USD": 0.5,
    "AUD/USD": 0.3,
    "USD/JPY": 0.3,
    "USD/CAD": 0.3,
    "GBP/JPY": 0.5,
    "BNB/USD": 2.0,
}
MAX_SPREAD_PIPS = 1.0

# Slippage modeling
SLIPPAGE_FACTOR = 0.2  # as % of ATR

# News filter
ENABLE_NEWS_FILTER = os.getenv("ENABLE_NEWS_FILTER", "false").lower() == "true"
NEWS_BLOCK_MINUTES = 15
# Simplified static list of high‑impact news events (UTC). Adjust as needed.
HIGH_IMPACT_NEWS = [
    {"name": "NFP", "day_of_week": 4, "time": "13:30"},   # Friday
    {"name": "FOMC", "day_of_week": 2, "time": "18:00"},  # Wednesday
    {"name": "CPI", "day_of_week": 3, "time": "13:30"},    # Thursday
]

# Smart money flags
ENABLE_ORDER_BLOCK = True
ENABLE_FVG = True
ENABLE_SESSION_GRAB = True

# Database DSN – set in environment variable DATABASE_URL
