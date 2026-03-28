import pandas as pd
import pandas_ta as ta
import numpy as np
import logging
from datetime import datetime
import time

logger = logging.getLogger(__name__)

class StrategyEngine:
    def __init__(self, config, db):
        self.config = config
        self.db = db

    # ---------- Indicator Computation ----------
    def compute_indicators(self, df: pd.DataFrame) -> dict:
        if df is None or len(df) < 100:
            return None

        close = df['close']
        high = df['high']
        low = df['low']

        ema_fast = close.ewm(span=self.config.EMA_FAST, adjust=False).mean()
        ema_slow = close.ewm(span=self.config.EMA_SLOW, adjust=False).mean()
        rsi = ta.rsi(close, length=self.config.RSI_PERIOD)
        atr = ta.atr(high, low, close, length=self.config.ATR_PERIOD)
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
            'low': low
        }

    # ---------- Liquidity Sweep ----------
    def check_liquidity_sweep(self, df: pd.DataFrame) -> bool:
        if len(df) < 30:
            return False

        last_high = df['high'].iloc[-20:-1].max()
        last_low = df['low'].iloc[-20:-1].min()
        current_candle = df.iloc[-1]
        avg_candle_size = (df['high'] - df['low']).rolling(20).mean().iloc[-1]

        bullish_sweep = (
            current_candle['low'] < last_low and
            current_candle['close'] > last_low and
            (current_candle['high'] - current_candle['low']) > avg_candle_size
        )
        bearish_sweep = (
            current_candle['high'] > last_high and
            current_candle['close'] < last_high and
            (current_candle['high'] - current_candle['low']) > avg_candle_size
        )
        return bullish_sweep or bearish_sweep

    # ---------- Market Structure ----------
    def detect_market_structure(self, df: pd.DataFrame) -> str:
        if len(df) < 30:
            return 'ranging'

        highs = df['high'].values
        lows = df['low'].values
        n = len(highs)

        swing_highs = []
        swing_lows = []
        for i in range(2, n-2):
            if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
                swing_highs.append((i, highs[i]))
            if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
                swing_lows.append((i, lows[i]))

        swing_highs = swing_highs[-5:]
        swing_lows = swing_lows[-5:]

        if len(swing_highs) >= 3:
            hh_sequence = all(swing_highs[i][1] < swing_highs[i+1][1] for i in range(len(swing_highs)-1))
            if hh_sequence:
                if len(swing_lows) >= 3:
                    hl_sequence = all(swing_lows[i][1] < swing_lows[i+1][1] for i in range(len(swing_lows)-1))
                    if hl_sequence:
                        return 'uptrend'
        if len(swing_lows) >= 3:
            ll_sequence = all(swing_lows[i][1] > swing_lows[i+1][1] for i in range(len(swing_lows)-1))
            if ll_sequence:
                if len(swing_highs) >= 3:
                    lh_sequence = all(swing_highs[i][1] > swing_highs[i+1][1] for i in range(len(swing_highs)-1))
                    if lh_sequence:
                        return 'downtrend'
        return 'ranging'

    # ---------- Entry Precision (EMA Bounce) ----------
    def get_entry_price_ema_bounce(self, df: pd.DataFrame, direction: str) -> float:
        if len(df) < 5:
            return None
        ema_fast = df['close'].ewm(span=self.config.EMA_FAST).mean()
        last_candle = df.iloc[-1]
        prev_candle = df.iloc[-2]

        if direction == 'BUY':
            touched_ema = last_candle['low'] <= ema_fast.iloc[-1] and last_candle['close'] > ema_fast.iloc[-1]
            if touched_ema:
                if prev_candle['close'] <= ema_fast.iloc[-2]:
                    return last_candle['close']
        else:
            touched_ema = last_candle['high'] >= ema_fast.iloc[-1] and last_candle['close'] < ema_fast.iloc[-1]
            if touched_ema:
                if prev_candle['close'] >= ema_fast.iloc[-2]:
                    return last_candle['close']
        return None

    # ---------- Noise Filter ----------
    def check_noise(self, entry_df: pd.DataFrame, entry_inds: dict) -> bool:
        if entry_df is None or len(entry_df) < 20 or entry_inds is None:
            return True
        atr = entry_inds['atr'].iloc[-1]
        price = entry_inds['close'].iloc[-1]
        if atr / price < 0.001:
            return True
        avg_candle_size = (entry_df['high'] - entry_df['low']).rolling(20).mean().iloc[-1]
        if avg_candle_size < self.config.MIN_CANDLE_SIZE_ATR_RATIO * atr:
            return True
        return False

    # ---------- Loss Streak ----------
    async def check_loss_streak(self, pair: str) -> bool:
        streak = await self.db.get_loss_streak(pair)
        if streak >= self.config.LOSS_STREAK_PAUSE:
            logger.info(f"Pausing {pair} due to loss streak of {streak}")
            return True
        return False

    # ---------- Kill Zone ----------
    def _is_kill_zone(self) -> bool:
        now = datetime.utcnow()
        hour = now.hour
        if self.config.LONDON_KILL_ZONE_START <= hour < self.config.LONDON_KILL_ZONE_END:
            return True
        if self.config.NEW_YORK_KILL_ZONE_START <= hour < self.config.NEW_YORK_KILL_ZONE_END:
            return True
        return False

    # ---------- NEWS FILTER ----------
    def _is_news_block(self) -> bool:
        if not self.config.ENABLE_NEWS_FILTER:
            return False
        now = datetime.utcnow()
        for event in self.config.HIGH_IMPACT_NEWS:
            if event['day_of_week'] == now.weekday():
                event_time = datetime.strptime(event['time'], "%H:%M").time()
                event_dt = datetime.combine(now.date(), event_time)
                diff = abs((now - event_dt).total_seconds() / 60)
                if diff <= self.config.NEWS_BLOCK_MINUTES:
                    return True
        return False

    # ---------- SPREAD FILTER ----------
    def get_estimated_spread(self, pair: str, price: float) -> float:
        pip_value = self.config.ESTIMATED_SPREAD_PIPS.get(pair, 0.5)
        if pair in ["USD/JPY", "GBP/JPY"]:
            spread_price = pip_value * 0.01
        elif "BTC" in pair or "ETH" in pair:
            spread_price = pip_value
        else:
            spread_price = pip_value * 0.0001
        return spread_price

    def check_spread(self, pair: str, price: float) -> bool:
        spread = self.get_estimated_spread(pair, price)
        max_spread = self.config.MAX_SPREAD_PIPS
        if pair in ["USD/JPY", "GBP/JPY"]:
            max_spread_price = max_spread * 0.01
        elif "BTC" in pair or "ETH" in pair:
            max_spread_price = max_spread
        else:
            max_spread_price = max_spread * 0.0001
        if spread > max_spread_price:
            logger.info(f"Spread too high for {pair}: {spread:.5f} > {max_spread_price:.5f}")
            return False
        return True

    # ---------- SLIPPAGE ADJUSTMENT ----------
    def adjust_for_slippage(self, entry: float, direction: str, atr: float) -> float:
        slippage = atr * self.config.SLIPPAGE_FACTOR
        if direction == 'BUY':
            return entry + slippage
        else:
            return entry - slippage

    # ---------- ORDER BLOCK ----------
    def detect_order_block(self, df: pd.DataFrame, direction: str) -> tuple:
        if len(df) < 50:
            return False, 0.0

        inds = self.compute_indicators(df)
        if inds is None:
            return False, 0.0
        atr = inds['atr'].iloc[-1]
        high = df['high'].values
        low = df['low'].values

        for i in range(-30, -5):
            if i < 2 or i >= len(low)-2:
                continue
            # Swing low
            if low[i] < low[i-1] and low[i] < low[i-2] and low[i] < low[i+1] and low[i] < low[i+2]:
                max_after = max(high[i+1:i+10])
                if max_after - low[i] > 1.5 * atr:
                    return True, low[i]
            # Swing high
            if high[i] > high[i-1] and high[i] > high[i-2] and high[i] > high[i+1] and high[i] > high[i+2]:
                min_after = min(low[i+1:i+10])
                if high[i] - min_after > 1.5 * atr:
                    return True, high[i]
        return False, 0.0

    # ---------- FAIR VALUE GAP ----------
    def detect_fvg(self, df: pd.DataFrame, direction: str) -> tuple:
        if len(df) < 5:
            return False, 0, 0
        for i in range(-5, -2):
            c1 = df.iloc[i-1]
            c2 = df.iloc[i]
            c3 = df.iloc[i+1]
            # Bullish FVG: middle candle high < previous high and low > next low
            if c2['high'] < c1['high'] and c2['low'] > c3['low']:
                return True, c2['low'], c2['high']
            # Bearish FVG: middle candle low > previous low and high < next high
            if c2['low'] > c1['low'] and c2['high'] < c3['high']:
                return True, c2['low'], c2['high']
        return False, 0, 0

    # ---------- SESSION LIQUIDITY GRAB ----------
    def check_session_liquidity_grab(self, df: pd.DataFrame, direction: str) -> bool:
        if not self._is_kill_zone():
            return False
        return self.check_liquidity_sweep(df)

    # ---------- MAIN SIGNAL GENERATION ----------
    async def generate_signal(self, pair: str, htf_df: pd.DataFrame, entry_df: pd.DataFrame) -> dict:
        # Anti‑spam
        last_time = await self.db.get_last_signal_time(pair)
        now = time.time()
        if now - last_time < self.config.SIGNAL_COOLDOWN_MINUTES * 60:
            return None

        # Indicators
        htf_inds = self.compute_indicators(htf_df)
        entry_inds = self.compute_indicators(entry_df)
        if htf_inds is None or entry_inds is None:
            return None

        # Loss streak pause
        if await self.check_loss_streak(pair):
            return None

        # Noise check
        if self.check_noise(entry_df, entry_inds):
            logger.info(f"Noisy market for {pair}, skipping.")
            return None

        # Trend filter (HTF)
        htf_ema_fast = htf_inds['ema_fast'].iloc[-1]
        htf_ema_slow = htf_inds['ema_slow'].iloc[-1]
        if htf_ema_fast > htf_ema_slow:
            direction = 'BUY'
        elif htf_ema_fast < htf_ema_slow:
            direction = 'SELL'
        else:
            return None

        # Spread filter
        if not self.check_spread(pair, entry_inds['close'].iloc[-1]):
            return None

        # Session filter (kill zones)
        if self.config.ENABLE_SESSION_FILTER and not self._is_kill_zone():
            return None

        # News filter
        if self._is_news_block():
            logger.info(f"News block active, skipping {pair}")
            return None

        # Entry confirmation
        entry_close = entry_inds['close'].iloc[-1]
        entry_ema_fast = entry_inds['ema_fast'].iloc[-1]
        rsi = entry_inds['rsi'].iloc[-1]

        if direction == 'BUY':
            if not (entry_close <= entry_ema_fast * 1.005 and entry_close >= entry_ema_fast * 0.995):
                return None
            if not (30 <= rsi <= 50):
                return None
        else:
            if not (entry_close >= entry_ema_fast * 0.995 and entry_close <= entry_ema_fast * 1.005):
                return None
            if not (50 <= rsi <= 70):
                return None

        # Market structure
        structure = self.detect_market_structure(entry_df)
        if direction == 'BUY' and structure != 'uptrend':
            return None
        if direction == 'SELL' and structure != 'downtrend':
            return None

        # Liquidity sweep
        liquidity_sweep = self.check_liquidity_sweep(entry_df)

        # Volatility filter
        atr = entry_inds['atr'].iloc[-1]
        atr_avg = entry_inds['atr_avg'].iloc[-1]
        if atr <= atr_avg:
            return None

        # Avoid extreme RSI
        if (direction == 'BUY' and rsi > 75) or (direction == 'SELL' and rsi < 25):
            return None

        # Avoid volatility spike
        if atr > self.config.VOLATILITY_SPIKE_THRESHOLD * atr_avg:
            return None

        # Entry price (EMA bounce model)
        if self.config.ENTRY_MODEL == "EMA_BOUNCE":
            entry_price = self.get_entry_price_ema_bounce(entry_df, direction)
            if entry_price is None:
                return None
        else:
            entry_price = entry_close

        # Smart Money Filters (if enabled)
        if self.config.ENABLE_ORDER_BLOCK:
            ob_found, ob_level = self.detect_order_block(entry_df, direction)
            if not ob_found:
                return None
            if abs(entry_price - ob_level) > 0.5 * atr:
                return None
        if self.config.ENABLE_FVG:
            fvg_found, fvg_low, fvg_high = self.detect_fvg(entry_df, direction)
            if not fvg_found:
                return None
            if not (fvg_low <= entry_price <= fvg_high):
                return None
        if self.config.ENABLE_SESSION_GRAB:
            if not self.check_session_liquidity_grab(entry_df, direction):
                return None

        # Adjust entry for slippage (risk calculation)
        adjusted_entry = self.adjust_for_slippage(entry_price, direction, atr)

        # Risk Management
        sl = adjusted_entry - (self.config.SL_MULTIPLIER * atr) if direction == 'BUY' else adjusted_entry + (self.config.SL_MULTIPLIER * atr)
        tp = adjusted_entry + (self.config.TP_MULTIPLIER * atr) if direction == 'BUY' else adjusted_entry - (self.config.TP_MULTIPLIER * atr)
        partial_tp = adjusted_entry + (1.0 * atr) if direction == 'BUY' else adjusted_entry - (1.0 * atr)

        risk = abs(adjusted_entry - sl)
        reward = abs(tp - adjusted_entry)
        rr = reward / risk if risk > 0 else 0
        if rr < self.config.MIN_RR:
            return None

        # Scoring
        score = 3  # trend
        details = {'trend': 3}
        score += 1  # rsi
        details['rsi'] = 1
        score += 2  # structure
        details['structure'] = 2
        if liquidity_sweep:
            score += 3
            details['sweep'] = 3
        score += 1  # volatility
        details['volatility'] = 1
        if self.config.ENABLE_SESSION_FILTER and self._is_kill_zone():
            score += 1
            details['session'] = 1
        if self.config.ENABLE_ORDER_BLOCK:
            score += 2
            details['order_block'] = 2
        if self.config.ENABLE_FVG:
            score += 2
            details['fvg'] = 2
        if self.config.ENABLE_SESSION_GRAB and self.check_session_liquidity_grab(entry_df, direction):
            score += 2
            details['session_grab'] = 2

        if score < self.config.MIN_SCORE:
            return None

        # Store last signal time
        await self.db.set_last_signal_time(pair, now)

        return {
            'score': score,
            'direction': direction,
            'entry': entry_price,
            'sl': sl,
            'tp': tp,
            'partial_tp': partial_tp,
            'rr': rr,
            'details': details,
            'liquidity_sweep': liquidity_sweep,
            'structure': structure
  }
