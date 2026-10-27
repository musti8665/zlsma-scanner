#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MarginPad Dual-Strategy Scanner — Donchian + DailyMACD
========================================================
"""

import os
import sys
import json
import csv
import time
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

ENABLE_DONCHIAN = True
ENABLE_DAILY    = True

PROFILES = {
    "conservative": {"name": "🛡️ محافظه‌کار", "target_vol": 0.20,
                     "size_min": 0.10, "size_max": 0.60},
    "balanced":     {"name": "⭐ متعادل",     "target_vol": 0.30,
                     "size_min": 0.10, "size_max": 0.75},
    "aggressive":   {"name": "🚀 تهاجمی",     "target_vol": 0.40,
                     "size_min": 0.10, "size_max": 0.90},
}

DAILY_PROFILE_KEY = os.environ.get("DAILY_PROFILE", "conservative").strip()
if DAILY_PROFILE_KEY not in PROFILES:
    DAILY_PROFILE_KEY = "conservative"
DAILY_PROFILE = PROFILES[DAILY_PROFILE_KEY]

VOL_FLOOR = 0.15
VOL_CEIL  = 0.80

DONCHIAN_PROFILES = {
    "spot_safe": {"name": "🛡 Spot-safe", "sf": 0.25, "leverage": 1.0},
    "lev_2x":    {"name": "⚡ 2x Lev",    "sf": 0.50, "leverage": 1.5},
    "lev_3x":    {"name": "🚀 3x Lev",    "sf": 0.75, "leverage": 2.25},
}
ACTIVE_DONCHIAN_PROFILE_KEY = "spot_safe"
ACTIVE_DONCHIAN_PROFILE = DONCHIAN_PROFILES[ACTIVE_DONCHIAN_PROFILE_KEY]

DAILY_PAPER_MODE = os.environ.get("DAILY_PAPER_MODE", "true").lower() == "true"
DAILY_EXIT_MODE  = os.environ.get("DAILY_EXIT_MODE", "macd").lower()
DAILY_COOLDOWN_HOURS = 30

API_KEY = os.environ.get("MARGINPAD_API_KEY", "mpb_d04c938486b9f5e711f9a736084fa9b5").strip()
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8601458987:AAEvcxx2ClOTfQIqEp9Ok6TwcvKRaMfIeqo").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

if not API_KEY:
    print("FATAL: MARGINPAD_API_KEY missing")
    sys.exit(1)

BASE = "https://marginpad.io/api/bot/v1"
HEADERS = {"X-API-Key": API_KEY}
H_JSON = {**HEADERS, "Content-Type": "application/json"}

# 👇 ۲۲ ارز مد نظر شما 👇
SYMBOLS = [
    "ADA", "AVAX", "LINK", "LTC",
    "ENA", "ATOM", "NEAR", "ARB", "OP", "PEPE", "SHIB", "SUI",
    "APT", "INJ", "TIA", "SEI", "WIF", "LDO", "UNI", "AAVE", "FIL", "RENDER",
]

DONCHIAN_SYMBOLS = SYMBOLS
DAILY_SYMBOLS    = SYMBOLS

# 👇 پارامترهای طلایی Donchian (از بک‌تست) 👇
DONCHIAN_PARAMS = {
    "entry_n": 10, "exit_n": 5,
    "vol_mult": 2.0, "atr_mult": 2.5, "ema_period": 100,
    "min_bars": 200, "tp_rr": 5.0,
}

DAILY_MACD_PARAMS = {
    "pivot_period": 8,
    "qqe_rsi": 20, "qqe_sf": 9,
    "macd_fast": 5, "macd_slow": 13, "macd_signal": 10,
    "sl_atr_mult": 2.5,
    "tp_rr": 2.0,
    "min_bars": 60,
    "vol_period": 30,
    "kijun_p": 20,
}

SCAN_TIMEFRAMES = [
    ("5m",  ["5", "5m", "M5"],                   1, 365 * 24 * 12),
    ("15m", ["15", "15m", "M15"],                2, 365 * 24 * 4),
    ("30m", ["30", "30m", "M30"],                3, 365 * 24 * 2),
    ("1h",  ["60", "60m", "H1"],                 4, 365 * 24),
    ("4h",  ["240", "4h", "H4"],                 5, 365 * 6),
]

# 👇 تغییر اصلی: DailyMACD فقط روی 4h و 1d معامله می‌کند 👇
DAILY_MACD_TIMEFRAMES = [
    ("4h",  ["240", "4h", "H4"],                  5, 365 * 6),
    ("1d",  ["1440", "1d", "D", "D1", "day"],     6, 365),
]

LEVERAGE = 10
MAX_DONCHIAN_POSITIONS = 3
MAX_DAILY_POSITIONS    = 5
MAX_TOTAL_POSITIONS = MAX_DONCHIAN_POSITIONS + MAX_DAILY_POSITIONS

MAX_NOTIONAL_PCT = 0.75
LOG_FILE = Path("marginpad_trades_log.json")
LEDGER_FILE = Path("paper_ledger.csv")

KILL_CONSEC_LOSSES    = 3
KILL_CONSEC_LOSS_SUM  = -15.0
KILL_MAX_DRAWDOWN     = -20.0
KILL_SILENCE_DAYS     = 10

FILTERS_DONCHIAN = {"min_sl": 0.5, "min_tp": 0.3, "min_rr": 0.5}
FILTERS_DAILY    = {"min_sl": 0.5, "min_tp": 1.0, "min_rr": 1.5}


def fmt_price(p):
    if p >= 1000:   return f"{p:.2f}"
    if p >= 1:      return f"{p:.4f}"
    if p >= 0.01:   return f"{p:.6f}"
    if p >= 0.0001: return f"{p:.8f}"
    return f"{p:.10f}".rstrip('0')


def round_price(price, ref_price):
    s = f"{ref_price:.10f}".rstrip("0")
    dec = min(len(s.split(".")[1]), 8) if "." in s else 0
    return round(price, dec)


def is_signal_ok(sig, filters):
    if sig["sl_pct"] < filters["min_sl"]:
        return False, f"SL too tight ({sig['sl_pct']:.2f}%)"
    if sig["tp_pct"] < filters["min_tp"]:
        return False, f"TP too tight ({sig['tp_pct']:.2f}%)"
    rr = sig["tp_pct"] / sig["sl_pct"]
    if rr < filters["min_rr"]:
        return False, f"R/R too low ({rr:.2f})"
    return True, ""


def compute_dynamic_size(vol30, profile):
    if np.isnan(vol30) or vol30 <= 0:
        return profile["target_vol"]
    clamped = max(VOL_FLOOR, min(VOL_CEIL, vol30))
    dyn = profile["target_vol"] / clamped
    return max(profile["size_min"], min(profile["size_max"], dyn))


def hours_since_last_entry(log, log_key, current_bar_ts):
    last = log.get("last_entry_bar", {}).get(log_key)
    if last is None:
        return 999999
    return (current_bar_ts - last) / 3600


def record_entry_bar(log, log_key, bar_ts):
    log.setdefault("last_entry_bar", {})[log_key] = int(bar_ts)


def send_telegram(text):
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
                print(f"  [telegram] HTTP {r.status_code}: {r.text[:200]}")
                return False
        return True
    except Exception as e:
        print(f"  [telegram] error: {e}")
        return False


def _sizes_block_risk(vol30, active_key):
    block = "\n📐 <b>سایز ۳ پروفایل:</b>\n"
    for k, p in PROFILES.items():
        d = compute_dynamic_size(vol30, p)
        marker = " ← اجرا شد" if k == active_key else ""
        block += f"  • {p['name']}: <b>{d*100:.1f}%</b>{marker}\n"
    return block


def _sizes_block_donchian(balance, active_key):
    block = "\n📐 <b>سایز ۳ پروفایل:</b>\n"
    for k, p in DONCHIAN_PROFILES.items():
        usd = balance * p["sf"]
        marker = " ← اجرا شد" if k == active_key else ""
        block += (f"  • {p['name']}: <b>{p['sf']*100:.0f}%</b> "
                  f"(${usd:,.0f}, {p['leverage']}×){marker}\n")
    return block


def format_donchian_trade(sym, sig, notional, margin, balance, profile_key, profile):
    side = sig["side"]
    emoji = "🟢" if side == "LONG" else "🔴"
    rr = sig["tp_pct"] / sig["sl_pct"] if sig["sl_pct"] > 0 else 0
    tf = sig.get("_tf_label", "1h")
    lev = profile["leverage"]
    exit_desc = ("بسته شدن بالای Donchian(5) high"
                 if side == "SHORT" else "بسته شدن زیر Donchian(5) low")
    return (
        f"{emoji} <b>Donchian: {sym} {side}</b>\n"
        f"🧠 <b>Donchian(10/5)+Vol+EMA100</b> | 📊 <b>{tf}</b>\n\n"
        f"📍 ورود: <code>{fmt_price(sig['entry'])}</code>\n"
        f"🛑 SL: <code>{fmt_price(sig['sl'])}</code> ({sig['sl_pct']:.2f}%) — 2.5×ATR\n"
        f"🎯 TP: <code>{fmt_price(sig['tp'])}</code> ({sig['tp_pct']:.2f}%) — 5R\n"
        f"📊 R/R: <b>{rr:.2f}</b>\n"
        f"💰 اجرا: <b>{profile['name']}</b> | Notional ${notional:,.2f} "
        f"| مارجین ${margin:.2f} ({lev}×)\n"
        f"🏦 موجودی: ${balance:,.2f}\n"
        f"{_sizes_block_donchian(balance, profile_key)}"
        f"ℹ️ خروج اصلی: {exit_desc}\n"
    )


def format_daily_trade(sym, sig, notional, margin, balance, profile_key, profile):
    side = sig["side"]
    emoji = "🟢" if side == "LONG" else "🔴"
    rr = sig["tp_pct"] / sig["sl_pct"] if sig["sl_pct"] > 0 else 0
    tf = sig.get("_tf_label", "1d")
    vol30 = sig.get("_vol30", 0) * 100
    paper = "📝 PAPER | " if DAILY_PAPER_MODE else ""
    return (
        f"{paper}{emoji} <b>DailyMACD: {sym} {side}</b>\n"
        f"🧠 <b>MACD(5,13,10)+QQE(20,9)</b> | 📊 <b>{tf}</b>\n"
        f"🚪 Exit: <b>{DAILY_EXIT_MODE.upper()}</b>\n\n"
        f"📍 ورود: <code>{fmt_price(sig['entry'])}</code>\n"
        f"🛑 SL: <code>{fmt_price(sig['sl'])}</code> ({sig['sl_pct']:.2f}%) — 2.5×ATR\n"
        f"🎯 TP: <code>{fmt_price(sig['tp'])}</code> ({sig['tp_pct']:.2f}%) — 2R\n"
        f"📊 R/R: <b>{rr:.2f}</b> | Vol30: <b>{vol30:.1f}%</b>\n"
        f"💰 اجرا: <b>{profile['name']}</b> | Notional ${notional:,.2f} "
        f"| مارجین ${margin:.2f} ({LEVERAGE}×)\n"
        f"🏦 موجودی: ${balance:,.2f}\n"
        f"{_sizes_block_risk(sig.get('_vol30', 0), profile_key)}"
        f"⏱ Cooldown: {DAILY_COOLDOWN_HOURS}h\n"
    )


def format_closed_trade(trade):
    emoji = "✅" if trade["pnl_pct"] > 0 else "❌"
    strategy = trade.get("strategy", "Unknown")
    reason = trade.get("close_reason", "")
    reason_line = f"\n📋 دلیل: <b>{reason}</b>" if reason else ""
    paper = "📝 " if trade.get("paper") else ""
    return (
        f"{paper}{emoji} <b>{strategy} بسته شد: {trade['symbol']} {trade['side']}</b>\n"
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
    paper_n = int(df["paper"].sum()) if "paper" in df.columns else 0

    report = (
        f"📈 <b>{title}:</b>\n"
        f"• کل: {total} (✅{wins} / ❌{losses})"
        + (f" | 📝paper: {paper_n}" if paper_n else "") + "\n"
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


def check_kill_switch(log):
    closed = log.get("closed_trades", [])
    if not closed:
        return False, ""

    dm = [t for t in closed if t.get("strategy") == "DailyMACD"]
    if len(dm) >= KILL_CONSEC_LOSSES:
        last_n = dm[-KILL_CONSEC_LOSSES:]
        if all(t["pnl_pct"] < 0 for t in last_n):
            total = sum(t["pnl_pct"] for t in last_n)
            if total < KILL_CONSEC_LOSS_SUM:
                return True, (f"🛑 KILL: {KILL_CONSEC_LOSSES} ضرر متوالی DailyMACD "
                              f"({total:+.1f}%)")

    if len(dm) >= 10:
        pnls = [t["pnl_pct"] for t in dm]
        eq = np.cumsum(pnls)
        peak = np.maximum.accumulate(eq)
        dd = float((eq - peak).min())
        if dd < KILL_MAX_DRAWDOWN:
            return True, f"🛑 KILL: DailyMACD drawdown {dd:.1f}%"

    return False, ""


def check_health(log):
    last_entry = log.get("last_entry_bar", {})
    if not last_entry:
        return None
    newest = max(last_entry.values())
    age_days = (time.time() - newest) / 86400
    if age_days > KILL_SILENCE_DAYS:
        return f"⚠️ هیچ سیگنال جدیدی در {age_days:.0f} روز گذشته"
    return None


def log_ledger(date_str, strategy, opened, closed, balance):
    header = not LEDGER_FILE.exists()
    try:
        with open(LEDGER_FILE, "a", newline="") as f:
            w = csv.writer(f)
            if header:
                w.writerow(["date", "strategy", "opened", "closed", "balance"])
            w.writerow([date_str, strategy, opened, closed, round(balance, 2)])
    except Exception as e:
        print(f"  [ledger] write error: {e}")


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
        return None
    return r.json()


def close_position(pos_dict):
    if not pos_dict:
        return None
    pos_id = pos_dict.get("id") or pos_dict.get("position_id") or pos_dict.get("order_id")
    symbol = pos_dict.get("symbol")
    if not pos_id or not symbol:
        return None
    for key in ("id", "position_id", "order_id"):
        try:
            r = requests.post(f"{BASE}/close", headers=H_JSON,
                             json={key: pos_id, "symbol": symbol}, timeout=15)
            if r.status_code in (200, 201, 204):
                return r.json() if r.text else {"ok": True}
        except Exception:
            continue
    return None


def load_log():
    if LOG_FILE.exists():
        try:
            return json.loads(LOG_FILE.read_text())
        except Exception:
            pass
    return {"open_entries": {}, "closed_trades": [], "last_entry_bar": {},
            "kill_switch": None}


def save_log(log):
    LOG_FILE.write_text(json.dumps(log, indent=2, default=str))


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
    return ret.rolling(DAILY_MACD_PARAMS["vol_period"]).std() * np.sqrt(bars_per_year)


def check_kijun_exit(df, side, kijun_p=20):
    if len(df) < kijun_p + 3:
        return False
    kijun = (df["high"].rolling(kijun_p).max() +
             df["low"].rolling(kijun_p).min()) / 2
    last_close = float(df["close"].iloc[-1])
    last_kijun = float(kijun.iloc[-1])
    if np.isnan(last_kijun):
        return False
    if side == "LONG" and last_close < last_kijun:
        return True
    if side == "SHORT" and last_close > last_kijun:
        return True
    return False


def detect_donchian_signals(df, tf_label):
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

    for offset in range(2, 15):
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

        sigs = []
        if close_v < float(don_lo_exit.iloc[i]):
            sigs.append({"action": "EXIT_LONG", "side": "LONG", "entry": close_v,
                         "_tf_label": tf_label, "_bar_ts": bar_ts})
        if close_v > float(don_hi_exit.iloc[i]):
            sigs.append({"action": "EXIT_SHORT", "side": "SHORT", "entry": close_v,
                         "_tf_label": tf_label, "_bar_ts": bar_ts})

        if close_v > float(don_hi_entry.iloc[i]) and vol_ok and close_v > ema_v:
            sl_price = close_v - P["atr_mult"] * atr_val
            sl_dist = close_v - sl_price
            if sl_dist > 0:
                tp_price = close_v + P["tp_rr"] * sl_dist
                sl_pct = sl_dist / close_v * 100
                tp_pct = (tp_price - close_v) / close_v * 100
                if 0 < sl_pct < 25:
                    sigs.append({"action": "ENTRY_LONG", "side": "LONG", "strategy": "Donchian",
                                 "entry": close_v, "sl": sl_price, "tp": tp_price,
                                 "sl_pct": sl_pct, "tp_pct": tp_pct,
                                 "_tf_label": tf_label, "_bar_ts": bar_ts,
                                 "_atr": atr_val})

        if close_v < float(don_lo_entry.iloc[i]) and vol_ok and close_v < ema_v:
            sl_price = close_v + P["atr_mult"] * atr_val
            sl_dist = sl_price - close_v
            if sl_dist > 0:
                tp_price = close_v - P["tp_rr"] * sl_dist
                sl_pct = sl_dist / close_v * 100
                tp_pct = (close_v - tp_price) / close_v * 100
                if 0 < sl_pct < 25:
                    sigs.append({"action": "ENTRY_SHORT", "side": "SHORT", "strategy": "Donchian",
                                 "entry": close_v, "sl": sl_price, "tp": tp_price,
                                 "sl_pct": sl_pct, "tp_pct": tp_pct,
                                 "_tf_label": tf_label, "_bar_ts": bar_ts,
                                 "_atr": atr_val})

        if sigs:
            return sigs
    return []


def detect_daily_macd_signals(df, tf_label, bars_per_year):
    P = DAILY_MACD_PARAMS
    n = len(df)
    if n < P["min_bars"]:
        return []

    close, high, low = df["close"], df["high"], df["low"]
    times = df.index

    def _e(s, nn):
        return s.ewm(span=nn, adjust=False).mean()

    def _r(c, nn):
        d = c.diff()
        g = d.clip(lower=0)
        l = -d.clip(upper=0)
        ag = g.ewm(alpha=1/nn, adjust=False).mean()
        al = l.ewm(alpha=1/nn, adjust=False).mean()
        return 100 - 100/(1 + ag/al.replace(0, np.nan))

    ml = _e(close, P["macd_fast"]) - _e(close, P["macd_slow"])
    sg = _e(ml, P["macd_signal"])
    ph = high.rolling(P["pivot_period"]).max()
    pl = low.rolling(P["pivot_period"]).min()
    pm = (ph + pl) / 2
    rsi = _r(close, P["qqe_rsi"])
    rma = _e(rsi, P["qqe_sf"])
    atr = compute_atr(df)
    vol30 = compute_vol30(df, bars_per_year)

    crosses = []
    for off in (2, 3):
        i = n - off
        if i < 1:
            continue
        cu = ml.iloc[i-1] <= sg.iloc[i-1] and ml.iloc[i] > sg.iloc[i]
        cd = ml.iloc[i-1] >= sg.iloc[i-1] and ml.iloc[i] < sg.iloc[i]
        try:
            ts = int(pd.Timestamp(times[i]).timestamp())
        except Exception:
            ts = None
        if cu:
            crosses.append(("UP", i, ts))
        if cd:
            crosses.append(("DOWN", i, ts))

    if not crosses:
        return []
    crosses.sort(key=lambda x: x[1], reverse=True)
    direction, i, bar_ts = crosses[0]

    av = float(atr[i])
    if av <= 0:
        return []
    vv = float(vol30.iloc[i]) if not pd.isna(vol30.iloc[i]) else 0.30
    entry = float(close.iloc[i])
    out = []

    if direction == "UP":
        out.append({"action": "EXIT_SHORT", "side": "SHORT",
                    "entry": entry, "_tf_label": tf_label, "_bar_ts": bar_ts})
        if close.iloc[i] > pm.iloc[i] and rma.iloc[i] > 50:
            sl = entry - P["sl_atr_mult"] * av
            if sl > 0:
                tp = entry + P["tp_rr"] * (entry - sl)
                slp = (entry - sl) / entry * 100
                tpp = (tp - entry) / entry * 100
                if 0 < slp < 20:
                    out.append({"action": "ENTRY_LONG", "side": "LONG", "strategy": "DailyMACD",
                                "entry": entry, "sl": sl, "tp": tp,
                                "sl_pct": slp, "tp_pct": tpp,
                                "_tf_label": tf_label, "_bar_ts": bar_ts,
                                "_vol30": vv})
    else:
        out.append({"action": "EXIT_LONG", "side": "LONG",
                    "entry": entry, "_tf_label": tf_label, "_bar_ts": bar_ts})
        if close.iloc[i] < pm.iloc[i] and rma.iloc[i] < 50:
            sl = entry + P["sl_atr_mult"] * av
            if sl > 0:
                tp = entry - P["tp_rr"] * (sl - entry)
                slp = (sl - entry) / entry * 100
                tpp = (entry - tp) / entry * 100
                if 0 < slp < 20:
                    out.append({"action": "ENTRY_SHORT", "side": "SHORT", "strategy": "DailyMACD",
                                "entry": entry, "sl": sl, "tp": tp,
                                "sl_pct": slp, "tp_pct": tpp,
                                "_tf_label": tf_label, "_bar_ts": bar_ts,
                                "_vol30": vv})
    return out


def fetch_timeframes(sym, min_bars_needed, bars=500):
    data = {}
    for tf_label, intervals, _, _ in SCAN_TIMEFRAMES:
        try:
            k_raw = get_klines_fallback(sym, intervals)
            if k_raw and len(k_raw) >= min_bars_needed:
                data[tf_label] = klines_to_df(k_raw[-bars:])
            else:
                data[tf_label] = None
        except Exception:
            data[tf_label] = None
    return data


def fetch_daily_macd_tf(sym, tf_label):
    intervals = None; bpy = 365
    for lbl, iv, _, b in DAILY_MACD_TIMEFRAMES:
        if lbl == tf_label: intervals = iv; bpy = b; break
    if intervals is None: intervals = ["1440", "1d", "D", "D1", "day"]
    for interval in intervals:
        try:
            k = get_klines(sym, interval)
            if k and len(k) >= DAILY_MACD_PARAMS["min_bars"]:
                return klines_to_df(k[-300:]), bpy
        except Exception: continue
    return None, bpy


def run_scan():
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    print("=" * 70)
    print(f"Dual-Strategy Scan — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Donchian: {'ON' if ENABLE_DONCHIAN else 'OFF'} "
          f"(spot_safe, max {MAX_DONCHIAN_POSITIONS})")
    print(f"  DailyMACD: {'ON' if ENABLE_DAILY else 'OFF'} "
          f"({'PAPER' if DAILY_PAPER_MODE else 'LIVE'}, max {MAX_DAILY_POSITIONS})")
    print("=" * 70)

    log = load_log()

    kill_on, kill_reason = check_kill_switch(log)
    if kill_on:
        print(f"\n{kill_reason}")
        if log.get("kill_switch") is None:
            log["kill_switch"] = kill_reason
            save_log(log)
            send_telegram(
                f"🛑 <b>KILL SWITCH ACTIVATED</b>\n"
                f"{kill_reason}\n\n"
                f"<i>New entries paused. Manual review required.</i>"
            )
        kill_on = True
    else:
        if log.get("kill_switch"):
            log["kill_switch"] = None
            save_log(log)

    health_warn = check_health(log)
    if health_warn:
        print(f"  {health_warn}")

    try:
        bal = get_balance()
        balance_usd = float(bal.get("balance_usd", 10000))
        print(f"Balance: ${balance_usd:,.2f}")
    except Exception as e:
        print(f"FATAL balance: {e}")
        return 1

    try:
        open_positions = get_positions()
        paper_open_n = sum(1 for v in log["open_entries"].values() if v.get("paper"))
        print(f"Open (exchange): {len(open_positions)} | Paper: {paper_open_n}")
        if open_positions:
            print(f"  {list(open_positions.keys())}")
    except Exception as e:
        print(f"FATAL positions: {e}")
        return 1

    newly_closed = []
    new_trades = []

    def log_key_sym(log_key):
        return log_key.split(":", 1)[-1]

    def count_strategy_total(strategy):
        return sum(1 for v in log["open_entries"].values()
                   if v.get("strategy") == strategy)

    # PHASE 1a: Donchian EXITS
    if ENABLE_DONCHIAN:
        for log_key in list(log["open_entries"].keys()):
            info = log["open_entries"].get(log_key)
            if not info or info.get("strategy") != "Donchian":
                continue
            sym = log_key_sym(log_key)
            if sym not in open_positions:
                continue
            pos_side = info.get("side", "LONG")
            entry_tf = info.get("tf", "1h")
            try:
                tf_intervals = ["60", "60m", "H1"]
                for tf_label, intervals, _, _ in SCAN_TIMEFRAMES:
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
                if not close_position(open_positions[sym]):
                    continue
                k1h = get_klines(sym, "60")
                price = float(k1h[-1]["close"])
                ep = info["entry"]
                pnl = ((price - ep) if pos_side == "LONG" else (ep - price)) / ep * 100
                log["open_entries"].pop(log_key)
                rec = {"symbol": sym, "strategy": "Donchian", "side": pos_side,
                       "entry": ep, "exit": price, "pnl_pct": round(pnl, 2),
                       "tf": entry_tf, "close_reason": f"Donchian-EXIT ({entry_tf})",
                       "paper": False}
                log["closed_trades"].append(rec)
                newly_closed.append(rec)
                open_positions.pop(sym, None)
                print(f"  [CLOSED] Donchian {sym} {pos_side} -> {pnl:+.2f}%")
            except Exception as e:
                print(f"  [DN {sym}] exit error: {e}")

    # PHASE 1b: DailyMACD EXITS
    if ENABLE_DAILY:
        for log_key in list(log["open_entries"].keys()):
            info = log["open_entries"].get(log_key)
            if not info or info.get("strategy") != "DailyMACD":
                continue
            sym = log_key_sym(log_key)
            is_paper = info.get("paper", False)
            if not is_paper and sym not in open_positions:
                continue
            pos_side = info.get("side", "LONG")
            entry_tf = info.get("tf", "1d")
            try:
                df_tf, bpy = fetch_daily_macd_tf(sym, entry_tf)
                if df_tf is None or len(df_tf) < DAILY_MACD_PARAMS["min_bars"]:
                    continue
                trig = False
                reason = ""
                if DAILY_EXIT_MODE == "macd":
                    sigs = detect_daily_macd_signals(df_tf, entry_tf, bpy)
                    exit_action = f"EXIT_{pos_side}"
                    if any(s["action"] == exit_action for s in sigs):
                        trig = True
                        reason = f"MACD-EXIT ({entry_tf})"
                elif DAILY_EXIT_MODE == "kijun":
                    if check_kijun_exit(df_tf, pos_side, DAILY_MACD_PARAMS["kijun_p"]):
                        trig = True
                        reason = f"KIJUN-EXIT ({entry_tf})"
                if not trig:
                    continue
                if not is_paper:
                    if not close_position(open_positions[sym]):
                        continue
                price = float(df_tf["close"].iloc[-1])
                ep = info["entry"]
                pnl = ((price - ep) if pos_side == "LONG" else (ep - price)) / ep * 100
                log["open_entries"].pop(log_key)
                rec = {"symbol": sym, "strategy": "DailyMACD", "side": pos_side,
                       "entry": ep, "exit": price, "pnl_pct": round(pnl, 2),
                       "tf": entry_tf, "close_reason": reason, "paper": is_paper}
                log["closed_trades"].append(rec)
                newly_closed.append(rec)
                if not is_paper and sym in open_positions:
                    open_positions.pop(sym, None)
                tag = "[PAPER] " if is_paper else ""
                print(f"  [CLOSED] {tag}DailyMACD {sym} {pos_side} -> {pnl:+.2f}%")
            except Exception as e:
                print(f"  [DM {sym}] exit error: {e}")

    # PHASE 2: SL/TP auto-close
    for log_key in list(log["open_entries"].keys()):
        info = log["open_entries"].get(log_key)
        if not info:
            continue
        sym = log_key_sym(log_key)
        is_paper = info.get("paper", False)
        strategy = info.get("strategy", "Unknown")
        side = info.get("side", "LONG")
        ep = info.get("entry", 0)
        sl = info.get("sl", 0)
        tp = info.get("tp", 0)
        if ep <= 0:
            continue

        if not is_paper and sym not in open_positions:
            try:
                k = get_klines(sym, "60")
                price = float(k[-1]["close"])
                pnl = ((price - ep) if side == "LONG" else (ep - price)) / ep * 100
                log["open_entries"].pop(log_key)
                rec = {"symbol": sym, "strategy": strategy, "side": side,
                       "entry": ep, "exit": price, "pnl_pct": round(pnl, 2),
                       "tf": info.get("tf", "1h"), "close_reason": "SL/TP",
                       "paper": False}
                log["closed_trades"].append(rec)
                newly_closed.append(rec)
                print(f"  [CLOSED-AUTO] {strategy} {sym} {side} -> {pnl:+.2f}%")
            except Exception as e:
                print(f"  [{sym}] close calc error: {e}")
            continue

        if is_paper and sl > 0 and tp > 0:
            try:
                if strategy == "DailyMACD":
                    entry_tf = info.get("tf", "1d")
                    df_tf, _ = fetch_daily_macd_tf(sym, entry_tf)
                    if df_tf is None:
                        continue
                    price = float(df_tf["close"].iloc[-1])
                else:
                    k = get_klines(sym, "60")
                    if not k:
                        continue
                    price = float(k[-1]["close"])
                hit = False
                reason = ""
                if side == "LONG":
                    if price <= sl:
                        hit = True
                        reason = "SL (paper)"
                    elif price >= tp:
                        hit = True
                        reason = "TP (paper)"
                else:
                    if price >= sl:
                        hit = True
                        reason = "SL (paper)"
                    elif price <= tp:
                        hit = True
                        reason = "TP (paper)"
                if not hit:
                    continue
                pnl = ((price - ep) if side == "LONG" else (ep - price)) / ep * 100
                log["open_entries"].pop(log_key)
                rec = {"symbol": sym, "strategy": strategy, "side": side,
                       "entry": ep, "exit": price, "pnl_pct": round(pnl, 2),
                       "tf": info.get("tf", "1d"), "close_reason": reason,
                       "paper": True}
                log["closed_trades"].append(rec)
                newly_closed.append(rec)
                print(f"  [CLOSED-AUTO-PAPER] {strategy} {sym} {side} -> {pnl:+.2f}% ({reason})")
            except Exception as e:
                print(f"  [{strategy}-paper {sym}] SL/TP check error: {e}")

    # PHASE 3: NEW ENTRIES
    if kill_on:
        print("\n  Entries skipped (kill switch active)")
    else:
        newly_opened_symbols = set()

        # 3a. Donchian
        if ENABLE_DONCHIAN:
            for sym in DONCHIAN_SYMBOLS:
                if sym in open_positions or sym in newly_opened_symbols:
                    continue
                if count_strategy_total("Donchian") + len([t for t in new_trades if t[0] == "Donchian"]) >= MAX_DONCHIAN_POSITIONS:
                    print(f"  Max Donchian ({MAX_DONCHIAN_POSITIONS}) reached")
                    break
                if len(open_positions) + len(new_trades) >= MAX_TOTAL_POSITIONS:
                    print(f"  Max total ({MAX_TOTAL_POSITIONS}) reached")
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
                for tf_label, _, tf_prio, _ in SCAN_TIMEFRAMES:
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
                if len(set(s["side"] for s in all_entries)) > 1:
                    continue

                all_entries.sort(key=lambda x: x["_tf_prio"], reverse=True)
                sig = all_entries[0]
                ok, reason = is_signal_ok(sig, FILTERS_DONCHIAN)
                if not ok:
                    continue
                if sig["side"] == "LONG":
                    if not (sig["sl"] < current_price < sig["tp"]):
                        continue
                else:
                    if not (sig["tp"] < current_price < sig["sl"]):
                        continue
                min_dist = current_price * 0.003
                if abs(current_price - sig["sl"]) < min_dist or abs(current_price - sig["tp"]) < min_dist:
                    continue

                sf = ACTIVE_DONCHIAN_PROFILE["sf"]
                lev = ACTIVE_DONCHIAN_PROFILE["leverage"]
                notional = min(balance_usd * sf, balance_usd * MAX_NOTIONAL_PCT)
                margin = max(notional / lev, 1.0)

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

                log["open_entries"][f"Donchian:{sym}"] = {
                    "strategy": "Donchian", "side": sig["side"], "entry": sig["entry"],
                    "sl": sig["sl"], "tp": sig["tp"], "margin": margin,
                    "tf": sig["_tf_label"], "_bar_ts": sig.get("_bar_ts"),
                    "profile": ACTIVE_DONCHIAN_PROFILE_KEY, "paper": False,
                }
                new_trades.append(("Donchian", sym, sig, notional, margin))
                newly_opened_symbols.add(sym)
                print(f"  [Donchian {sig['_tf_label']}] {sig['side']} {sym} "
                      f"@ {fmt_price(sig['entry'])} | {sf*100:.0f}% spot")

        # 3b. DailyMACD (فقط 4h و 1d)
        if ENABLE_DAILY:
            for sym in DAILY_SYMBOLS:
                if not DAILY_PAPER_MODE and (sym in open_positions or sym in newly_opened_symbols):
                    continue
                if DAILY_PAPER_MODE and f"DailyMACD:{sym}" in log["open_entries"]:
                    continue
                if count_strategy_total("DailyMACD") + len([t for t in new_trades if t[0] == "DailyMACD"]) >= MAX_DAILY_POSITIONS:
                    print(f"  Max DailyMACD ({MAX_DAILY_POSITIONS}) reached")
                    break
                if len(open_positions) + len(new_trades) >= MAX_TOTAL_POSITIONS:
                    print(f"  Max total ({MAX_TOTAL_POSITIONS}) reached")
                    break

                sigs_by_side = {"LONG": [], "SHORT": []}
                for tf_label, iv, tf_prio, bpy in DAILY_MACD_TIMEFRAMES:
                    try:
                        df_tf, _bpy = fetch_daily_macd_tf(sym, tf_label)
                        if df_tf is None: continue
                        sigs = detect_daily_macd_signals(df_tf, tf_label, bpy)
                        for s in sigs:
                            if s["action"].startswith("ENTRY_"):
                                s["_tf_prio"] = tf_prio
                                s["_tf_label"] = tf_label
                                s["_bpy"] = bpy
                                sigs_by_side[s["side"]].append(s)
                    except Exception: continue

                for side in ("LONG", "SHORT"):
                    side_sigs = sigs_by_side[side]
                    if not side_sigs: continue
                    side_sigs.sort(key=lambda x: x["_tf_prio"], reverse=True)
                    sig = side_sigs[0]
                    tf_label = sig["_tf_label"]
                    log_key = (f"PAPER:{sym}:{side}" if DAILY_PAPER_MODE else f"{sym}:{side}")

                    if log_key in log["open_entries"]: continue
                    if not DAILY_PAPER_MODE and has_position(open_positions, sym, side): continue
                    if not DAILY_PAPER_MODE and not can_open_more(): break

                    cooldown_key = f"DailyMACD:{sym}:{side}"
                    bar_ts = sig.get("_bar_ts")
                    if bar_ts and hours_since_last_entry(log, cooldown_key, bar_ts) < DAILY_COOLDOWN_HOURS: continue

                    ok, reason = is_signal_ok(sig, FILTERS_DAILY)
                    if not ok: continue

                    try:
                        df_tf_check, _ = fetch_daily_macd_tf(sym, tf_label)
                        if df_tf_check is None: continue
                        current_price = float(df_tf_check["close"].iloc[-1])
                    except Exception: continue

                    if side == "LONG" and not (sig["sl"] < current_price < sig["tp"]): continue
                    if side == "SHORT" and not (sig["tp"] < current_price < sig["sl"]): continue

                    risk_amount = balance_usd * RISK_PCT_PER_TRADE
                    sl_frac = sig["sl_pct"] / 100.0
                    notional = min(risk_amount / sl_frac, max_notional)
                    margin = max(notional / LEVERAGE, 1.0)

                    if DAILY_PAPER_MODE:
                        sig["_paper"] = True; sig["_tf_label"] = tf_label
                        log["open_entries"][log_key] = {
                            "symbol": sym, "strategy": "DailyMACD", "side": side,
                            "entry": sig["entry"], "sl": sig["sl"], "tp": sig["tp"],
                            "margin": margin, "tf": tf_label, "paper": True,
                        }
                        if bar_ts: record_entry_bar(log, cooldown_key, bar_ts)
                        new_trades.append((sym, sig, margin))
                        print(f"  📝 [PAPER {tf_label}] DailyMACD {side} {sym} @ {fmt_price(sig['entry'])}")
                    else:
                        coid = f"DailyMACD-{tf_label}-{side}-{sym}-{int(now.timestamp())}"
                        sl_r = round_price(sig["sl"], sig["entry"]); tp_r = round_price(sig["tp"], sig["entry"])
                        try:
                            resp = open_position(sym, side.lower(), margin, LEVERAGE, sl_r, tp_r, coid)
                        except Exception as e: continue
                        if resp is None or resp.get("idempotent"): continue
                        
                        sig["_paper"] = False; sig["_tf_label"] = tf_label
                        log["open_entries"][log_key] = {
                            "symbol": sym, "strategy": "DailyMACD", "side": side,
                            "entry": sig["entry"], "sl": sig["sl"], "tp": sig["tp"],
                            "margin": margin, "tf": tf_label, "paper": False,
                        }
                        if bar_ts: record_entry_bar(log, cooldown_key, bar_ts)
                        new_trades.append((sym, sig, margin))
                        print(f"  🟢 [LIVE {tf_label}] DailyMACD {side} {sym} @ {fmt_price(sig['entry'])}")

    save_log(log)

    # LEDGER
    for strat in ["DailyMACD", "Donchian"]:
        n_open = len([t for t in new_trades if t[0] == strat])
        n_closed = len([t for t in newly_closed if t.get("strategy") == strat])
        if n_open or n_closed:
            log_ledger(today_str, strat, n_open, n_closed, balance_usd)

    # TELEGRAM
    msg_blocks = []

    if kill_on:
        msg_blocks.append(f"🛑 <b>KILL SWITCH: entries paused</b>\n{kill_reason}")

    if health_warn:
        msg_blocks.append(f"{health_warn}")

    if new_trades:
        dn_new = [t for t in new_trades if t[0] == "Donchian"]
        dm_new = [t for t in new_trades if t[0] == "DailyMACD"]
        if dn_new:
            msg_blocks.append(
                f"<b>🔔 Donchian — {now.strftime('%H:%M')} UTC</b>\n"
                f"<i>{ACTIVE_DONCHIAN_PROFILE['name']} (spot only)</i>"
            )
            for _, sym, sig, notional, margin in dn_new:
                msg_blocks.append(format_donchian_trade(sym, sig, notional, margin,
                                                         balance_usd,
                                                         ACTIVE_DONCHIAN_PROFILE_KEY,
                                                         ACTIVE_DONCHIAN_PROFILE))
        if dm_new:
            tag = "📝 PAPER " if DAILY_PAPER_MODE else ""
            msg_blocks.append(
                f"<b>{tag}🔔 DailyMACD — {now.strftime('%H:%M')} UTC</b>\n"
                f"<i>{DAILY_PROFILE['name']} | Exit: {DAILY_EXIT_MODE.upper()}</i>"
            )
            for _, sym, sig, notional, margin in dm_new:
                msg_blocks.append(format_daily_trade(sym, sig, notional, margin,
                                                      balance_usd, DAILY_PROFILE_KEY,
                                                      DAILY_PROFILE))

    if newly_closed:
        msg_blocks.append(f"<b>🔒 بسته‌شده — {now.strftime('%H:%M')} UTC</b>")
        for t in newly_closed:
            msg_blocks.append(format_closed_trade(t))

    if new_trades or newly_closed:
        msg_blocks.append(format_strategy_summary(log, "DailyMACD", "📊 DailyMACD Stats"))
        msg_blocks.append(format_strategy_summary(log, "Donchian",  "📊 Donchian Stats"))

    if msg_blocks:
        send_telegram("\n\n".join(msg_blocks))

    print(f"\nResult: {len(new_trades)} new | {len(newly_closed)} closed"
          f"{' | KILL SWITCH ACTIVE' if kill_on else ''}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    INTERVAL_MIN = int(os.environ.get("INTERVAL_MIN", "0"))

    if INTERVAL_MIN <= 0:
        sys.exit(run_scan())

    interval_sec = INTERVAL_MIN * 60
    print(f"Loop mode: every {INTERVAL_MIN} min (Ctrl+C to stop)")
    while True:
        try:
            run_scan()
        except KeyboardInterrupt:
            print("\nStopped by user")
            sys.exit(0)
        except Exception as e:
            print(f"WARNING: Scan crashed: {e}")
        print(f"Sleeping {INTERVAL_MIN} min...")
        try:
            time.sleep(interval_sec)
        except KeyboardInterrupt:
            print("\nStopped by user")
            sys.exit(0)
