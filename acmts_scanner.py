#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MarginPad Multi-Strategy Scanner — VolTarget + Donchian Breakout
================================================================
Strategy A: VolTarget (MACD 6,19,2 + Pivot 8 + QQE 10,5) — LONG/SHORT
Strategy B: Donchian(8/2) + Vol(1.5×MA20) + EMA(150) — LONG + SHORT (mirrored)
            SL = 3×ATR(14), TP = 5R (safety net; primary exit = Donchian signal)

هر ۳ پروفایل هر استراتژی در تلگرام نمایش داده می‌شود، فقط پروفایل فعال معامله باز می‌کند.
"""

import os
import sys
import json
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

# ============================================================
# VolTarget PROFILES
# ============================================================
PROFILES = {
    "conservative": {"name": "🛡️ محافظه‌کار", "target_vol": 0.20,
                     "size_min": 0.10, "size_max": 0.60},
    "balanced":     {"name": "⭐ متعادل",     "target_vol": 0.30,
                     "size_min": 0.10, "size_max": 0.75},
    "aggressive":   {"name": "🚀 تهاجمی",     "target_vol": 0.40,
                     "size_min": 0.10, "size_max": 0.90},
}
ACTIVE_PROFILE_KEY = "balanced"
ACTIVE_PROFILE = PROFILES[ACTIVE_PROFILE_KEY]

VOL_FLOOR = 0.15
VOL_CEIL = 0.80

# ============================================================
# Donchian PROFILES
# ============================================================
DONCHIAN_PROFILES = {
    "spot_safe": {"name": "🛡 Spot-safe", "sf": 0.25, "leverage": 1.0},
    "lev_2x":    {"name": "⚡ 2x Lev",    "sf": 0.50, "leverage": 1.5},
    "lev_3x":    {"name": "🚀 3x Lev",    "sf": 0.75, "leverage": 2.25},
}
ACTIVE_DONCHIAN_PROFILE_KEY = os.environ.get("DONCHIAN_PROFILE", "spot_safe").strip()
if ACTIVE_DONCHIAN_PROFILE_KEY not in DONCHIAN_PROFILES:
    ACTIVE_DONCHIAN_PROFILE_KEY = "spot_safe"
ACTIVE_DONCHIAN_PROFILE = DONCHIAN_PROFILES[ACTIVE_DONCHIAN_PROFILE_KEY]

# ============================================================
# ENV
# ============================================================
API_KEY = os.environ.get("MARGINPAD_API_KEY", "mpb_d04c938486b9f5e711f9a736084fa9b5").strip()
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8601458987:AAEvcxx2ClOTfQIqEp9Ok6TwcvKRaMfIeqo").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

if not API_KEY:
    print("FATAL: MARGINPAD_API_KEY missing")
    sys.exit(1)

BASE = "https://marginpad.io/api/bot/v1"
HEADERS = {"X-API-Key": API_KEY}
H_JSON = {**HEADERS, "Content-Type": "application/json"}

# ============================================================
# SYMBOLS
# ============================================================
SYMBOLS = [
    "ADA", "AVAX", "LINK", "LTC",
    "ENA", "ATOM", "NEAR", "ARB", "OP", "PEPE", "SHIB", "SUI",
    "APT", "INJ", "TIA", "SEI", "WIF", "LDO", "UNI", "AAVE", "FIL", "RENDER",
]

# ✅ Donchian هم روی همین ارزها کار می‌کند (+ BTC/ETH/SOL از نسخه قبلی)
DONCHIAN_SYMBOLS = sorted(set(SYMBOLS + ["BTC", "ETH", "SOL"]))

# ============================================================
# PARAMS
# ============================================================
PIVOT_QQE_PARAMS = {
    "pivot_period": 8,
    "qqe_rsi": 10, "qqe_sf": 5,
    "macd_fast": 6, "macd_slow": 19, "macd_signal": 2,
    "sl_atr_mult": 2.0,
    "tp_rr": 2.0,
    "min_bars": 60,
    "vol_period": 30,
}

DONCHIAN_PARAMS = {
    "entry_n": 8,
    "exit_n": 2,
    "vol_mult": 1.5,
    "atr_mult": 3.0,
    "ema_period": 150,
    "min_bars": 200,
    "tp_rr": 5.0,  # safety net only; primary exit = Donchian signal
}

PIVOT_QQE_TIMEFRAMES = [
    ("5m",  ["5", "5m", "M5"],                   1, 365 * 24 * 12),
    ("15m", ["15", "15m", "M15"],                2, 365 * 24 * 4),
    ("30m", ["30", "30m", "M30"],                3, 365 * 24 * 2),
    ("1h",  ["60", "60m", "H1"],                 4, 365 * 24),
    ("4h",  ["240", "4h", "H4"],                 5, 365 * 6),
]

LEVERAGE = 10
MAX_VOLTARGET_POSITIONS = 5
MAX_DONCHIAN_POSITIONS = 5
MAX_TOTAL_POSITIONS = MAX_VOLTARGET_POSITIONS + MAX_DONCHIAN_POSITIONS
MAX_NOTIONAL_PCT = 0.75
LOG_FILE = Path("marginpad_trades_log.json")

MIN_SL_PCT = 0.5
MIN_TP_PCT = 0.3
MIN_RR = 0.5


# ============================================================
# HELPERS
# ============================================================
def fmt_price(p: float) -> str:
    if p >= 1000:   return f"{p:.2f}"
    if p >= 1:      return f"{p:.4f}"
    if p >= 0.01:   return f"{p:.6f}"
    if p >= 0.0001: return f"{p:.8f}"
    return f"{p:.10f}".rstrip('0')


def round_price(price: float, ref_price: float) -> float:
    s = f"{ref_price:.10f}".rstrip("0")
    dec = min(len(s.split(".")[1]), 8) if "." in s else 0
    return round(price, dec)


def is_signal_ok(sig) -> tuple:
    if sig["sl_pct"] < MIN_SL_PCT:
        return False, f"SL too tight ({sig['sl_pct']:.2f}%)"
    if sig["tp_pct"] < MIN_TP_PCT:
        return False, f"TP too tight ({sig['tp_pct']:.2f}%)"
    rr = sig["tp_pct"] / sig["sl_pct"]
    if rr < MIN_RR:
        return False, f"R/R too low ({rr:.2f})"
    return True, ""


def compute_dynamic_size(vol30: float, profile: dict) -> float:
    if np.isnan(vol30) or vol30 <= 0:
        return profile["target_vol"]
    clamped = max(VOL_FLOOR, min(VOL_CEIL, vol30))
    dyn = profile["target_vol"] / clamped
    return max(profile["size_min"], min(profile["size_max"], dyn))


# ============================================================
# TELEGRAM
# ============================================================
def send_telegram(text: str) -> bool:
    if not TG_TOKEN or not TG_CHAT:
        return False
    try:
        for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
            r = requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT, "text": chunk, "parse_mode": "HTML",
                      "disable_web_page_preview": True},
                timeout=15,
            )
            if r.status_code != 200:
                return False
        return True
    except Exception as e:
        print(f"  [telegram] error: {e}")
        return False


def format_new_trade(sym, sig, notional, margin, balance, profile_key, profile):
    side = sig["side"]
    emoji = "🟢" if side == "LONG" else "🔴"
    rr = sig["tp_pct"] / sig["sl_pct"] if sig["sl_pct"] > 0 else 0
    tf = sig.get("_tf_label", "1h")
    vol30 = sig.get("_vol30", 0) * 100
    dyn = compute_dynamic_size(sig.get("_vol30", 0), profile)

    sizes_block = "\n📐 <b>سایز پیشنهادی ۳ پروفایل:</b>\n"
    for k, p in PROFILES.items():
        d = compute_dynamic_size(sig.get("_vol30", 0), p)
        marker = " ← اجرا شد" if k == profile_key else ""
        sizes_block += f"  • {p['name']}: <b>{d*100:.1f}%</b>{marker}\n"

    return (
        f"{emoji} <b>VolTarget: {sym} {side}</b>\n"
        f"🧠 استراتژی: <b>MACD(6,19,2)+Pivot+QQE</b>\n"
        f"📊 تایم‌فریم: <b>{tf}</b>\n\n"
        f"📍 ورود: <code>{fmt_price(sig['entry'])}</code>\n"
        f"🛑 SL: <code>{fmt_price(sig['sl'])}</code> ({sig['sl_pct']:.2f}%)\n"
        f"🎯 TP: <code>{fmt_price(sig['tp'])}</code> ({sig['tp_pct']:.2f}%)\n"
        f"📊 R/R: <b>{rr:.2f}</b> | Vol30: <b>{vol30:.1f}%</b>\n"
        f"💰 اجرا با <b>{profile['name']}</b>: Notional ${notional:,.2f} "
        f"| مارجین ${margin:.2f} ({LEVERAGE}×)\n"
        f"🏦 موجودی: ${balance:,.2f}\n"
        f"{sizes_block}"
    )


def format_donchian_trade(sym, sig, notional, margin, balance, profile_key, profile):
    side = sig["side"]
    emoji = "🟢" if side == "LONG" else "🔴"
    rr = sig["tp_pct"] / sig["sl_pct"] if sig["sl_pct"] > 0 else 0
    tf = sig.get("_tf_label", "1h")
    lev = profile["leverage"]

    sizes_block = "\n📐 <b>سایز پیشنهادی ۳ پروفایل:</b>\n"
    for k, p in DONCHIAN_PROFILES.items():
        usd = balance * p["sf"]
        marker = " ← اجرا شد" if k == profile_key else ""
        sizes_block += (f"  • {p['name']}: <b>{p['sf']*100:.0f}%</b> "
                        f"(${usd:,.0f}, {p['leverage']}×){marker}\n")

    exit_desc = ("بسته شدن بالای Donchian(2) high"
                 if side == "SHORT" else "بسته شدن زیر Donchian(2) low")

    return (
        f"{emoji} <b>Donchian: {sym} {side}</b>\n"
        f"🧠 استراتژی: <b>Donchian(8/2)+Vol+EMA150</b>\n"
        f"📊 تایم‌فریم: <b>{tf}</b>\n\n"
        f"📍 ورود: <code>{fmt_price(sig['entry'])}</code>\n"
        f"🛑 SL: <code>{fmt_price(sig['sl'])}</code> ({sig['sl_pct']:.2f}%) — 3×ATR\n"
        f"🎯 TP: <code>{fmt_price(sig['tp'])}</code> ({sig['tp_pct']:.2f}%) — 5R\n"
        f"📊 R/R: <b>{rr:.2f}</b>\n"
        f"💰 اجرا با <b>{profile['name']}</b>: Notional ${notional:,.2f} "
        f"| مارجین ${margin:.2f} ({lev}×)\n"
        f"🏦 موجودی: ${balance:,.2f}\n"
        f"{sizes_block}"
        f"ℹ️ خروج اصلی: {exit_desc}\n"
    )


def format_closed_trade(trade):
    emoji = "✅" if trade["pnl_pct"] > 0 else "❌"
    strategy = trade.get("strategy", "VolTarget")
    reason = trade.get("close_reason", "")
    reason_line = f"\n📋 دلیل: <b>{reason}</b>" if reason else ""
    return (
        f"{emoji} <b>{strategy} بسته شد: {trade['symbol']} {trade['side']}</b>\n"
        f"📊 TF: {trade.get('tf', '1h')}{reason_line}\n\n"
        f"📍 ورود: <code>{fmt_price(trade['entry'])}</code>\n"
        f"🏁 خروج: <code>{fmt_price(trade['exit'])}</code>\n"
        f"📊 سود/ضرر: <b>{trade['pnl_pct']:+.2f}%</b>\n"
    )


def format_strategy_summary(log_data, strategy, title):
    closed = [t for t in log_data.get("closed_trades", []) if t.get("strategy") == strategy]
    if not closed:
        return f"<i>{title}: هنوز معامله بسته‌شده‌ای ثبت نشده است.</i>"
    df = pd.DataFrame(closed)
    total = len(df)
    wins = len(df[df["pnl_pct"] > 0])
    losses = total - wins
    wr = (wins / total) * 100
    total_pnl = df["pnl_pct"].sum()
    avg_pnl = total_pnl / total
    long_df = df[df["side"] == "LONG"]
    short_df = df[df["side"] == "SHORT"]

    report = (
        f"📈 <b>{title}:</b>\n"
        f"• کل: {total} (✅{wins} / ❌{losses})\n"
        f"• وین‌ریت: {wr:.1f}%\n"
        f"• سود/زیان کل: <b>{total_pnl:+.2f}%</b>\n"
        f"• میانگین: {avg_pnl:+.2f}%\n"
    )
    if not long_df.empty:
        l_wr = len(long_df[long_df["pnl_pct"] > 0]) / len(long_df) * 100
        report += f"🟢 LONG: {len(long_df)} — وین‌ریت {l_wr:.0f}%\n"
    if not short_df.empty:
        s_wr = len(short_df[short_df["pnl_pct"] > 0]) / len(short_df) * 100
        report += f"🔴 SHORT: {len(short_df)} — وین‌ریت {s_wr:.0f}%\n"
    return report


# ============================================================
# API
# ============================================================
def get_klines(symbol, interval):
    r = requests.get(f"{BASE}/klines?symbol={symbol}&interval={interval}", timeout=15)
    r.raise_for_status()
    return r.json()


def get_klines_fallback(symbol, intervals):
    for interval in intervals:
        try:
            k = get_klines(symbol, interval)
            if k and len(k) >= 50:
                return k
        except Exception:
            continue
    return None


def get_positions():
    r = requests.get(f"{BASE}/positions?status=open", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return {p["symbol"]: p for p in r.json().get("positions", [])}


def get_balance():
    r = requests.get(f"{BASE}/balance", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def open_position(symbol, side, margin, leverage, sl, tp, coid):
    body = {"symbol": symbol, "side": side, "margin_usd": round(margin, 2),
            "leverage": leverage, "sl": sl, "tp": tp, "client_order_id": coid}
    r = requests.post(f"{BASE}/open", headers=H_JSON, json=body, timeout=15)
    if r.status_code not in (200, 201):
        try:
            err = r.json()
        except Exception:
            err = r.text[:800]
        print(f"  [{symbol}] HTTP {r.status_code} — error: {err}")
        print(f"  [{symbol}] payload: {body}")
        return None
    return r.json()


def close_position(pos_dict):
    if not pos_dict:
        return None
    pos_id = pos_dict.get("id") or pos_dict.get("position_id") or pos_dict.get("order_id")
    symbol = pos_dict.get("symbol")
    if not pos_id or not symbol:
        print(f"  [close {symbol}] missing id/symbol")
        return None
    for key in ("id", "position_id", "order_id"):
        try:
            r = requests.post(f"{BASE}/close", headers=H_JSON,
                             json={key: pos_id, "symbol": symbol}, timeout=15)
            if r.status_code in (200, 201, 204):
                print(f"  [close {symbol}] OK via '{key}'")
                return r.json() if r.text else {"ok": True}
        except Exception as e:
            print(f"  [close {symbol}] '{key}' error: {e}")
    print(f"  [close {symbol}] all attempts failed")
    return None


# ============================================================
# LOG
# ============================================================
def load_log():
    if LOG_FILE.exists():
        try:
            return json.loads(LOG_FILE.read_text())
        except Exception:
            pass
    return {"open_entries": {}, "closed_trades": []}


def save_log(log):
    LOG_FILE.write_text(json.dumps(log, indent=2, default=str))


# ============================================================
# INDICATORS
# ============================================================
def compute_atr(df, length=14):
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    return pd.Series(tr).ewm(alpha=1/length, adjust=False).mean().values


def klines_to_df(klines):
    df = pd.DataFrame(klines)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df.set_index("time").sort_index()


def compute_vol30(df, bars_per_year):
    ret = df["close"].pct_change()
    return ret.rolling(PIVOT_QQE_PARAMS["vol_period"]).std() * np.sqrt(bars_per_year)


# ============================================================
# STRATEGY A: VolTarget (MACD + Pivot + QQE) LONG/SHORT
# ============================================================
def detect_pivot_qqe_signals(df, tf_label, bars_per_year):
    P = PIVOT_QQE_PARAMS
    n = len(df)
    if n < P["min_bars"]:
        return []

    close, high, low = df["close"], df["high"], df["low"]
    times = df.index

    def _ema(s, nn): return s.ewm(span=nn, adjust=False).mean()

    def _rsi(c, nn):
        d = c.diff()
        g = d.clip(lower=0)
        l = -d.clip(upper=0)
        ag = g.ewm(alpha=1/nn, adjust=False).mean()
        al = l.ewm(alpha=1/nn, adjust=False).mean()
        return 100 - 100 / (1 + ag / al.replace(0, np.nan))

    ml = _ema(close, P["macd_fast"]) - _ema(close, P["macd_slow"])
    sig_line = _ema(ml, P["macd_signal"])
    ph = high.rolling(P["pivot_period"]).max()
    pl = low.rolling(P["pivot_period"]).min()
    pivot_mid = (ph + pl) / 2
    rsi = _rsi(close, P["qqe_rsi"])
    rsi_ma = _ema(rsi, P["qqe_sf"])
    atr = compute_atr(df)
    vol30 = compute_vol30(df, bars_per_year)

    crosses = []
    for offset in (2, 3):
        i = n - offset
        if i < 1:
            continue
        cu = (ml.iloc[i-1] <= sig_line.iloc[i-1]) and (ml.iloc[i] > sig_line.iloc[i])
        cd = (ml.iloc[i-1] >= sig_line.iloc[i-1]) and (ml.iloc[i] < sig_line.iloc[i])
        try:
            bar_ts = int(pd.Timestamp(times[i]).timestamp())
        except Exception:
            bar_ts = None
        if cu:   crosses.append(("UP", i, bar_ts))
        if cd:   crosses.append(("DOWN", i, bar_ts))

    if not crosses:
        return []

    crosses.sort(key=lambda x: x[1], reverse=True)
    direction, i, bar_ts = crosses[0]

    atr_val = float(atr[i])
    if atr_val <= 0:
        return []

    vol_val = float(vol30.iloc[i]) if not pd.isna(vol30.iloc[i]) else 0.30
    entry = float(close.iloc[i])
    signals = []

    if direction == "UP":
        signals.append({"action": "EXIT_SHORT", "side": "SHORT",
                        "entry": entry, "_tf_label": tf_label, "_bar_ts": bar_ts})
        if close.iloc[i] > pivot_mid.iloc[i] and rsi_ma.iloc[i] > 50:
            sl_price = entry - P["sl_atr_mult"] * atr_val
            sl_dist = entry - sl_price
            if sl_dist > 0:
                tp_price = entry + P["tp_rr"] * sl_dist
                sl_pct = sl_dist / entry * 100
                tp_pct = (tp_price - entry) / entry * 100
                if 0 < sl_pct < 15:
                    signals.append({
                        "action": "ENTRY_LONG", "side": "LONG",
                        "entry": entry, "sl": sl_price, "tp": tp_price,
                        "sl_pct": sl_pct, "tp_pct": tp_pct,
                        "_tf_label": tf_label, "_bar_ts": bar_ts,
                        "_vol30": vol_val,
                    })
    else:
        signals.append({"action": "EXIT_LONG", "side": "LONG",
                        "entry": entry, "_tf_label": tf_label, "_bar_ts": bar_ts})
        if close.iloc[i] < pivot_mid.iloc[i] and rsi_ma.iloc[i] < 50:
            sl_price = entry + P["sl_atr_mult"] * atr_val
            sl_dist = sl_price - entry
            if sl_dist > 0:
                tp_price = entry - P["tp_rr"] * sl_dist
                sl_pct = sl_dist / entry * 100
                tp_pct = (entry - tp_price) / entry * 100
                if 0 < sl_pct < 15:
                    signals.append({
                        "action": "ENTRY_SHORT", "side": "SHORT",
                        "entry": entry, "sl": sl_price, "tp": tp_price,
                        "sl_pct": sl_pct, "tp_pct": tp_pct,
                        "_tf_label": tf_label, "_bar_ts": bar_ts,
                        "_vol30": vol_val,
                    })
    return signals


# ============================================================
# STRATEGY B: Donchian Breakout — LONG + SHORT (mirrored)
# ============================================================
def detect_donchian_signals(df, tf_label):
    """
    LONG entry:  Close > Donchian_high(8)  AND Vol > 1.5×MA20 AND Close > EMA(150)
    SHORT entry: Close < Donchian_low(8)   AND Vol > 1.5×MA20 AND Close < EMA(150)
    LONG exit:   Close < Donchian_low(2)
    SHORT exit:  Close > Donchian_high(2)
    SL = 3×ATR | TP = 5R (safety net; primary exit = Donchian signal)
    """
    P = DONCHIAN_PARAMS
    n = len(df)
    if n < P["min_bars"]:
        return []
    if "volume" not in df.columns:
        return []

    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]

    tr = pd.concat([high - low,
                    (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()

    don_hi_entry = high.shift(1).rolling(P["entry_n"]).max()
    don_lo_entry = low.shift(1).rolling(P["entry_n"]).min()
    don_hi_exit  = high.shift(1).rolling(P["exit_n"]).max()
    don_lo_exit  = low.shift(1).rolling(P["exit_n"]).min()
    ema = close.ewm(span=P["ema_period"], adjust=False).mean()
    vol_ma = volume.rolling(20).mean()

    for offset in (2, 3):
        i = n - offset
        if i < P["min_bars"]:
            continue
        if pd.isna(don_hi_entry.iloc[i]) or pd.isna(don_lo_entry.iloc[i]):
            continue
        if pd.isna(atr.iloc[i]) or pd.isna(ema.iloc[i]) or pd.isna(vol_ma.iloc[i]):
            continue

        atr_val = float(atr.iloc[i])
        if atr_val <= 0:
            continue

        close_v = float(close.iloc[i])
        ema_v = float(ema.iloc[i])
        vol_ok = float(volume.iloc[i]) > P["vol_mult"] * float(vol_ma.iloc[i])

        try:
            bar_ts = int(pd.Timestamp(df.index[i]).timestamp())
        except Exception:
            bar_ts = None

        bar_signals = []

        # --- EXIT signals ---
        if close_v < float(don_lo_exit.iloc[i]):
            bar_signals.append({"action": "EXIT_LONG", "side": "LONG",
                                "entry": close_v, "_tf_label": tf_label,
                                "_bar_ts": bar_ts})
        if close_v > float(don_hi_exit.iloc[i]):
            bar_signals.append({"action": "EXIT_SHORT", "side": "SHORT",
                                "entry": close_v, "_tf_label": tf_label,
                                "_bar_ts": bar_ts})

        # --- LONG entry ---
        if close_v > float(don_hi_entry.iloc[i]) and vol_ok and close_v > ema_v:
            sl_price = close_v - P["atr_mult"] * atr_val
            sl_dist = close_v - sl_price
            if sl_dist > 0:
                tp_price = close_v + P["tp_rr"] * sl_dist
                sl_pct = sl_dist / close_v * 100
                tp_pct = (tp_price - close_v) / close_v * 100
                if 0 < sl_pct < 25:
                    bar_signals.append({
                        "action": "ENTRY_LONG", "side": "LONG",
                        "entry": close_v, "sl": sl_price, "tp": tp_price,
                        "sl_pct": sl_pct, "tp_pct": tp_pct,
                        "_tf_label": tf_label, "_bar_ts": bar_ts,
                        "_atr": atr_val,
                    })

        # --- SHORT entry (mirrored) ---
        if close_v < float(don_lo_entry.iloc[i]) and vol_ok and close_v < ema_v:
            sl_price = close_v + P["atr_mult"] * atr_val
            sl_dist = sl_price - close_v
            if sl_dist > 0:
                tp_price = close_v - P["tp_rr"] * sl_dist
                sl_pct = sl_dist / close_v * 100
                tp_pct = (close_v - tp_price) / close_v * 100
                if 0 < sl_pct < 25:
                    bar_signals.append({
                        "action": "ENTRY_SHORT", "side": "SHORT",
                        "entry": close_v, "sl": sl_price, "tp": tp_price,
                        "sl_pct": sl_pct, "tp_pct": tp_pct,
                        "_tf_label": tf_label, "_bar_ts": bar_ts,
                        "_atr": atr_val,
                    })

        if bar_signals:
            return bar_signals

    return []


# ============================================================
# FETCH TIMEFRAMES
# ============================================================
def fetch_timeframes(sym, min_bars_needed, bars=500):
    data = {}
    for tf_label, intervals, _, _ in PIVOT_QQE_TIMEFRAMES:
        try:
            k_raw = get_klines_fallback(sym, intervals)
            if k_raw and len(k_raw) >= min_bars_needed:
                data[tf_label] = klines_to_df(k_raw[-bars:])
            else:
                data[tf_label] = None
        except Exception:
            data[tf_label] = None
    return data


# ============================================================
# MAIN SCAN
# ============================================================
def run_scan() -> int:
    now = datetime.now(timezone.utc)
    print("=" * 70)
    print(f"Multi-Strategy Scan — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"VolTarget  → {ACTIVE_PROFILE['name']} (target_vol={ACTIVE_PROFILE['target_vol']*100:.0f}%)")
    print(f"Donchian   → {ACTIVE_DONCHIAN_PROFILE['name']} ({ACTIVE_DONCHIAN_PROFILE['sf']*100:.0f}%, "
          f"{ACTIVE_DONCHIAN_PROFILE['leverage']}×) — LONG+SHORT")
    print(f"Symbols:   VolTarget={len(SYMBOLS)} | Donchian={len(DONCHIAN_SYMBOLS)}")
    print("=" * 70)

    log = load_log()

    try:
        bal = get_balance()
        balance_usd = float(bal.get("balance_usd", 10000))
        print(f"Balance: ${balance_usd:,.2f}")
    except Exception as e:
        print(f"FATAL balance: {e}"); return 1

    try:
        open_positions = get_positions()
        print(f"Open: {len(open_positions)} — {list(open_positions.keys())}")
    except Exception as e:
        print(f"FATAL positions: {e}"); return 1

    newly_closed = []

    # ==========================================================
    # PHASE 1: VolTarget exits (MACD opposite cross)
    # ==========================================================
    for log_key in list(log["open_entries"].keys()):
        entry_info = log["open_entries"].get(log_key)
        if not entry_info or entry_info.get("strategy") != "VolTarget":
            continue
        sym = log_key.split(":", 1)[-1]
        if sym not in open_positions:
            continue

        pos_side = entry_info.get("side", "LONG")
        entry_tf = entry_info.get("tf", "1h")
        try:
            tf_intervals, tf_bpy = ["60", "60m", "H1"], 365 * 24
            for tf_label, intervals, _, bpy in PIVOT_QQE_TIMEFRAMES:
                if tf_label == entry_tf:
                    tf_intervals, tf_bpy = intervals, bpy
                    break
            k_raw = get_klines_fallback(sym, tf_intervals)
            if not k_raw or len(k_raw) < 60:
                continue
            df_tf = klines_to_df(k_raw[-300:])
            signals = detect_pivot_qqe_signals(df_tf, entry_tf, tf_bpy)
            exit_action = f"EXIT_{pos_side}"
            if not any(s["action"] == exit_action for s in signals):
                continue

            pos = open_positions[sym]
            if not close_position(pos):
                continue

            k1h = get_klines(sym, "60")
            current_price = float(k1h[-1]["close"])
            entry_price = entry_info["entry"]
            pnl = ((current_price - entry_price) if pos_side == "LONG"
                   else (entry_price - current_price)) / entry_price * 100

            log["open_entries"].pop(log_key)
            rec = {"symbol": sym, "strategy": "VolTarget", "side": pos_side,
                   "entry": entry_price, "exit": current_price,
                   "pnl_pct": round(pnl, 2), "tf": entry_tf,
                   "close_reason": f"MACD-EXIT ({entry_tf})"}
            log["closed_trades"].append(rec)
            newly_closed.append(rec)
            open_positions.pop(sym, None)
            print(f"  [CLOSED-SIGNAL] VolTarget {sym} {pos_side} → {pnl:+.2f}%")
        except Exception as e:
            print(f"  [VolTarget {sym}] exit-check error: {e}")

    # ==========================================================
    # PHASE 1b: Donchian exits (LONG/SHORT-aware)
    # ==========================================================
    for log_key in list(log["open_entries"].keys()):
        entry_info = log["open_entries"].get(log_key)
        if not entry_info or entry_info.get("strategy") != "Donchian":
            continue
        sym = log_key.split(":", 1)[-1]
        if sym not in open_positions:
            continue

        pos_side = entry_info.get("side", "LONG")
        entry_tf = entry_info.get("tf", "1h")
        try:
            tf_intervals = ["60", "60m", "H1"]
            for tf_label, intervals, _, _ in PIVOT_QQE_TIMEFRAMES:
                if tf_label == entry_tf:
                    tf_intervals = intervals
                    break
            k_raw = get_klines_fallback(sym, tf_intervals)
            if not k_raw or len(k_raw) < DONCHIAN_PARAMS["min_bars"]:
                continue
            df_tf = klines_to_df(k_raw[-500:])
            signals = detect_donchian_signals(df_tf, entry_tf)
            exit_action = f"EXIT_{pos_side}"
            if not any(s["action"] == exit_action for s in signals):
                continue

            pos = open_positions[sym]
            if not close_position(pos):
                continue

            k1h = get_klines(sym, "60")
            current_price = float(k1h[-1]["close"])
            entry_price = entry_info["entry"]
            pnl = ((current_price - entry_price) if pos_side == "LONG"
                   else (entry_price - current_price)) / entry_price * 100

            log["open_entries"].pop(log_key)
            rec = {"symbol": sym, "strategy": "Donchian", "side": pos_side,
                   "entry": entry_price, "exit": current_price,
                   "pnl_pct": round(pnl, 2), "tf": entry_tf,
                   "close_reason": f"Donchian-EXIT ({entry_tf})"}
            log["closed_trades"].append(rec)
            newly_closed.append(rec)
            open_positions.pop(sym, None)
            print(f"  [CLOSED-SIGNAL] Donchian {sym} {pos_side} → {pnl:+.2f}%")
        except Exception as e:
            print(f"  [Donchian {sym}] exit-check error: {e}")

    # ==========================================================
    # PHASE 2: SL/TP auto-close (both strategies)
    # ==========================================================
    for log_key in list(log["open_entries"].keys()):
        sym = log_key.split(":", 1)[-1]
        if sym in open_positions:
            continue
        entry_info = log["open_entries"].pop(log_key)
        try:
            k = get_klines(sym, "60")
            current_price = float(k[-1]["close"])
            entry_price = entry_info["entry"]
            pos_side = entry_info.get("side", "LONG")
            pnl = ((current_price - entry_price) if pos_side == "LONG"
                   else (entry_price - current_price)) / entry_price * 100
            rec = {"symbol": sym, "strategy": entry_info.get("strategy", "VolTarget"),
                   "side": pos_side, "entry": entry_price, "exit": current_price,
                   "pnl_pct": round(pnl, 2), "tf": entry_info.get("tf", "1h"),
                   "close_reason": "SL/TP"}
            log["closed_trades"].append(rec)
            newly_closed.append(rec)
            print(f"  [CLOSED-AUTO] {entry_info.get('strategy')} {sym} {pos_side} → {pnl:+.2f}%")
        except Exception as e:
            print(f"  [{sym}] close calc error: {e}")

    # ==========================================================
    # PHASE 3a: VolTarget new entries
    # ==========================================================
    new_trades = []

    def count_open_by_strategy(strategy):
        return sum(1 for k, v in log["open_entries"].items()
                   if v.get("strategy") == strategy and k.split(":", 1)[-1] in open_positions)

    for sym in SYMBOLS:
        if sym in open_positions:
            continue
        if count_open_by_strategy("VolTarget") + len([t for t in new_trades if t[0] == "VolTarget"]) >= MAX_VOLTARGET_POSITIONS:
            print(f"  Max VolTarget positions ({MAX_VOLTARGET_POSITIONS}) reached")
            break
        if len(open_positions) + len(new_trades) >= MAX_TOTAL_POSITIONS:
            print(f"  Max total positions ({MAX_TOTAL_POSITIONS}) reached")
            break

        try:
            k1h = get_klines(sym, "60")
            if not k1h or len(k1h) < 5:
                continue
            current_price = float(k1h[-1]["close"])
        except Exception:
            continue

        tf_data = fetch_timeframes(sym, PIVOT_QQE_PARAMS["min_bars"] + 50, bars=300)
        all_entries = []
        for tf_label, _, tf_prio, bpy in PIVOT_QQE_TIMEFRAMES:
            df_tf = tf_data.get(tf_label)
            if df_tf is None:
                continue
            try:
                for s in detect_pivot_qqe_signals(df_tf, tf_label, bpy):
                    if s["action"].startswith("ENTRY_"):
                        s["_tf_prio"] = tf_prio
                        all_entries.append(s)
            except Exception:
                continue

        if not all_entries:
            continue

        sides = set(s["side"] for s in all_entries)
        if len(sides) > 1:
            print(f"  [VT {sym}] SKIP: conflicting directions {list(sides)}")
            continue

        all_entries.sort(key=lambda x: x["_tf_prio"], reverse=True)
        sig = all_entries[0]

        ok, reason = is_signal_ok(sig)
        if not ok:
            print(f"  [VT {sym}] SKIP ({sig['side']} {sig['_tf_label']}): {reason}")
            continue

        if sig["side"] == "LONG":
            if not (sig["sl"] < current_price < sig["tp"]):
                print(f"  [VT {sym}] SKIP: price out of SL/TP (LONG)")
                continue
        else:
            if not (sig["tp"] < current_price < sig["sl"]):
                print(f"  [VT {sym}] SKIP: price out of SL/TP (SHORT)")
                continue

        min_dist = current_price * 0.003
        if abs(current_price - sig["sl"]) < min_dist or abs(current_price - sig["tp"]) < min_dist:
            print(f"  [VT {sym}] SKIP: SL/TP too close")
            continue

        dyn_size = compute_dynamic_size(sig.get("_vol30", 0), ACTIVE_PROFILE)
        notional = min(balance_usd * dyn_size, balance_usd * MAX_NOTIONAL_PCT)
        margin = max(notional / LEVERAGE, 1.0)
        if margin < 1.0:
            continue

        coid = f"VolTarget-{sig['side']}-{sym}-{int(now.timestamp())}"
        sl_r = round_price(sig["sl"], sig["entry"])
        tp_r = round_price(sig["tp"], sig["entry"])
        side_lc = "long" if sig["side"] == "LONG" else "short"

        try:
            resp = open_position(sym, side_lc, margin, LEVERAGE, sl_r, tp_r, coid)
        except Exception as e:
            print(f"  [VT {sym}] network error: {e}")
            continue

        if resp is None or resp.get("idempotent"):
            continue

        log_key = f"VolTarget:{sym}"
        log["open_entries"][log_key] = {
            "strategy": "VolTarget", "side": sig["side"], "entry": sig["entry"],
            "sl": sig["sl"], "tp": sig["tp"], "margin": margin,
            "tf": sig["_tf_label"], "_bar_ts": sig.get("_bar_ts"),
            "profile": ACTIVE_PROFILE_KEY,
        }
        new_trades.append(("VolTarget", sym, sig, notional, margin))

        emoji = "🟢" if sig["side"] == "LONG" else "🔴"
        rr = sig["tp_pct"] / sig["sl_pct"] if sig["sl_pct"] > 0 else 0
        print(f"  {emoji} [VolTarget {sig['_tf_label']}] {sig['side']} {sym} "
              f"@ {fmt_price(sig['entry'])} | R/R {rr:.2f} | "
              f"Size {dyn_size*100:.1f}% | Margin ${margin:.2f}")

    # ==========================================================
    # PHASE 3b: Donchian new entries (LONG + SHORT)
    # ==========================================================
    for sym in DONCHIAN_SYMBOLS:
        if sym in open_positions:
            continue
        if count_open_by_strategy("Donchian") + len([t for t in new_trades if t[0] == "Donchian"]) >= MAX_DONCHIAN_POSITIONS:
            print(f"  Max Donchian positions ({MAX_DONCHIAN_POSITIONS}) reached")
            break
        if len(open_positions) + len(new_trades) >= MAX_TOTAL_POSITIONS:
            print(f"  Max total positions ({MAX_TOTAL_POSITIONS}) reached")
            break

        try:
            k1h = get_klines(sym, "60")
            if not k1h or len(k1h) < 5:
                continue
            current_price = float(k1h[-1]["close"])
        except Exception:
            continue

        tf_data = fetch_timeframes(sym, DONCHIAN_PARAMS["min_bars"] + 50, bars=500)
        all_entries = []
        for tf_label, _, tf_prio, _ in PIVOT_QQE_TIMEFRAMES:
            df_tf = tf_data.get(tf_label)
            if df_tf is None:
                continue
            try:
                for s in detect_donchian_signals(df_tf, tf_label):
                    if s["action"] in ("ENTRY_LONG", "ENTRY_SHORT"):
                        s["_tf_prio"] = tf_prio
                        all_entries.append(s)
            except Exception:
                continue

        if not all_entries:
            continue

        sides = set(s["side"] for s in all_entries)
        if len(sides) > 1:
            print(f"  [DN {sym}] SKIP: conflicting directions {list(sides)}")
            continue

        all_entries.sort(key=lambda x: x["_tf_prio"], reverse=True)
        sig = all_entries[0]

        ok, reason = is_signal_ok(sig)
        if not ok:
            print(f"  [DN {sym}] SKIP ({sig['side']} {sig['_tf_label']}): {reason}")
            continue

        if sig["side"] == "LONG":
            if not (sig["sl"] < current_price < sig["tp"]):
                print(f"  [DN {sym}] SKIP: price out of SL/TP (LONG)")
                continue
        else:
            if not (sig["tp"] < current_price < sig["sl"]):
                print(f"  [DN {sym}] SKIP: price out of SL/TP (SHORT)")
                continue

        min_dist = current_price * 0.003
        if abs(current_price - sig["sl"]) < min_dist or abs(current_price - sig["tp"]) < min_dist:
            print(f"  [DN {sym}] SKIP: SL/TP too close")
            continue

        sf = ACTIVE_DONCHIAN_PROFILE["sf"]
        lev = ACTIVE_DONCHIAN_PROFILE["leverage"]
        notional = min(balance_usd * sf, balance_usd * MAX_NOTIONAL_PCT)
        margin = max(notional / lev, 1.0)
        if margin < 1.0:
            continue

        coid = f"Donchian-{sig['side']}-{sym}-{int(now.timestamp())}"
        sl_r = round_price(sig["sl"], sig["entry"])
        tp_r = round_price(sig["tp"], sig["entry"])
        side_lc = "long" if sig["side"] == "LONG" else "short"

        try:
            resp = open_position(sym, side_lc, margin, lev, sl_r, tp_r, coid)
        except Exception as e:
            print(f"  [DN {sym}] network error: {e}")
            continue

        if resp is None or resp.get("idempotent"):
            continue

        log_key = f"Donchian:{sym}"
        log["open_entries"][log_key] = {
            "strategy": "Donchian", "side": sig["side"], "entry": sig["entry"],
            "sl": sig["sl"], "tp": sig["tp"], "margin": margin,
            "tf": sig["_tf_label"], "_bar_ts": sig.get("_bar_ts"),
            "profile": ACTIVE_DONCHIAN_PROFILE_KEY,
        }
        new_trades.append(("Donchian", sym, sig, notional, margin))

        emoji = "🟢" if sig["side"] == "LONG" else "🔴"
        rr = sig["tp_pct"] / sig["sl_pct"] if sig["sl_pct"] > 0 else 0
        print(f"  {emoji} [Donchian {sig['_tf_label']}] {sig['side']} {sym} "
              f"@ {fmt_price(sig['entry'])} | R/R {rr:.2f} | "
              f"Size {sf*100:.0f}% | Lev {lev}× | Margin ${margin:.2f}")

    save_log(log)

    # ==========================================================
    # Telegram
    # ==========================================================
    msg_blocks = []

    if new_trades:
        vt_new = [t for t in new_trades if t[0] == "VolTarget"]
        dn_new = [t for t in new_trades if t[0] == "Donchian"]

        if vt_new:
            msg_blocks.append(
                f"<b>🔔 VolTarget — {now.strftime('%H:%M')} UTC</b>\n"
                f"<i>اجرا: {ACTIVE_PROFILE['name']}</i>"
            )
            for _, sym, sig, notional, margin in vt_new:
                msg_blocks.append(
                    format_new_trade(sym, sig, notional, margin, balance_usd,
                                     ACTIVE_PROFILE_KEY, ACTIVE_PROFILE))
        if dn_new:
            msg_blocks.append(
                f"<b>🔔 Donchian — {now.strftime('%H:%M')} UTC</b>\n"
                f"<i>اجرا: {ACTIVE_DONCHIAN_PROFILE['name']} (LONG+SHORT)</i>"
            )
            for _, sym, sig, notional, margin in dn_new:
                msg_blocks.append(
                    format_donchian_trade(sym, sig, notional, margin, balance_usd,
                                          ACTIVE_DONCHIAN_PROFILE_KEY,
                                          ACTIVE_DONCHIAN_PROFILE))

    if newly_closed:
        msg_blocks.append(f"<b>🔒 بسته‌شده — {now.strftime('%H:%M')} UTC</b>")
        for t in newly_closed:
            msg_blocks.append(format_closed_trade(t))

    if new_trades or newly_closed:
        msg_blocks.append(format_strategy_summary(log, "VolTarget", "VolTarget Stats"))
        msg_blocks.append(format_strategy_summary(log, "Donchian", "Donchian Stats"))

    if msg_blocks and (new_trades or newly_closed):
        if send_telegram("\n\n".join(msg_blocks)):
            print(f"  [telegram] sent")

    print(f"\nResult: {len(new_trades)} new | {len(newly_closed)} closed")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(run_scan())
