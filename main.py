import asyncio
import logging
import os
import config                     # import the whole module
from config import BOT_TOKEN, TWELVE_DATA_API_KEY   # import specific constants
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

async def main():
    if not BOT_TOKEN or not TWELVE_DATA_API_KEY:
        logger.error("Missing BOT_TOKEN or TWELVE_DATA_API_KEY")
        return

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        logger.error("DATABASE_URL not set")
        return

    db = Database(dsn)
    await db.init()

    market_data = MarketData(TWELVE_DATA_API_KEY, config)   # pass the module
    strategy = StrategyEngine(config, db)                   # pass the module
    bot = TradingBot(config, db, market_data, strategy)     # pass the module

    # Run HTTP server and bot concurrently
    await asyncio.gather(
        start_http_server(),
        bot.run()
    )

if __name__ == "__main__":
    asyncio.run(main())
