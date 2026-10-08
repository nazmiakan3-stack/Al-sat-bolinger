import yfinance as yf
import pandas as pd
import pandas_ta as ta
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.gridspec import GridSpec
import io
import time
import requests
from datetime import datetime
import warnings
warnings.filterwarnings("ignore")

# ======================== AYARLAR ========================
TELEGRAM_TOKEN = "BURAYA_BOT_TOKEN_YAZ"
TELEGRAM_CHAT_ID = "BURAYA_CHAT_ID_YAZ"

SYMBOLS = {
    "ALTIN (GC=F)": "GC=F",
    "GUMUS (SI=F)": "SI=F"
}

INTERVAL = "1m"
LOOKBACK = 120
VOLUME_LOOKBACK = 20
EMA_PERIODS = [9, 21, 50]
RSI_PERIOD = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9

SEND_CHART_EVERY_MINUTE = True
# ========================================================


def send_telegram_photo(photo_bytes, caption=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    files = {"photo": ("chart.png", photo_bytes, "image/png")}
    data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"}
    try:
        r = requests.post(url, files=files, data=data, timeout=30)
        return r.status_code == 200
    except Exception as e:
        print("Telegram fotoğraf hatası:", e)
        return False


def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, data=data, timeout=15)
    except Exception as e:
        print("Telegram mesaj hatası:", e)


def get_data(ticker):
    try:
        df = yf.download(ticker, period="2d", interval=INTERVAL, progress=False, auto_adjust=True)
        if df.empty or len(df) < 60:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [c.lower() for c in df.columns]
        return df.tail(LOOKBACK).copy()
    except Exception as e:
        print(f"Veri çekme hatası ({ticker}):", e)
        return None


def calculate_indicators(df):
    for p in EMA_PERIODS:
        df[f"ema_{p}"] = ta.ema(df["close"], length=p)
    df["rsi"] = ta.rsi(df["close"], length=RSI_PERIOD)
    macd = ta.macd(df["close"], fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL)
    df["dif"] = macd[f"MACD_{MACD_FAST}_{MACD_SLOW}_{MACD_SIGNAL}"]
    df["dea"] = macd[f"MACDs_{MACD_FAST}_{MACD_SLOW}_{MACD_SIGNAL}"]
    df["hist"] = macd[f"MACDh_{MACD_FAST}_{MACD_SLOW}_{MACD_SIGNAL}"]
    return df.dropna()


def find_all_signals(df):
    """
    Geçmiş tüm LONG ve SHORT noktalarını bulur.
    Dönen: list of (index, 'LONG'/'SHORT')
    """
    signals = []
    if len(df) < VOLUME_LOOKBACK + 5:
        return signals

    for i in range(VOLUME_LOOKBACK, len(df)):
        window = df.iloc[i - VOLUME_LOOKBACK : i + 1]
        current = df.iloc[i]

        vol_max = window["volume"].max()
        vol_min = window["volume"].min()

        # SHORT
        if current["dif"] > 0 and current["volume"] >= vol_max * 0.98:
            signals.append((df.index[i], "SHORT"))

        # LONG
        elif current["dea"] < 0 and current["volume"] <= vol_min * 1.02:
            signals.append((df.index[i], "LONG"))

    return signals


def check_latest_signal(df):
    """Sadece en son mumu kontrol eder (mesaj için)"""
    signals = find_all_signals(df)
    if not signals:
        return None, ""
    last_idx, last_type = signals[-1]
    if last_idx == df.index[-1]:
        last = df.iloc[-1]
        if last_type == "SHORT":
            reason = f"DIF pozitif ({last['dif']:.4f}) + Hacim en yüksek"
        else:
            reason = f"DEA negatif ({last['dea']:.4f}) + Hacim en düşük"
        return last_type, reason
    return None, ""


def create_chart(df, symbol_name, all_signals, latest_signal=None):
    fig = plt.figure(figsize=(14, 11), facecolor="#121212")
    gs = GridSpec(4, 1, height_ratios=[3.2, 1, 1, 1.3], hspace=0.06)

    ax_price = fig.add_subplot(gs[0])
    ax_vol   = fig.add_subplot(gs[1], sharex=ax_price)
    ax_rsi   = fig.add_subplot(gs[2], sharex=ax_price)
    ax_macd  = fig.add_subplot(gs[3], sharex=ax_price)

    for ax in [ax_price, ax_vol, ax_rsi, ax_macd]:
        ax.set_facecolor("#1a1a1a")
        ax.tick_params(colors="#cccccc", labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#333333")

    # ----- Fiyat + EMA -----
    ax_price.plot(df.index, df["close"], color="#00d4ff", linewidth=1.6, label="Fiyat", zorder=2)
    ema_colors = ["#ffd700", "#ff6b6b", "#4ecdc4"]
    for i, p in enumerate(EMA_PERIODS):
        ax_price.plot(df.index, df[f"ema_{p}"], color=ema_colors[i],
                      linewidth=1.2, label=f"EMA {p}", alpha=0.9, zorder=2)

    # ----- LONG / SHORT işaretleri -----
    for idx, sig_type in all_signals:
        price = df.loc[idx, "close"]
        if sig_type == "LONG":
            ax_price.scatter(idx, price, marker="^", color="#00ff99", s=120,
                             zorder=5, edgecolors="white", linewidths=0.8, label="_nolegend_")
        else:
            ax_price.scatter(idx, price, marker="v", color="#ff4444", s=120,
                             zorder=5, edgecolors="white", linewidths=0.8, label="_nolegend_")

    # En son sinyal büyük vurgulu
    if latest_signal:
        color = "#ff4444" if latest_signal == "SHORT" else "#00ff99"
        ax_price.annotate(
            f"★ {latest_signal}",
            xy=(df.index[-1], df["close"].iloc[-1]),
            xytext=(12, 30), textcoords="offset points",
            color=color, fontsize=13, fontweight="bold",
            arrowprops=dict(arrowstyle="->", color=color, lw=1.8),
            zorder=6
        )

    # Legend'e manuel ekleme
    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], color="#00d4ff", lw=1.6, label="Fiyat"),
        Line2D([0], [0], color="#ffd700", lw=1.2, label="EMA 9"),
        Line2D([0], [0], color="#ff6b6b", lw=1.2, label="EMA 21"),
        Line2D([0], [0], color="#4ecdc4", lw=1.2, label="EMA 50"),
        Line2D([0], [0], marker="^", color="w", markerfacecolor="#00ff99",
               markersize=10, label="LONG", linestyle="None"),
        Line2D([0], [0], marker="v", color="w", markerfacecolor="#ff4444",
               markersize=10, label="SHORT", linestyle="None"),
    ]
    ax_price.legend(handles=legend_elements, loc="upper left", facecolor="#2a2a2a",
                    labelcolor="white", fontsize=8, framealpha=0.85)

    ax_price.set_ylabel("Fiyat", color="#cccccc")
    ax_price.set_title(
        f"{symbol_name}  |  1 Dakika  |  {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        color="white", fontsize=13, pad=8
    )

    # ----- Hacim -----
    vol_colors = ["#26a69a" if c >= o else "#ef5350" for c, o in zip(df["close"], df["open"])]
    ax_vol.bar(df.index, df["volume"], color=vol_colors, width=0.0007, alpha=0.85)
    ax_vol.set_ylabel("Hacim", color="#cccccc")

    # ----- RSI -----
    ax_rsi.plot(df.index, df["rsi"], color="#e040fb", linewidth=1.4)
    ax_rsi.axhline(70, color="#ef5350", linestyle="--", alpha=0.7)
    ax_rsi.axhline(30, color="#26a69a", linestyle="--", alpha=0.7)
    ax_rsi.axhline(50, color="#666666", linestyle=":", alpha=0.5)
    ax_rsi.set_ylim(0, 100)
    ax_rsi.set_ylabel("RSI", color="#cccccc")
    ax_rsi.fill_between(df.index, 70, 100, color="#ef5350", alpha=0.08)
    ax_rsi.fill_between(df.index, 0, 30, color="#26a69a", alpha=0.08)

    # ----- MACD -----
    ax_macd.plot(df.index, df["dif"], color="#00bcd4", linewidth=1.3, label="DIF")
    ax_macd.plot(df.index, df["dea"], color="#ff9800", linewidth=1.3, label="DEA")
    hist_colors = ["#26a69a" if h >= 0 else "#ef5350" for h in df["hist"]]
    ax_macd.bar(df.index, df["hist"], color=hist_colors, width=0.0007, alpha=0.7)
    ax_macd.axhline(0, color="#555555", linewidth=0.8)
    ax_macd.legend(loc="upper left", facecolor="#2a2a2a", labelcolor="white",
                   fontsize=8, framealpha=0.8)
    ax_macd.set_ylabel("MACD", color="#cccccc")

    ax_macd.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    plt.setp(ax_price.get_xticklabels(), visible=False)
    plt.setp(ax_vol.get_xticklabels(), visible=False)
    plt.setp(ax_rsi.get_xticklabels(), visible=False)

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor())
    buf.seek(0)
    plt.close(fig)
    return buf


def process_symbol(name, ticker):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {name} kontrol ediliyor...")
    df = get_data(ticker)
    if df is None:
        print("  → Veri alınamadı")
        return

    df = calculate_indicators(df)
    all_signals = find_all_signals(df)
    latest_signal, reason = check_latest_signal(df)

    last = df.iloc[-1]
    caption = (
        f"<b>{name}</b>\n"
        f"Fiyat: <b>{last['close']:.2f}</b>\n"
        f"RSI: {last['rsi']:.1f}  |  DIF: {last['dif']:.4f}  |  DEA: {last['dea']:.4f}\n"
    )

    if latest_signal:
        emoji = "🔴 <b>SHORT SİNYAL</b>" if latest_signal == "SHORT" else "🟢 <b>LONG SİNYAL</b>"
        caption += f"\n{emoji}\n{reason}"
        print(f"  ★ {latest_signal} → {reason}")
    else:
        print(f"  Sinyal yok | Fiyat: {last['close']:.2f} | Toplam işaret: {len(all_signals)}")

    if SEND_CHART_EVERY_MINUTE or latest_signal:
        chart = create_chart(df, name, all_signals, latest_signal)
        if send_telegram_photo(chart, caption):
            print("  → Telegram'a gönderildi")
        else:
            print("  → Telegram gönderilemedi")
    elif latest_signal:
        send_telegram_message(caption)


def main():
    print("=" * 55)
    print("  VADELİ ALTIN & GÜMÜŞ 1DK TAKİP BOTU + İŞARETLER")
    print("  Ctrl+C ile durdurabilirsiniz")
    print("=" * 55)

    send_telegram_message(
        "🟢 <b>Vadeli Altın & Gümüş botu aktif!</b>\n"
        "LONG ▲  ve  SHORT ▼  noktaları grafikte işaretleniyor."
    )

    while True:
        try:
            for name, ticker in SYMBOLS.items():
                process_symbol(name, ticker)
                time.sleep(3)
            print("60 saniye bekleniyor...\n")
            time.sleep(60)
        except KeyboardInterrupt:
            print("\nBot durduruldu.")
            send_telegram_message("🔴 Bot durduruldu.")
            break
        except Exception as e:
            print("Genel hata:", e)
            time.sleep(20)


if __name__ == "__main__":
    main()
