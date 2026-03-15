"""Market analysis generator — builds a text summary from indicator data."""

import time


def generate_market_analysis(indicators: dict, scores: tuple[float, float], config) -> dict:
    """
    Generate a market analysis summary from current indicators.
    Returns dict with 'summary', 'bias', 'conditions' list, and 'timestamp'.
    """
    if not indicators:
        return {
            "summary": "Esperando datos del mercado...",
            "bias": "neutral",
            "conditions": [],
            "timestamp": time.time(),
        }

    conditions = []
    bias_points = 0  # positive = bullish, negative = bearish

    # ─── 1. Trend (HTF EMA) ───
    htf_f = indicators.get("htf_ema_fast", 0)
    htf_s = indicators.get("htf_ema_slow", 0)
    ema_f = indicators.get("ema_fast", 0)
    ema_s = indicators.get("ema_slow", 0)

    if htf_f > 0 and htf_s > 0:
        if htf_f > htf_s:
            if ema_f > ema_s:
                conditions.append("Tendencia alcista en ambos timeframes (1m y 5m proxy)")
                bias_points += 3
            else:
                conditions.append("Tendencia macro alcista pero corto plazo bajista — posible retroceso")
                bias_points += 1
        else:
            if ema_f < ema_s:
                conditions.append("Tendencia bajista en ambos timeframes (1m y 5m proxy)")
                bias_points -= 3
            else:
                conditions.append("Tendencia macro bajista pero corto plazo alcista — posible rebote tecnico")
                bias_points -= 1

    # ─── 2. Momentum (RSI) ───
    rsi = indicators.get("rsi", 50)
    if rsi < 20:
        conditions.append(f"RSI en zona de sobreventa extrema ({rsi:.0f}) — alta probabilidad de rebote")
        bias_points += 2
    elif rsi < 30:
        conditions.append(f"RSI en sobreventa ({rsi:.0f}) — presion vendedora agotandose")
        bias_points += 1
    elif rsi > 80:
        conditions.append(f"RSI en sobrecompra extrema ({rsi:.0f}) — alta probabilidad de correccion")
        bias_points -= 2
    elif rsi > 70:
        conditions.append(f"RSI en sobrecompra ({rsi:.0f}) — presion compradora agotandose")
        bias_points -= 1
    elif 45 <= rsi <= 55:
        conditions.append(f"RSI neutral ({rsi:.0f}) — mercado sin direccion clara")

    # ─── 3. MACD Momentum ───
    macd_h = indicators.get("macd_histogram", 0)
    macd = indicators.get("macd", 0)
    if macd_h > 0 and macd > 0:
        conditions.append("MACD positivo con histograma creciente — momentum alcista fuerte")
        bias_points += 1
    elif macd_h > 0:
        conditions.append("Histograma MACD positivo — momentum mejorando")
        bias_points += 0.5
    elif macd_h < 0 and macd < 0:
        conditions.append("MACD negativo con histograma decreciente — momentum bajista fuerte")
        bias_points -= 1
    elif macd_h < 0:
        conditions.append("Histograma MACD negativo — momentum debilitandose")
        bias_points -= 0.5

    # ─── 4. Volatility (ATR + BB) ───
    atr_pct = indicators.get("atr_pct", 0) * 100
    bb_width = indicators.get("bb_width", 0) * 100

    if bb_width < 0.2:
        conditions.append(f"Bollinger Bands en squeeze (ancho: {bb_width:.2f}%) — explosion de volatilidad inminente")
    elif bb_width > 0.8:
        conditions.append(f"Bandas de Bollinger expandidas ({bb_width:.2f}%) — alta volatilidad activa")

    if atr_pct > 0.4:
        conditions.append(f"ATR alto ({atr_pct:.2f}%) — movimientos amplios, cuidado con stops ajustados")
    elif atr_pct < 0.1:
        conditions.append(f"ATR bajo ({atr_pct:.2f}%) — mercado comprimido, poco movimiento")

    # ─── 5. Volume ───
    vol_ratio = indicators.get("volume_ratio", 1)
    vol_delta = indicators.get("volume_delta", 0)

    if vol_ratio > 2.0:
        direction = "comprador" if vol_delta > 0.1 else "vendedor" if vol_delta < -0.1 else "mixto"
        conditions.append(f"Volumen {vol_ratio:.1f}x por encima del promedio — interes {direction} elevado")
        bias_points += 1 if vol_delta > 0 else -1 if vol_delta < 0 else 0
    elif vol_ratio > 1.3:
        conditions.append(f"Volumen moderadamente alto ({vol_ratio:.1f}x) — actividad creciente")
    elif vol_ratio < 0.5:
        conditions.append(f"Volumen muy bajo ({vol_ratio:.1f}x) — mercado sin interes, evitar trades")
    elif vol_ratio < 0.8:
        conditions.append(f"Volumen por debajo del promedio ({vol_ratio:.1f}x) — baja participacion")

    if abs(vol_delta) > 0.3:
        side = "compradores" if vol_delta > 0 else "vendedores"
        conditions.append(f"Delta de volumen dominado por {side} ({vol_delta*100:+.0f}%)")

    # ─── 6. Orderbook ───
    imb = indicators.get("imbalance", 0)
    if imb > 0.25:
        conditions.append(f"Orderbook fuertemente dominado por bids ({imb*100:.0f}%) — soporte comprador")
        bias_points += 1
    elif imb > 0.10:
        conditions.append(f"Orderbook ligeramente bid-heavy ({imb*100:.0f}%)")
    elif imb < -0.25:
        conditions.append(f"Orderbook fuertemente dominado por asks ({imb*100:.0f}%) — presion vendedora")
        bias_points -= 1
    elif imb < -0.10:
        conditions.append(f"Orderbook ligeramente ask-heavy ({imb*100:.0f}%)")

    # ─── 7. BB Position ───
    bb_pos = indicators.get("bb_position", 0.5)
    if bb_pos < 0.10:
        conditions.append("Precio en la banda inferior de Bollinger — zona de posible rebote")
    elif bb_pos > 0.90:
        conditions.append("Precio en la banda superior de Bollinger — zona de posible rechazo")

    # ─── 8. Consecutive candles (exhaustion) ───
    cg = indicators.get("consecutive_green", 0)
    cr = indicators.get("consecutive_red", 0)
    if cg >= 5:
        conditions.append(f"{cg} velas verdes consecutivas — riesgo de agotamiento alcista")
        bias_points -= 0.5
    elif cr >= 5:
        conditions.append(f"{cr} velas rojas consecutivas — riesgo de agotamiento bajista")
        bias_points += 0.5

    # ─── Build summary ───
    if bias_points >= 3:
        bias = "bullish"
        emoji = "+"
        summary = "Mercado con sesgo fuertemente alcista."
    elif bias_points >= 1.5:
        bias = "bullish"
        emoji = "+"
        summary = "Mercado con sesgo alcista moderado."
    elif bias_points <= -3:
        bias = "bearish"
        emoji = "-"
        summary = "Mercado con sesgo fuertemente bajista."
    elif bias_points <= -1.5:
        bias = "bearish"
        emoji = "-"
        summary = "Mercado con sesgo bajista moderado."
    else:
        bias = "neutral"
        emoji = "~"
        summary = "Mercado lateral sin direccion clara."

    # Add scoring context
    long_s, short_s = scores
    if long_s >= 3.0 and long_s > short_s:
        summary += f" Senal LONG activa (score: {long_s:.1f})."
    elif short_s >= 3.0 and short_s > long_s:
        summary += f" Senal SHORT activa (score: {short_s:.1f})."
    else:
        max_s = max(long_s, short_s)
        if max_s > 0:
            summary += f" Sin senal suficiente (mejor score: {max_s:.1f}/3.0)."

    # Chop detection
    if bb_width < 0.2 and 45 < rsi < 55 and vol_ratio < 0.8:
        summary += " Mercado choppy — mejor esperar."

    return {
        "summary": summary,
        "bias": bias,
        "conditions": conditions[:8],  # Max 8 conditions for dashboard
        "bias_score": round(bias_points, 1),
        "timestamp": time.time(),
    }
