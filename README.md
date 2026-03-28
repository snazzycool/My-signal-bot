# Trading Bot – High‑Probability Telegram Signal Bot

A production‑ready trading bot that scans forex and crypto markets, generates high‑quality BUY/SELL signals using a strict multi‑layer confluence system, and tracks trade outcomes.

## Final Features
- **Multi‑layer signal model**: Trend filter, pullback confirmation, market structure, liquidity sweep, volatility filter, session filter.
- **Scoring system**: Weighted scores – only trades with ≥7 points.
- **Risk management**: ATR‑based SL/TP, minimum 1:5 RR, partial TP at 1:1.
- **Anti‑spam**: Max 1 signal per pair per hour.
- **Trade tracking**: PostgreSQL database with PENDING/WIN/LOSS status.
- **Verification**: Automatic outcome check every 30 minutes.
- **Performance analytics**: Win rate, total trades, per‑pair breakdown.
- **Keep‑alive HTTP server** – prevents Render free tier from sleeping.
- **Spread filter** – estimated pips, skip if too high.
- **Slippage handling** – adjust entry price for risk calculation.
- **News filter** – block trading ±15 min around static high‑impact news events.
- **Order block detection** – smart money concept.
- **Fair Value Gap (FVG) detection** – 3‑candle imbalance zones.
- **Session liquidity grab** – liquidity sweep during kill zone only.

## Deployment on Render + Neon

1. Push this code to a GitHub repository.
2. Create a Neon PostgreSQL database and copy its connection string.
3. On Render, create a new **Web Service** connected to your repo.
4. Set environment variables:
   - `BOT_TOKEN`
   - `TWELVE_DATA_API_KEY`
   - `DATABASE_URL` (from Neon)
   - `ENABLE_SESSION_FILTER` (optional)
   - `ENABLE_NEWS_FILTER` (optional)
5. Build command: `pip install -r requirements.txt`
6. Start command: `python main.py`
7. Create a cron job (e.g., on cron-job.org) that pings `https://your-bot.onrender.com/health` every 5 minutes to keep the service awake.

## Commands
- `/start` – main menu
- `/status` – bot status

## License
MIT
