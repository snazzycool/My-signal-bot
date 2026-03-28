def format_signal_message(signal: dict, pair: str) -> str:
    score = signal['score']
    max_score = 11
    confidence = "LOW"
    if score >= 9:
        confidence = "VERY HIGH"
    elif score >= 7:
        confidence = "HIGH"
    elif score >= 5:
        confidence = "MEDIUM"

    msg = (
        f"🚨 **TRADE SIGNAL**\n"
        f"Pair: {pair}\n"
        f"Direction: {signal['direction']}\n"
        f"Entry: {signal['entry']:.5f}\n"
        f"SL: {signal['sl']:.5f}\n"
        f"TP: {signal['tp']:.5f}\n"
        f"Partial TP (1:1): {signal['partial_tp']:.5f}\n"
        f"RR: 1:{signal['rr']:.2f}\n"
        f"Confidence: **{confidence}** ({score}/{max_score})\n"
        f"Structure: {signal.get('structure', 'N/A')}"
    )
    if signal.get('liquidity_sweep'):
        msg += "\n🕯️ *Liquidity Sweep Detected*"
    return msg
