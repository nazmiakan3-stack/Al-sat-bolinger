#!/usr/bin/env python3
"""
XAGUSDT Bollinger + RSI Bot
Binance Futures | 15m | Long + Short
"""

import os
import time
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv
import ccxt
import pandas as pd
import ta
import requests

load_dotenv()

# ==================== AYARLAR ====================
API_KEY      = os.getenv("BINANCE_API_KEY", "")
API_SECRET   = os.getenv("BINANCE_API_SECRET", "")
SYMBOL       = os.getenv("SYMBOL", "XAGUSDT")
TIMEFRAME    = os.getenv("TIMEFRAME", "15m")
QUANTITY     = float(os.getenv("QUANTITY", "0.01"))
LEVERAGE     = int(os.getenv("LEVERAGE", "5"))
RSI_LENGTH   = int(os.getenv("RSI_LENGTH", "14"))
RSI_OB       = float(os.getenv("RSI_OVERBOUGHT", "65"))
RSI_OS       = float(os.getenv("RSI_OVERSOLD", "35"))
BB_LENGTH    = int(os.getenv("BB_LENGTH", "20"))
BB_STD       = float(os.getenv("BB_STD", "2.0"))
COOLDOWN     = int(os.getenv("COOLDOWN_BARS", "4"))
PAPER_MODE   = os.getenv("PAPER_MODE", "true").lower() == "true"
TG_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT      = os.getenv("TELEGRAM_CHAT_ID", "")

# ==================== LOGGING ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger()

# ==================== TELEGRAM ====================
def send_telegram(msg: str):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TG_CHAT, "text": msg}, timeout=10)
    except Exception as e:
        log.warning(f"Telegram hatası: {e}")

# ==================== EXCHANGE ====================
def create_exchange():
    exchange = ccxt.binanceusdm({
        "apiKey": API_KEY,
        "secret": API_SECRET,
        "enableRateLimit": True,
        "options": {"defaultType": "future"}
    })
    if not PAPER_MODE:
        exchange.load_markets()
        exchange.set_leverage(LEVERAGE, SYMBOL)
    return exchange

# ==================== DATA ====================
def fetch_ohlcv(exchange, limit=100):
    ohlcv = exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=limit)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df

def add_indicators(df):
    df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=RSI_LENGTH).rsi()
    bb = ta.volatility.BollingerBands(df["close"], window=BB_LENGTH, window_dev=BB_STD)
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_middle"] = bb.bollinger_mavg()
    df["bb_lower"] = bb.bollinger_lband()
    return df

# ==================== SİNYAL ====================
def check_signal(df):
    """Son kapanmış mumu kullanır (repaint yok)"""
    if len(df) < max(BB_LENGTH, RSI_LENGTH) + 5:
        return None

    # Son kapanmış mum ([-2] çünkü [-1] hala oluşuyor olabilir)
    row = df.iloc[-2]
    prev = df.iloc[-3]

    # AL (LONG)
    if row["low"] <= row["bb_lower"] and row["rsi"] <= RSI_OS:
        return "LONG"

    # SAT (SHORT)
    if row["high"] >= row["bb_upper"] and row["rsi"] >= RSI_OB:
        return "SHORT"

    return None

# ==================== POZİSYON ====================
def get_position(exchange):
    if PAPER_MODE:
        return getattr(get_position, "paper_side", None), getattr(get_position, "paper_qty", 0)

    positions = exchange.fetch_positions([SYMBOL])
    for p in positions:
        if float(p["contracts"]) > 0:
            side = "LONG" if p["side"] == "long" else "SHORT"
            return side, float(p["contracts"])
    return None, 0

def close_position(exchange, side, qty):
    if PAPER_MODE:
        log.info(f"[PAPER] Pozisyon kapatıldı: {side}")
        get_position.paper_side = None
        get_position.paper_qty = 0
        return True

    opposite = "sell" if side == "LONG" else "buy"
    order = exchange.create_order(
        SYMBOL, "market", opposite, qty,
        params={"reduceOnly": True}
    )
    log.info(f"Pozisyon kapatıldı: {side} | {order['id']}")
    return True

def open_position(exchange, side, qty):
    if PAPER_MODE:
        log.info(f"[PAPER] Yeni pozisyon açıldı: {side} | Miktar: {qty}")
        get_position.paper_side = side
        get_position.paper_qty = qty
        return True

    order_side = "buy" if side == "LONG" else "sell"
    order = exchange.create_order(SYMBOL, "market", order_side, qty)
    log.info(f"Yeni pozisyon açıldı: {side} | {order['id']}")
    return True

# ==================== ANA DÖNGÜ ====================
def main():
    log.info("=" * 50)
    log.info(f"Bot başlatıldı | {SYMBOL} | {TIMEFRAME} | PAPER={PAPER_MODE}")
    log.info("=" * 50)
    send_telegram(f"🤖 Bot başladı\n{SYMBOL} {TIMEFRAME}\nPAPER={PAPER_MODE}")

    exchange = create_exchange()
    last_signal_bar = None
    last_side = None

    while True:
        try:
            df = fetch_ohlcv(exchange)
            df = add_indicators(df)

            current_bar = df.iloc[-2]["timestamp"]
            signal = check_signal(df)

            pos_side, pos_qty = get_position(exchange)

            # Cooldown kontrolü
            can_trade = True
            if last_signal_bar is not None:
                bars_passed = (current_bar - last_signal_bar).total_seconds() / (15 * 60)
                if bars_passed < COOLDOWN:
                    can_trade = False

            if signal and can_trade:
                log.info(f"Sinyal: {signal} | RSI={df.iloc[-2]['rsi']:.1f} | Close={df.iloc[-2]['close']:.2f}")

                # Ters sinyal → kapat + aç
                if pos_side and pos_side != signal:
                    close_position(exchange, pos_side, pos_qty)
                    time.sleep(1)
                    open_position(exchange, signal, QUANTITY)
                    last_side = signal
                    last_signal_bar = current_bar
                    send_telegram(f"🔄 {pos_side} kapatıldı → {signal} açıldı\nFiyat: {df.iloc[-2]['close']:.2f}")

                # Pozisyon yoksa aç
                elif not pos_side:
                    open_position(exchange, signal, QUANTITY)
                    last_side = signal
                    last_signal_bar = current_bar
                    send_telegram(f"✅ {signal} açıldı\nFiyat: {df.iloc[-2]['close']:.2f}\nRSI: {df.iloc[-2]['rsi']:.1f}")

            # Her 15 dakikada bir durum logla
            if datetime.now(timezone.utc).minute % 15 == 0 and datetime.now(timezone.utc).second < 10:
                log.info(f"Durum | Fiyat: {df.iloc[-1]['close']:.2f} | RSI: {df.iloc[-1]['rsi']:.1f} | Pozisyon: {pos_side or 'Yok'}")

            time.sleep(30)  # 30 saniyede bir kontrol

        except ccxt.NetworkError as e:
            log.error(f"Ağ hatası: {e}")
            time.sleep(60)
        except ccxt.ExchangeError as e:
            log.error(f"Borsa hatası: {e}")
            time.sleep(60)
        except Exception as e:
            log.exception(f"Beklenmeyen hata: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
