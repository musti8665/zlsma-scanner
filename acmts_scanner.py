#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MarginPad Dual-Strategy Scanner — Donchian + DailyMACD
========================================================
VolTarget REMOVED (WF Sharpe mean=0.11, std=2.06 — coin flip).

Active strategies:
  DailyMACD  (1d)      ✅ PRIMARY   WF mean=1.79, std=0.99 — stable
  Donchian   (5m-4h)   ⚠️  SMALL    WF mean=1.88, std=2.18 — volatile

Loop mode: هر ۳۰ دقیقه یک بار اسکن می‌کند (بدون نیاز به cron).

Kill switch pauses new entries if:
  • 3 consecutive DailyMACD losses with total < -15%, OR
  • DailyMACD drawdown > 20%

Paper ledger written to paper_ledger.csv
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

# ============================================================
# STRATEGY TOGGLES
# ============================================================
ENABLE_DONCHIAN = True
ENABLE_DAILY    = True

# ============================================================
# RISK PROFILES (used by DailyMACD Vol-Targeting sizing)
# ============================================================
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

# ============================================================
# DONCHIAN PROFILES
# ============================================================
DONCHIAN_PROFILES = {
    "spot_safe": {"name": "🛡 Spot-safe", "sf": 0.25, "leverage": 1.0},
    "lev_2x":    {"name": "⚡ 2x Lev",    "sf": 0.50, "leverage": 1.5},
    "lev_3x":    {"name": "🚀 3x Lev",    "sf": 0.75, "leverage": 2.25},
}
ACTIVE_DONCHIAN_PROFILE_KEY = "spot_safe"   # FORCED — do not use leverage
ACTIVE_DONCHIAN_PROFILE = DONCHIAN_PROFILES[ACTIVE_DONCHIAN_PROFILE_KEY]

# ============================================================
# DAILY MACD CONFIG
# ============================================================
DAILY_PAPER_MODE = os.environ.get("DAILY_PAPER_MODE", "true").lower() == "true"
DAILY_EXIT_MODE  = os.environ.get("DAILY_EXIT_MODE", "macd").lower()  # "macd" | "kijun"
DAILY_COOLDOWN_HOURS = 30

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
DONCHIAN_SYMBOLS = sorted(set(SYMBOLS + ["BTC", "ETH", "SOL"]))
DAILY_SYMBOLS    = SYMBOLS

# ============================================================
# PARAMS
# ============================================================
DONCHIAN_PARAMS = {
    "entry_n": 8, "exit_n": 2,
    "vol_mult": 1.5, "atr_mult": 3.0, "ema_period": 150,
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
DAILY_TIMEFRAMES = [("1d", ["1440", "1d", "D", "D1"], 1, 365)]

# ============================================================
# LIMITS
# ============================================================
LEVERAGE = 10
MAX_DONCHIAN_POSITIONS = 3
MAX_DAILY_POSITIONS    = 5
MAX_TOTAL_POSITIONS = MAX_DONCHIAN_POSITIONS + MAX_DAILY_POSITIONS

MAX_NOTIONAL_PCT = 0.75
LOG_FILE = Path("marginpad_trades_log.json")
LEDGER_FILE = Path("paper_ledger.csv")

# Kill switch thresholds
KILL_CONSEC_LOSSES    = 3
KILL_CONSEC_LOSS_SUM  = -15.0
KILL_MAX_DRAWDOWN     = -20.0
KILL_SILENCE_DAYS     = 10

# ============================================================
# FILTER THRESHOLDS
# ============================================================
FILTERS_DONCHIAN = {"min_sl": 0.5, "min_tp": 0.3, "min_rr": 0.5}
FILTERS_DAILY    = {"min_sl": 0.5, "min_tp": 1.0, "min_rr": 1.5}

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

def is_signal_ok(sig, filters) -> tuple:
    if sig["sl_pct"] < filters["min_sl"]:
        return False, f"SL too tight ({sig['sl_pct']:.2f}%)"
    if sig["tp_pct"] < filters["min_tp"]:
        return False, f"TP too tight ({sig['tp_pct']:.2f}%)"
    rr = sig["tp_pct"] / sig["sl_pct"]
    if rr < filters["min_rr"]:
        return False, f"R/R too low ({rr:.2f})"
    return True, ""

def compute_dynamic_size(vol30: float, profile: dict) -> float:
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
                print(f"  [telegram] HTTP {r.status_code}: {r.text[:200]}")
                return False
        return True
    except Exception as e:
        print(f"  [telegram] error: {e}")
        return False

def _sizes_block_risk(vol30, active_key):
    """Display all 3 risk profile sizes (used by DailyMACD)."""
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
    exit_desc = ("بسته شدن بالای Donchian(2) high"
                 if side == "SHORT" else "بسته شدن زیر Donchian(2) low")
    return (
        f"{emoji} <b>Donchian: {sym} {side}</b>\n"
        f"🧠 <b>Donchian(8/2)+Vol+EMA150</b> | 📊 <b>{tf}</b>\n\n"
        f"📍 ورود: <code>{fmt_price(sig['entry'])}</code>\n"
        f"🛑 SL: <code>{fmt_price(sig['sl'])}</code> ({sig['sl_pct']:.2f}%) — 3×ATR\n"
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

# ============================================================
# KILL SWITCH + HEALTH CHECK + LEDGER
# ============================================================
def check_kill_switch(log):
    """Returns (triggered: bool, reason: str)."""
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
    """Returns warning string or None."""
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

# ============================================================
# LOG
# ============================================================
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
    if side == "LONG" and last_close < last_kijun: return True
    if side == "SHORT" and last_close > last_kijun: return True
    return False

# ============================================================
# STRATEGY A: Donchian Breakout (LONG + SHORT)
# ============================================================
def detect_donchian_signals(df, tf_label):
    P = DONCHIAN_PARAMS
    n = len(df)
    if n < P["min_bars"]: return []
    if "volume" not in df.columns: return []

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
        if i < P["min_bars"]: continue
        if pd.isna(don_hi_entry.iloc[i]) or pd.isna(don_lo_entry.iloc[i]): continue
        if pd.isna(atr.iloc[i]) or pd.isna(ema.iloc[i]) or pd.isna(vol_ma.iloc[i]): continue

        atr_val = float(atr.iloc[i])
        if atr_val <= 0: continue
        close_v = float(close.iloc[i])
        ema_v = float(ema.iloc[i])
        vol_ok = float(volume.iloc[i]) > P["vol_mult"] * float(vol_ma.iloc[i])
        try: bar_ts = int(pd.Timestamp(df.index[i]).timestamp())
        except Exception: bar_ts = None

        sigs = []
        if close_v < float(don_lo_exit.iloc[i]):
            sigs.append({"action": "EXIT_LONG", "side": "LONG", "entry": close_v,
                         "_tf_label": tf_
