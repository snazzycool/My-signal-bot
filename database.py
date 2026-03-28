import asyncpg
from typing import List, Dict, Optional, Tuple
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool = None

    async def init(self):
        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)
        async with self.pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS trades (
                    id SERIAL PRIMARY KEY,
                    pair TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry REAL NOT NULL,
                    sl REAL NOT NULL,
                    tp REAL NOT NULL,
                    partial_tp REAL,
                    partial_notified INTEGER DEFAULT 0,
                    score INTEGER,
                    timestamp TEXT NOT NULL,
                    status TEXT DEFAULT 'PENDING'
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS last_signal (
                    pair TEXT PRIMARY KEY,
                    last_time REAL
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS chats (
                    chat_id INTEGER PRIMARY KEY
                )
            ''')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_status ON trades(status)')
        logger.info("PostgreSQL database initialized")

    async def log_trade(self, pair: str, direction: str, entry: float, sl: float, tp: float, partial_tp: float, score: int) -> int:
        timestamp = datetime.utcnow().isoformat()
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO trades (pair, direction, entry, sl, tp, partial_tp, score, timestamp) VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id",
                pair, direction, entry, sl, tp, partial_tp, score, timestamp
            )
            return row['id']

    async def update_trade_outcome(self, trade_id: int, outcome: str):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE trades SET status = $1 WHERE id = $2", outcome, trade_id)

    async def mark_partial_notified(self, trade_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE trades SET partial_notified = 1 WHERE id = $1", trade_id)

    async def get_pending_trades(self) -> List[Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, pair, direction, entry, sl, tp, partial_tp, partial_notified FROM trades WHERE status = 'PENDING'"
            )
            return [dict(row) for row in rows]

    async def get_last_signal_time(self, pair: str) -> float:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT last_time FROM last_signal WHERE pair = $1", pair)
            return row['last_time'] if row else 0.0

    async def set_last_signal_time(self, pair: str, timestamp: float):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO last_signal (pair, last_time) VALUES ($1,$2) ON CONFLICT (pair) DO UPDATE SET last_time = EXCLUDED.last_time",
                pair, timestamp
            )

    async def add_chat(self, chat_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("INSERT INTO chats (chat_id) VALUES ($1) ON CONFLICT DO NOTHING", chat_id)

    async def get_all_chats(self) -> List[int]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT chat_id FROM chats")
            return [row['chat_id'] for row in rows]

    async def get_performance(self) -> Tuple[int, int, int, float]:
        async with self.pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM trades")
            wins = await conn.fetchval("SELECT COUNT(*) FROM trades WHERE status = 'WIN'")
            losses = await conn.fetchval("SELECT COUNT(*) FROM trades WHERE status = 'LOSS'")
            win_rate = (wins / total * 100) if total > 0 else 0
            return total, wins, losses, win_rate

    async def get_recent_trades(self, limit: int = 10) -> List[Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT pair, direction, entry, sl, tp, score, status, timestamp FROM trades ORDER BY timestamp DESC LIMIT $1",
                limit
            )
            return [dict(row) for row in rows]

    async def get_pair_performance(self) -> Dict[str, Tuple[int, int]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT pair, status, COUNT(*) FROM trades GROUP BY pair, status")
            result = {}
            for pair, status, count in rows:
                if pair not in result:
                    result[pair] = [0, 0]
                if status == 'WIN':
                    result[pair][0] = count
                elif status == 'LOSS':
                    result[pair][1] = count
            return {pair: (wins, losses) for pair, (wins, losses) in result.items()}

    async def get_recent_outcomes(self, pair: str, limit: int = 5) -> List[str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT status FROM trades WHERE pair = $1 ORDER BY timestamp DESC LIMIT $2",
                pair, limit
            )
            return [row['status'] for row in rows if row['status'] in ('WIN', 'LOSS')]

    async def get_loss_streak(self, pair: str) -> int:
        outcomes = await self.get_recent_outcomes(pair, 10)
        streak = 0
        for outcome in outcomes:
            if outcome == 'LOSS':
                streak += 1
            else:
                break
        return streak
