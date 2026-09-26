#!/usr/bin/env python3
"""
XAGUSDT Bollinger + RSI Bot
Binance Futures | 15m | Paper Mode | Leverage 20x
Sanal pozisyon + Gerçek fiyat + TP %2 / SL %5 + Grafik
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
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from io import BytesIO

load_dotenv()

# ==================== AYARLAR ====================
SYMBOL          = os.getenv("SYMBOL", "XAGUSDT")
TIMEFRAME       = os.getenv("TIMEFRAME", "15m")
NOTIONAL_USDT   = float(os.getenv("NOTIONAL_USDT", "50"))
LEVERAGE        = int(os.getenv("LEVERAGE", "20"))
RSI_LENGTH      = int(os.getenv("RSI_LENGTH", "14"))
RSI_OB          = float(os.getenv("RSI_OVERBOUGHT", "65"))
RSI_OS          = float(os.getenv("RSI_OVERSOLD", "35"))
BB_LENGTH       = int(os.getenv("BB_LENGTH", "20"))
BB_STD          = float(os.getenv("BB_STD", "2.0"))
COOLDOWN        = int(os.getenv("COOLDOWN_BARS", "4"))
PAPER_MODE      = True   # Zorunlu sanal
TG_TOKEN        = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT         = os.getenv("TELEGRAM_CHAT_ID", "")

TP_PCT = 0.02
SL_PCT = 0.05

# Sanal cüzdan
paper_balance = 200.0
paper_side = None
paper_qty = 0.0
paper_entry = 0.0
paper_margin = 0.0

# ==================== LOGGING ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
log = logging.getLogger()

# ==================== TELEGRAM ====================
def send_telegram(text: str, photo_bytes: bytes = None):
    if not TG_TOKEN or not TG_CHAT:
        log.info(f"Telegram mesajı (gönderilmedi): {text[:80]}...")
        return
    try:
        if photo_bytes:
            url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
            files = {"photo": ("chart.png", photo_bytes, "image/png")}
            data = {"chat_id": TG_CHAT, "caption": text, "parse_mode": "HTML"}
            requests.post(url, data=data, files=files, timeout=30)
        else:
            url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
            requests.post(url, json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        log.warning(f"Telegram hatası: {e}")

# ==================== GRAFİK ====================
def create_chart(df: pd.DataFrame, signal: str, entry_price: float) -> bytes:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), gridspec_kw={"height_ratios": [3, 1]})

    ax1.plot(df["timestamp"], df["close"], label="Close", color="black", linewidth=1.2)
    ax1.plot(df["timestamp"], df["bb_upper"], label="BB Upper", color="red", alpha=0.7)
    ax1.plot(df["timestamp"], df["bb_middle"], label="BB Mid", color="gray", alpha=0.7)
    ax1.plot(df["timestamp"], df["bb_lower"], label="BB Lower", color="green", alpha=0.7)
    ax1.fill_between(df["timestamp"], df["bb_upper"], df["bb_lower"], color="blue", alpha=0.08)
    ax1.axhline(entry_price, color="orange", linestyle="--", linewidth=1.5, label=f"Entry {entry_price:.2f}")
    ax1.set_title(f"XAGUSDT 15m | {signal} | Entry: {entry_price:.2f} | Leverage 20x (PAPER)", fontsize=12)
    ax1.legend(loc="upper left", fontsize=9)
    ax1.grid(True, alpha=0.3)

    ax2.plot(df["timestamp"], df["rsi"], color="purple", label="RSI")
    ax2.axhline(RSI_OB, color="red", linestyle="--", alpha=0.7)
    ax2.axhline(RSI_OS, color="green", linestyle="--", alpha=0.7)
    ax2.axhline(50, color="gray", linestyle=":", alpha=0.5)
    ax2.set_ylim(0, 100)
    ax2.legend(loc="upper left", fontsize=9)
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    buf = BytesIO()
    plt.savefig(buf, format="png", dpi=110)
    buf.seek(0)
    plt.close()
    return buf.getvalue()

# ==================== EXCHANGE (sadece veri) ====================
def create_exchange():
    return ccxt.binanceusdm({
        "enableRateLimit": True,
        "options": {"defaultType": "future"}
    })

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

def check_signal(df):
    if len(df) < max(BB_LENGTH, RSI_LENGTH) + 5:
        return None
    row = df.iloc[-2]
    if row["low"] <= row["bb_lower"] and row["rsi"] <= RSI_OS:
        return "LONG"
    if row["high"] >= row["bb_upper"] and row["rsi"] >= RSI_OB:
        return "SHORT"
    return None

# ==================== PAPER POZİSYON ====================
def calculate_qty(price: float) -> float:
    return round(NOTIONAL_USDT / price, 3)

def open_paper_position(side: str, price: float, df: pd.DataFrame):
    global paper_side, paper_qty, paper_entry, paper_margin, paper_balance

    qty = calculate_qty(price)
    margin = NOTIONAL_USDT / LEVERAGE          # 50 / 20 = 2.5 USDT

    paper_side = side
    paper_qty = qty
    paper_entry = price
    paper_margin = margin

    chart = create_chart(df.tail(60), side, price)

    msg = (
        f"<b>{'🟢 LONG' if side == 'LONG' else '🔴 SHORT'} SANAL AÇILDI</b>\n\n"
        f"Sembol: <code>{SYMBOL}</code>\n"
        f"Giriş Fiyatı: <b>{price:.2f}</b>\n"
        f"Miktar: {qty} XAG\n"
        f"Notional: {NOTIONAL_USDT} USDT\n"
        f"Kaldıraç: <b>20x</b> (Isolated)\n"
        f"Kullanılan Marjin: {margin:.2f} USDT\n"
        f"TP: +%{TP_PCT*100:.0f} | SL: -%{SL_PCT*100:.0f}\n\n"
        f"Sanal Cüzdan: <b>{paper_balance:.2f} USDT</b>\n"
        f"Zaman: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    send_telegram(msg, chart)
    log.info(f"[PAPER] {side} açıldı | Entry: {price:.2f} | Qty: {qty} | Margin: {margin:.2f}")

def check_tp_sl(current_price: float) -> str | None:
    """TP veya SL’ye değdi mi?"""
    global paper_side, paper_entry
    if not paper_side:
        return None

    if paper_side == "LONG":
        tp = paper_entry * (1 + TP_PCT)
        sl = paper_entry * (1 - SL_PCT)
        if current_price >= tp:
            return "TP"
        if current_price <= sl:
            return "SL"
    else:  # SHORT
        tp = paper_entry * (1 - TP_PCT)
        sl = paper_entry * (1 + SL_PCT)
        if current_price <= tp:
            return "TP"
        if current_price >= sl:
            return "SL"
    return None

def close_paper_position(current_price: float, reason: str):
    global paper_side, paper_qty, paper_entry, paper_margin, paper_balance

    if not paper_side:
        return

    # PnL hesapla
    if paper_side == "LONG":
        pnl = (current_price - paper_entry) * paper_qty
    else:
        pnl = (paper_entry - current_price) * paper_qty

    paper_balance += pnl

    msg = (
        f"<b>{'🟢' if pnl >= 0 else '🔴'} SANAL POZİSYON KAPANDI</b>\n\n"
        f"Yön: {paper_side}\n"
        f"Giriş: {paper_entry:.2f}\n"
        f"Çıkış: {current_price:.2f}\n"
        f"Sebep: <b>{reason}</b>\n"
        f"Kar/Zarar: <b>{pnl:+.2f} USDT</b>\n\n"
        f"Yeni Sanal Cüzdan: <b>{paper_balance:.2f} USDT</b>"
    )
    send_telegram(msg)
    log.info(f"[PAPER] {paper_side} kapandı ({reason}) | PnL: {pnl:+.2f} | Bakiye: {paper_balance:.2f}")

    paper_side = None
    paper_qty = 0.0
    paper_entry = 0.0
    paper_margin = 0.0

# ==================== ANA DÖNGÜ ====================
def main():
    log.info("=" * 60)
    log.info(f"PAPER BOT BAŞLADI | {SYMBOL} 15m | Leverage 20x | Notional {NOTIONAL_USDT} USDT")
    log.info(f"Başlangıç Sanal Cüzdan: 200 USDT")
    log.info("=" * 60)
    send_telegram(
        f"🤖 <b>Sanal Bot Başladı</b>\n"
        f"{SYMBOL} | 15m | Kaldıraç 20x\n"
        f"Notional: {NOTIONAL_USDT} USDT\n"
        f"Başlangıç Cüzdan: <b>200 USDT</b>\n"
        f"Mod: <b>PAPER (Sanal)</b>"
    )

    exchange = create_exchange()
    last_signal_bar = None

    while True:
        try:
            df = fetch_ohlcv(exchange)
            df = add_indicators(df)
            current_bar = df.iloc[-2]["timestamp"]
            price = float(df.iloc[-1]["close"])  # anlık fiyat
            signal = check_signal(df)

            # 1) Açık pozisyon varsa TP/SL kontrol et
            if paper_side:
                hit = check_tp_sl(price)
                if hit:
                    close_paper_position(price, hit)

            # 2) Yeni sinyal
            can_trade = True
            if last_signal_bar is not None:
                bars = (current_bar - last_signal_bar).total_seconds() / (15 * 60)
                if bars < COOLDOWN:
                    can_trade = False

            if signal and can_trade and not paper_side:
                open_paper_position(signal, float(df.iloc[-2]["close"]), df)
                last_signal_bar = current_bar

            # Periyodik log
            now = datetime.now(timezone.utc)
            if now.minute % 15 == 0 and now.second < 20:
                status = paper_side or "Yok"
                log.info(f"Durum | Fiyat: {price:.2f} | RSI: {df.iloc[-1]['rsi']:.1f} | Pozisyon: {status} | Cüzdan: {paper_balance:.2f}")

            time.sleep(20)

        except Exception as e:
            log.exception(f"Hata: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
