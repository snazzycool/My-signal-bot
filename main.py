import asyncio
import logging
import os
import threading
import config
from config import BOT_TOKEN, TWELVE_DATA_API_KEY
from database import Database
from market_data import MarketData
from strategy import StrategyEngine
from bot import TradingBot
from http_server import start_http_server

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def run_http():
    """Run the HTTP server in its own asyncio loop."""
    await start_http_server()

def start_http_thread():
    """Start the HTTP server in a background thread."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_http())
    loop.close()

def main():
    if not BOT_TOKEN or not TWELVE_DATA_API_KEY:
        logger.error("Missing BOT_TOKEN or TWELVE_DATA_API_KEY")
        return

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        logger.error("DATABASE_URL not set")
        return

    # Initialize database synchronously (asyncio needed)
    db = Database(dsn)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(db.init())
    loop.close()

    market_data = MarketData(TWELVE_DATA_API_KEY, config)
    strategy = StrategyEngine(config, db)
    bot = TradingBot(config, db, market_data, strategy)

    # Start HTTP server in a background thread
    http_thread = threading.Thread(target=start_http_thread, daemon=True)
    http_thread.start()
    logger.info("HTTP server started in background thread")

    # Run the bot (synchronous, blocks main thread)
    bot.run_sync()

if __name__ == "__main__":
    main()
