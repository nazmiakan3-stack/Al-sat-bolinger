#!/usr/bin/env python3
"""
Multi-Asset Bollinger + RSI Bot
Binance Futures | 15m | Paper Mode | Leverage 20x
10 Assets (Crypto, Gold, Silver) | TradingView-style Charts with Highs/Lows
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
import matplotlib.pyplot as plt
import mplfinance as mpf  # TradingView-style charts
from io import BytesIO

load_dotenv()

# ==================== AYARLAR ====================
# İşlem yapılacak varlıklar (8 Kripto + Gümüş + Altın)
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
    "ARBUSDT", "DOGEUSDT", "XRPUSDT", "MATICUSDT",
    "XAUUSDT", "XAGUSDT"
]
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

# Sanal cüzdan ve pozisyon bilgileri (her varlık için ayrı)
class PaperPosition:
    def __init__(self, initial_balance=200.0):
        self.balance = initial_balance
        self.side = None
        self.qty = 0.0
        self.entry = 0.0
        self.margin = 0.0
        self.last_signal_bar = None

# Varlık bazlı pozisyon takibi
paper_positions = {symbol: PaperPosition() for symbol in SYMBOLS}

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
def create_chart(symbol: str, df: pd.DataFrame, signal: str, entry_price: float) -> bytes:
    # `mplfinance` için DataFrame'i hazırla
    plot_df = df.set_index("timestamp")
    
    # Tepe ve dip noktalarını belirle
    highs = plot_df[plot_df["high"] == plot_df["high"].rolling(10, center=True).max()]["high"]
    lows = plot_df[plot_df["low"] == plot_df["low"].rolling(10, center=True).min()]["low"]
    
    # Grafiği oluştur (mplfinance)
    mc = mpf.make_marketcolors(up='#17cf2d', down='#ff2e2e', inherit=True)
    s  = mpf.make_mpf_style(base_mpf_style='nightclouds', marketcolors=mc)
    
    # Addplot ile tepe ve dip noktalarını ekle
    ap_plots = [
        mpf.make_addplot(highs, type='scatter', color='lime', marker='^', markersize=10),
        mpf.make_addplot(lows, type='scatter', color='tomato', marker='v', markersize=10)
    ]
    
    fig, axlist = mpf.plot(plot_df, type='candle', style=s,
                            addplot=ap_plots,
                            title=f"{symbol} 15m | {signal} | Entry: {entry_price:.2f} | Leverage 20x (PAPER)",
                            ylabel='Price', ylabel_lower='RSI',
                            volume=False, # Hacim grafiği eklemek isterseniz True yapın
                            panel_ratios=(3, 1), # Fiyat ve RSI panelleri arası oran
                            show_nontrading=False,
                            returnfig=True)
    
    # Fiyat grafiğine Bollinger bantlarını ekle
    ax_price = axlist[0]
    ax_price.plot(df["bb_upper"], label="BB Upper", color="red", alpha=0.7)
    ax_price.plot(df["bb_middle"], label="BB Mid", color="gray", alpha=0.7)
    ax_price.plot(df["bb_lower"], label="BB Lower", color="green", alpha=0.7)
    ax_price.fill_between(range(len(df)), df["bb_upper"], df["bb_lower"], color="blue", alpha=0.08)
    ax_price.axhline(entry_price, color="orange", linestyle="--", linewidth=1.5, label=f"Entry {entry_price:.2f}")
    ax_price.legend(loc="upper left", fontsize=9)
    ax_price.grid(True, alpha=0.3)
    
    # RSI paneline RSI çizgisini ve eşiklerini ekle
    ax_rsi = axlist[2]
    ax_rsi.plot(df["rsi"], color="purple", label="RSI")
    ax_rsi.axhline(RSI_OB, color="red", linestyle="--", alpha=0.7)
    ax_rsi.axhline(RSI_OS, color="green", linestyle="--", alpha=0.7)
    ax_rsi.axhline(50, color="gray", linestyle=":", alpha=0.5)
    ax_rsi.set_ylim(0, 100)
    ax_rsi.legend(loc="upper left", fontsize=9)
    ax_rsi.grid(True, alpha=0.3)

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
def fetch_ohlcv(exchange, symbol, limit=100):
    ohlcv = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=limit)
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

def open_paper_position(symbol: str, side: str, price: float, df: pd.DataFrame):
    pos = paper_positions[symbol]
    
    qty = calculate_qty(price)
    margin = NOTIONAL_USDT / LEVERAGE          # 50 / 20 = 2.5 USDT

    pos.side = side
    pos.qty = qty
    pos.entry = price
    pos.margin = margin

    chart = create_chart(symbol, df.tail(60), side, price)

    msg = (
        f"<b>[{symbol}] {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'} SANAL AÇILDI</b>\n\n"
        f"Giriş Fiyatı: <b>{price:.2f}</b>\n"
        f"Miktar: {qty} {symbol.split('USDT')[0]}\n"
        f"Notional: {NOTIONAL_USDT} USDT\n"
        f"Kaldıraç: <b>20x</b> (Isolated)\n"
        f"Kullanılan Marjin: {margin:.2f} USDT\n"
        f"TP: +%{TP_PCT*100:.0f} | SL: -%{SL_PCT*100:.0f}\n\n"
        f"Sanal Cüzdan: <b>{pos.balance:.2f} USDT</b>\n"
        f"Zaman: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    send_telegram(msg, chart)
    log.info(f"[{symbol}] [PAPER] {side} açıldı | Entry: {price:.2f} | Qty: {qty} | Margin: {margin:.2f}")

def check_tp_sl(symbol: str, current_price: float) -> str | None:
    """TP veya SL’ye değdi mi?"""
    pos = paper_positions[symbol]
    if not pos.side:
        return None

    if pos.side == "LONG":
        tp = pos.entry * (1 + TP_PCT)
        sl = pos.entry * (1 - SL_PCT)
        if current_price >= tp:
            return "TP"
        if current_price <= sl:
            return "SL"
    else:  # SHORT
        tp = pos.entry * (1 - TP_PCT)
        sl = pos.entry * (1 + SL_PCT)
        if current_price <= tp:
            return "TP"
        if current_price >= sl:
            return "SL"
    return None

def close_paper_position(symbol: str, current_price: float, reason: str):
    pos = paper_positions[symbol]
    
    if not pos.side:
        return

    # PnL hesapla
    if pos.side == "LONG":
        pnl = (current_price - pos.entry) * pos.qty
    else:
        pnl = (pos.entry - current_price) * pos.qty

    pos.balance += pnl

    msg = (
        f"<b>[{symbol}] {'🟢' if pnl >= 0 else '🔴'} SANAL POZİSYON KAPANDI</b>\n\n"
        f"Yön: {pos.side}\n"
        f"Giriş: {pos.entry:.2f}\n"
        f"Çıkış: {current_price:.2f}\n"
        f"Sebep: <b>{reason}</b>\n"
        f"Kar/Zarar: <b>{pnl:+.2f} USDT</b>\n\n"
        f"Yeni Sanal Cüzdan: <b>{pos.balance:.2f} USDT</b>"
    )
    send_telegram(msg)
    log.info(f"[{symbol}] [PAPER] {pos.side} kapandı ({reason}) | PnL: {pnl:+.2f} | Bakiye: {pos.balance:.2f}")

    pos.side = None
    pos.qty = 0.0
    pos.entry = 0.0
    pos.margin = 0.0

# ==================== ANA DÖNGÜ ====================
def main():
    # Dosya adını otomatik olarak alıyoruz
    script_name = os.path.basename(__file__)
    
    log.info("=" * 60)
    log.info(f"MULTI-ASSET PAPER BOT STARTED [{script_name}] | Pairs: {', '.join(SYMBOLS)}")
    log.info(f"Base Balance: 200 USDT per Asset (2000 USDT total)")
    log.info("=" * 60)
    
    # Telegram mesajına dosya adını ekledik
    send_telegram(
        f"🤖 <b>Multi-Asset Sanal Bot Başladı</b>\n\n"
        f"📁 <b>Çalışan Dosya:</b> <code>{script_name}</code>\n"
        f"📈 <b>Varlıklar:</b> {', '.join(SYMBOLS)}\n"
        f"💰 <b>Notional:</b> {NOTIONAL_USDT} USDT\n"
        f"💼 <b>Cüzdan:</b> 200 USDT (Her Varlık İçin)\n"
        f"⚙️ <b>Mod:</b> PAPER (Sanal)"
    )

    exchange = create_exchange()

    while True:
        try:
            for symbol in SYMBOLS:
                pos = paper_positions[symbol]
                df = fetch_ohlcv(exchange, symbol)
                df = add_indicators(df)
                current_bar = df.iloc[-2]["timestamp"]
                price = float(df.iloc[-1]["close"])  # anlık fiyat
                signal = check_signal(df)

                # 1) Açık pozisyon varsa TP/SL kontrol et
                if pos.side:
                    hit = check_tp_sl(symbol, price)
                    if hit:
                        close_paper_position(symbol, price, hit)

                # 2) Yeni sinyal
                can_trade = True
                if pos.last_signal_bar is not None:
                    bars = (current_bar - pos.last_signal_bar).total_seconds() / (15 * 60)
                    if bars < COOLDOWN:
                        can_trade = False

                if signal and can_trade and not pos.side:
                    open_paper_position(symbol, signal, float(df.iloc[-2]["close"]), df)
                    pos.last_signal_bar = current_bar

                # Periyodik log
                now = datetime.now(timezone.utc)
                if now.minute % 15 == 0 and now.second < 20:
                    status = pos.side or "None"
                    log.info(f"[{symbol}] Status | Price: {price:.2f} | RSI: {df.iloc[-1]['rsi']:.1f} | Position: {status} | Wallet: {pos.balance:.2f}")

            time.sleep(20)

        except Exception as e:
            log.exception(f"Error for {symbol}: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
