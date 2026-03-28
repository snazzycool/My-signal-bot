import pandas as pd
import numpy as np
import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)


class StrategyEngine:

    def __init__(self, config, db):
        self.config = config
        self.db = db

    # ------------------------------------------------------------------
    # Indicators
    # ------------------------------------------------------------------

    def compute_rsi(self, close: pd.Series, period: int = 14) -> pd.Series:
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(window=period).mean()
        avg_loss = loss.rolling(window=period).mean()
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def compute_atr(
        self,
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        period: int = 14,
    ) -> pd.Series:
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()

    def compute_indicators(self, df: pd.DataFrame) -> dict:
        if df is None or len(df) < 100:
            return None

        close = df['close']
        high = df['high']
        low = df['low']

        ema_fast = close.ewm(span=self.config.EMA_FAST, adjust=False).mean()
        ema_slow = close.ewm(span=self.config.EMA_SLOW, adjust=False).mean()
        rsi = self.compute_rsi(close, self.config.RSI_PERIOD)
        atr = self.compute_atr(high, low, close, self.config.ATR_PERIOD)
        atr_avg = atr.rolling(20).mean()
        swing_high = high.rolling(window=5, center=True).max()
        swing_low = low.rolling(window=5, center=True).min()

        return {
            'ema_fast': ema_fast,
            'ema_slow': ema_slow,
            'rsi': rsi,
            'atr': atr,
            'atr_avg': atr_avg,
            'swing_high': swing_high,
            'swing_low': swing_low,
            'close': close,
            'high': high,
            'low': low,
        }

    # ------------------------------------------------------------------
    # Liquidity sweep
    # ------------------------------------------------------------------

    def check_liquidity_sweep(self, df: pd.DataFrame) -> bool:
        if len(df) < 30:
            return False

        last_high = df['high'].iloc[-20:-1].max()
        last_low = df['low'].iloc[-20:-1].min()
        c = df.iloc[-1]
        avg_candle_size = (df['high'] - df['low']).rolling(20).mean().iloc[-1]

        bullish_sweep = (
            c['low'] < last_low
            and c['close'] > last_low
            and (c['high'] - c['low']) > avg_candle_size
        )
        bearish_sweep = (
            c['high'] > last_high
            and c['close'] < last_high
            and (c['high'] - c['low']) > avg_candle_size
        )
        return bullish_sweep or bearish_sweep

    # ------------------------------------------------------------------
    # Market structure
    # ------------------------------------------------------------------

    def detect_market_structure(self, df: pd.DataFrame) -> str:
        if len(df) < 30:
            return 'ranging'

        highs = df['high'].values
        lows = df['low'].values
        n = len(highs)

        swing_highs = []
        swing_lows = []

        for i in range(2, n - 2):
            if (highs[i] > highs[i - 1] and highs[i] > highs[i - 2]
                    and highs[i] > highs[i + 1] and highs[i] > highs[i + 2]):
                swing_highs.append((i, highs[i]))
            if (lows[i] < lows[i - 1] and lows[i] < lows[i - 2]
                    and lows[i] < lows[i + 1] and lows[i] < lows[i + 2]):
                swing_lows.append((i, lows[i]))

        swing_highs = swing_highs[-5:]
        swing_lows = swing_lows[-5:]

        if len(swing_highs) >= 3 and len(swing_lows) >= 3:
            hh = all(
                swing_highs[i][1] < swing_highs[i + 1][1]
                for i in range(len(swing_highs) - 1)
            )
            hl = all(
                swing_lows[i][1] < swing_lows[i + 1][1]
                for i in range(len(swing_lows) - 1)
            )
            if hh and hl:
                return 'uptrend'

            ll = all(
                swing_lows[i][1] > swing_lows[i + 1][1]
                for i in range(len(swing_lows) - 1)
            )
            lh = all(
                swing_highs[i][1] > swing_highs[i + 1][1]
                for i in range(len(swing_highs) - 1)
            )
            if ll and lh:
                return 'downtrend'

        return 'ranging'

    # ------------------------------------------------------------------
    # Entry precision — EMA bounce model
    # ------------------------------------------------------------------

    def get_entry_price_ema_bounce(
        self, df: pd.DataFrame, direction: str
    ) -> float:
        if len(df) < 5:
            return None

        ema_fast = df['close'].ewm(span=self.config.EMA_FAST).mean()
        last = df.iloc[-1]
        prev = df.iloc[-2]

        if direction == 'BUY':
            touched = (
                last['low'] <= ema_fast.iloc[-1]
                and last['close'] > ema_fast.iloc[-1]
                and prev['close'] <= ema_fast.iloc[-2]
            )
        else:
            touched = (
                last['high'] >= ema_fast.iloc[-1]
                and last['close'] < ema_fast.iloc[-1]
                and prev['close'] >= ema_fast.iloc[-2]
            )

        return last['close'] if touched else None

    # ------------------------------------------------------------------
    # Noise filter
    # ------------------------------------------------------------------

    def check_noise(
        self, entry_df: pd.DataFrame, entry_inds: dict
    ) -> bool:
        if entry_df is None or len(entry_df) < 20 or entry_inds is None:
            return True

        atr = entry_inds['atr'].iloc[-1]
        price = entry_inds['close'].iloc[-1]

        if atr / price < 0.001:
            return True

        avg_candle_size = (
            (entry_df['high'] - entry_df['low']).rolling(20).mean().iloc[-1]
        )
        if avg_candle_size < self.config.MIN_CANDLE_SIZE_ATR_RATIO * atr:
            return True

        return False

    # ------------------------------------------------------------------
    # Loss streak pause
    # ------------------------------------------------------------------

    async def check_loss_streak(self, pair: str) -> bool:
        streak = await self.db.get_loss_streak(pair)
        if streak >= self.config.LOSS_STREAK_PAUSE:
            logger.info(f"Pausing {pair} due to loss streak of {streak}")
            return True
        return False

    # ------------------------------------------------------------------
    # Kill zone (session filter)
    # ------------------------------------------------------------------

    def _is_kill_zone(self) -> bool:
        hour = datetime.utcnow().hour
        london = (
            self.config.LONDON_KILL_ZONE_START
            <= hour
            < self.config.LONDON_KILL_ZONE_END
        )
        new_york = (
            self.config.NEW_YORK_KILL_ZONE_START
            <= hour
            < self.config.NEW_YORK_KILL_ZONE_END
        )
        return london or new_york

    # ------------------------------------------------------------------
    # News filter
    # ------------------------------------------------------------------

    def _is_news_block(self) -> bool:
        if not self.config.ENABLE_NEWS_FILTER:
            return False

        now = datetime.utcnow()
        for event in self.config.HIGH_IMPACT_NEWS:
            if event['day_of_week'] == now.weekday():
                event_dt = datetime.combine(
                    now.date(),
                    datetime.strptime(event['time'], "%H:%M").time(),
                )
                diff_minutes = abs((now - event_dt).total_seconds() / 60)
                if diff_minutes <= self.config.NEWS_BLOCK_MINUTES:
                    return True
        return False

    # ------------------------------------------------------------------
    # Spread filter
    # ------------------------------------------------------------------

    def get_estimated_spread(self, pair: str, price: float) -> float:
        pip_value = self.config.ESTIMATED_SPREAD_PIPS.get(pair, 0.5)
        if pair in ("USD/JPY", "GBP/JPY"):
            return pip_value * 0.01
        if "BTC" in pair or "ETH" in pair:
            return pip_value
        return pip_value * 0.0001

    def check_spread(self, pair: str, price: float) -> bool:
        spread = self.get_estimated_spread(pair, price)
        max_spread = self.config.MAX_SPREAD_PIPS

        if pair in ("USD/JPY", "GBP/JPY"):
            max_spread_price = max_spread * 0.01
        elif "BTC" in pair or "ETH" in pair:
            max_spread_price = max_spread
        else:
            max_spread_price = max_spread * 0.0001

        if spread > max_spread_price:
            logger.info(
                f"Spread too high for {pair}: "
                f"{spread:.5f} > {max_spread_price:.5f}"
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Slippage adjustment
    # ------------------------------------------------------------------

    def adjust_for_slippage(
        self, entry: float, direction: str, atr: float
    ) -> float:
        slippage = atr * self.config.SLIPPAGE_FACTOR
        return entry + slippage if direction == 'BUY' else entry - slippage

    # ------------------------------------------------------------------
    # Order block detection
    # ------------------------------------------------------------------

    def detect_order_block(
        self, df: pd.DataFrame, direction: str
    ) -> tuple:
        if len(df) < 50:
            return False, 0.0

        inds = self.compute_indicators(df)
        if inds is None:
            return False, 0.0

        atr = inds['atr'].iloc[-1]
        high = df['high'].values
        low = df['low'].values

        for i in range(-30, -5):
            if i < 2 or i >= len(low) - 2:
                continue

            is_swing_low = (
                low[i] < low[i - 1] and low[i] < low[i - 2]
                and low[i] < low[i + 1] and low[i] < low[i + 2]
            )
            if is_swing_low:
                max_after = max(high[i + 1:i + 10])
                if max_after - low[i] > 1.5 * atr:
                    return True, low[i]

            is_swing_high = (
                high[i] > high[i - 1] and high[i] > high[i - 2]
                and high[i] > high[i + 1] and high[i] > high[i + 2]
            )
            if is_swing_high:
                min_after = min(low[i + 1:i + 10])
                if high[i] - min_after > 1.5 * atr:
                    return True, high[i]

        return False, 0.0

    # ------------------------------------------------------------------
    # FIX: Fair Value Gap (3-candle imbalance)
    #
    # Original logic was checking middle-candle OHLC relationships, which
    # is not how FVGs work.
    #
    # Correct definition:
    #   Bullish FVG: candle[i-1].low > candle[i+1].high
    #                (gap between prior candle's low and next candle's high)
    #   Bearish FVG: candle[i-1].high < candle[i+1].low
    #                (gap between prior candle's high and next candle's low)
    #
    # We then check whether the current price is inside that gap zone.
    # ------------------------------------------------------------------

    def detect_fvg(self, df: pd.DataFrame, direction: str) -> tuple:
        if len(df) < 5:
            return False, 0.0, 0.0

        # Scan the last 5 three-candle windows looking for an unmitigated FVG
        for i in range(-5, -2):
            try:
                c1 = df.iloc[i - 1]   # candle before middle
                c3 = df.iloc[i + 1]   # candle after middle
            except IndexError:
                continue

            if direction == 'BUY':
                # Bullish FVG: gap between c1's low and c3's high
                if c1['low'] > c3['high']:
                    fvg_low = c3['high']
                    fvg_high = c1['low']
                    return True, fvg_low, fvg_high

            else:  # SELL
                # Bearish FVG: gap between c1's high and c3's low
                if c1['high'] < c3['low']:
                    fvg_low = c1['high']
                    fvg_high = c3['low']
                    return True, fvg_low, fvg_high

        return False, 0.0, 0.0

    # ------------------------------------------------------------------
    # Session liquidity grab
    # ------------------------------------------------------------------

    def check_session_liquidity_grab(
        self, df: pd.DataFrame, direction: str
    ) -> bool:
        if not self._is_kill_zone():
            return False
        return self.check_liquidity_sweep(df)

    # ------------------------------------------------------------------
    # Main signal generation
    # ------------------------------------------------------------------

    async def generate_signal(
        self,
        pair: str,
        htf_df: pd.DataFrame,
        entry_df: pd.DataFrame,
    ) -> dict:

        # ── Anti-spam ────────────────────────────────────────────────────
        last_time = await self.db.get_last_signal_time(pair)
        now = time.time()
        if now - last_time < self.config.SIGNAL_COOLDOWN_MINUTES * 60:
            return None

        # ── Indicators ───────────────────────────────────────────────────
        htf_inds = self.compute_indicators(htf_df)
        entry_inds = self.compute_indicators(entry_df)
        if htf_inds is None or entry_inds is None:
            return None

        # ── Loss streak pause ────────────────────────────────────────────
        if await self.check_loss_streak(pair):
            return None

        # ── Noise filter ─────────────────────────────────────────────────
        if self.check_noise(entry_df, entry_inds):
            logger.info(f"Noisy market for {pair}, skipping.")
            return None

        # ── HTF trend direction ──────────────────────────────────────────
        htf_ema_fast = htf_inds['ema_fast'].iloc[-1]
        htf_ema_slow = htf_inds['ema_slow'].iloc[-1]

        if htf_ema_fast > htf_ema_slow:
            direction = 'BUY'
        elif htf_ema_fast < htf_ema_slow:
            direction = 'SELL'
        else:
            return None

        # ── Spread filter ─────────────────────────────────────────────────
        entry_close = entry_inds['close'].iloc[-1]
        if not self.check_spread(pair, entry_close):
            return None

        # ── Session filter ────────────────────────────────────────────────
        if self.config.ENABLE_SESSION_FILTER and not self._is_kill_zone():
            return None

        # ── News filter ───────────────────────────────────────────────────
        if self._is_news_block():
            logger.info(f"News block active, skipping {pair}")
            return None

        entry_ema_fast = entry_inds['ema_fast'].iloc[-1]
        rsi = entry_inds['rsi'].iloc[-1]
        atr = entry_inds['atr'].iloc[-1]
        atr_avg = entry_inds['atr_avg'].iloc[-1]

        # ── FIX: Widen RSI pullback window ────────────────────────────────
        # Original was 30–50 (BUY) / 50–70 (SELL) — far too tight, most
        # valid setups were being rejected. Widened to 30–55 / 45–70.
        if direction == 'BUY':
            if not (30 <= rsi <= 55):
                return None
        else:
            if not (45 <= rsi <= 70):
                return None

        # ── Price must be near the fast EMA (within 0.5%) ────────────────
        if not (entry_close * 0.995 <= entry_ema_fast <= entry_close * 1.005):
            return None

        # ── Market structure must confirm direction ───────────────────────
        structure = self.detect_market_structure(entry_df)
        if direction == 'BUY' and structure != 'uptrend':
            return None
        if direction == 'SELL' and structure != 'downtrend':
            return None

        # ── Volatility filter ─────────────────────────────────────────────
        if atr <= atr_avg:
            return None

        # Avoid extreme RSI
        if (direction == 'BUY' and rsi > 75) or (direction == 'SELL' and rsi < 25):
            return None

        # Avoid volatility spikes
        if atr > self.config.VOLATILITY_SPIKE_THRESHOLD * atr_avg:
            return None

        # ── Liquidity sweep (for scoring, NOT a hard gate) ────────────────
        liquidity_sweep = self.check_liquidity_sweep(entry_df)

        # ── Entry price ───────────────────────────────────────────────────
        if self.config.ENTRY_MODEL == "EMA_BOUNCE":
            entry_price = self.get_entry_price_ema_bounce(entry_df, direction)
            if entry_price is None:
                return None
        else:
            entry_price = entry_close

        # ── FIX: Smart money filters are now OPTIONAL score boosters only ─
        # Previously they were both gates (reject if not found) AND score
        # boosters simultaneously, making the score inflated/meaningless.
        # Now: they contribute score only if found, never reject the trade.
        ob_found, ob_level = False, 0.0
        if self.config.ENABLE_ORDER_BLOCK:
            ob_found, ob_level = self.detect_order_block(entry_df, direction)

        fvg_found, fvg_low, fvg_high = False, 0.0, 0.0
        if self.config.ENABLE_FVG:
            fvg_found, fvg_low, fvg_high = self.detect_fvg(entry_df, direction)

        session_grab = False
        if self.config.ENABLE_SESSION_GRAB:
            session_grab = self.check_session_liquidity_grab(entry_df, direction)

        # ── Slippage-adjusted entry for risk calc ─────────────────────────
        adjusted_entry = self.adjust_for_slippage(entry_price, direction, atr)

        # ── Risk management ───────────────────────────────────────────────
        if direction == 'BUY':
            sl = adjusted_entry - self.config.SL_MULTIPLIER * atr
            tp = adjusted_entry + self.config.TP_MULTIPLIER * atr
            partial_tp = adjusted_entry + 1.0 * atr
        else:
            sl = adjusted_entry + self.config.SL_MULTIPLIER * atr
            tp = adjusted_entry - self.config.TP_MULTIPLIER * atr
            partial_tp = adjusted_entry - 1.0 * atr

        risk = abs(adjusted_entry - sl)
        reward = abs(tp - adjusted_entry)
        rr = reward / risk if risk > 0 else 0.0

        if rr < self.config.MIN_RR:
            return None

        # ── FIX: Scoring — gates and scoring are now fully separate ───────
        # Base score from mandatory confirmed conditions:
        score = 0
        details = {}

        score += 3          # HTF trend confirmed
        details['trend'] = 3

        score += 1          # RSI pullback in valid range
        details['rsi'] = 1

        score += 2          # Market structure confirmed
        details['structure'] = 2

        score += 1          # ATR above average (already gated above)
        details['volatility'] = 1

        # Optional boosters (contribute only if detected)
        if liquidity_sweep:
            score += 3
            details['sweep'] = 3

        if self.config.ENABLE_SESSION_FILTER and self._is_kill_zone():
            score += 1
            details['session'] = 1

        if ob_found:
            score += 2
            details['order_block'] = 2

        if fvg_found:
            score += 2
            details['fvg'] = 2

        if session_grab:
            score += 2
            details['session_grab'] = 2

        if score < self.config.MIN_SCORE:
            return None

      
