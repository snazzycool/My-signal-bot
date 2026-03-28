import asyncio
import pandas as pd
from twelvedata import TDClient
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class RateLimiter:
    def __init__(self, calls_per_minute: int):
        self.rate = calls_per_minute / 60
        self.tokens = 1.0
        self.last = datetime.utcnow()
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            now = datetime.utcnow()
            elapsed = (now - self.last).total_seconds()
            self.tokens += elapsed * self.rate
            if self.tokens > 1.0:
                self.tokens = 1.0
            if self.tokens < 1.0:
                sleep_time = (1.0 - self.tokens) / self.rate
                await asyncio.sleep(sleep_time)
            self.tokens -= 1.0
            self.last = now

class MarketData:
    def __init__(self, api_key: str, config):
        self.client = TDClient(apikey=api_key)
        self.config = config
        self.rate_limiter = RateLimiter(config.RATE_LIMIT_CALLS_PER_MINUTE)
        self.semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_REQUESTS)

    async def fetch_ohlcv(self, symbol: str, interval: str, output_size: int = 200) -> pd.DataFrame:
        await self.rate_limiter.acquire()
        async with self.semaphore:
            try:
                loop = asyncio.get_event_loop()
                df = await loop.run_in_executor(
                    None,
                    lambda: self.client.time_series(symbol=symbol, interval=interval, outputsize=output_size).as_pandas()
                )
                if df.empty:
                    logger.warning(f"No data for {symbol} {interval}")
                    return None
                return df
            except Exception as e:
                logger.error(f"Error fetching {symbol} {interval}: {e}")
                return None

    async def fetch_multitimeframe(self, symbol: str) -> dict:
        htf_task = self.fetch_ohlcv(symbol, "1h", 200)
        entry_task = self.fetch_ohlcv(symbol, "5min", 200)
        htf_df, entry_df = await asyncio.gather(htf_task, entry_task)
        return {"htf": htf_df, "entry": entry_df}
