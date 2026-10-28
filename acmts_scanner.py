#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MarginPad Triple-Strategy Scanner — Donchian + DailyMACD + C_BE_trail (FIXED)
============================================================================
ENV:
  MARGINPAD_API_KEY   (fallback hardcoded)
  TELEGRAM_TOKEN      (fallback hardcoded)
  TELEGRAM_CHAT_ID    (auto-detected from getUpdates)
  VERBOSE=1           per-symbol diagnosis
  DRY_RUN=1           detect only, don't open trades
  INTERVAL_MIN=5      loop every N minutes (0 = single run)
  MIN_MARGIN_USD=5    skip trades with smaller margin
  KILL_COUNT_PAPER=0  1 = paper losses count toward kill-switch
  RESET_KILL=1        clear kill-switch on startup
  DAILY_PAPER_MODE=false  to trade DailyMACD live
"""

import os, sys, json, csv, time, requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
from numpy.lib.stride_tricks import sliding_window_view

ENABLE_DONCHIAN = True
ENABLE_DAILY    = True
ENABLE_CBT      = True

# ============================================================
# ENV / MODES
# ============================================================
VERBOSE          = os.environ.get("VERBOSE", "0") == "1"
DRY_RUN          = os.environ.get("DRY_RUN", "0") == "1"
INTERVAL_MIN     = int(os.environ.get("INTERVAL_MIN", "0"))
MIN_MARGIN_USD   = float(os.environ.get("MIN_MARGIN_USD", "5.0"))
KILL_COUNT_PAPER = os.environ.get("KILL_COUNT_PAPER", "0") == "1"
RESET_KILL       = os.environ.get("RESET_KILL", "0") == "1"

# ============================================================
# پروفایل‌های دینامیک
# ============================================================
PROFILES = {
    "conservative": {"name": "🛡️ محافظه‌کار", "target_vol": 0.20,
                     "size_min": 0.10, "size_max": 0.60},
    "balanced":     {"name": "⭐ متعادل",     "target_vol": 0.30,
                     "size_min": 0.10, "size_max": 0.75},
    "aggressive":   {"name": "🚀 تهاجمی",     "target_vol": 0.40,
                     "size_min": 0.10, "size_max": 0.90},
}

def _get_profile(env_key, default="conservative"):
    key = os.environ.get(env_key, default).strip()
    if key not in PROFILES:
        key = "conservative"
    return key, PROFILES[key]

DAILY_PROFILE_KEY,    DAILY_PROFILE    = _get_profile("DAILY_PROFILE")
DONCHIAN_PROFILE_KEY, DONCHIAN_PROFILE = _get_profile("DONCHIAN_PROFILE")
CBT_PROFILE_KEY,      CBT_PROFILE      = _get_profile("CBT_PROFILE")

VOL_FLOOR, VOL_CEIL = 0.15, 0.80

DAILY_PAPER_MODE = os.environ.get("DAILY_PAPER_MODE", "true").lower() == "true"
DAILY_EXIT_MODE  = os.environ.get("DAILY_EXIT_MODE", "macd").lower()
DAILY_COOLDOWN_HOURS = 30
CBT_COOLDOWN_HOURS   = 15

API_KEY = os.environ.get("MARGINPAD_API_KEY",
                          "mpb_d04c938486b9f5e711f9a736084fa9b5").strip()
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN",
                           "8601458987:AAEvcxx2ClOTfQIqEp9Ok6TwcvKRaMfIeqo").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

if not API_KEY:
    print("FATAL: MARGINPAD_API_KEY missing"); sys.exit(1)

BASE   = "https://marginpad.io/api/bot/v1"
HEADERS = {"X-API-Key": API_KEY}
H_JSON  = {**HEADERS, "Content-Type": "application/json"}

# ============================================================
# TELEGRAM auto-detect
# ============================================================
def _auto_detect_chat_id():
    if not TG_TOKEN: return ""
    try:
        r = requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates", timeout=10)
        data = r.json()
        if data.get("ok") and data.get("result"):
            for upd in reversed(data["result"]):
                msg = (upd.get("message") or upd.get("channel_post")
                       or upd.get("edited_message"))
                if msg and msg.get("chat", {}).get("id"):
                    return str(msg["chat"]["id"])
    except Exception as e:
        print(f"  [tg-detect] error: {e}")
    return ""

if not TG_CHAT and TG_TOKEN:
    print("⏳ TELEGRAM_CHAT_ID not set — trying auto-detect...")
    TG_CHAT = _auto_detect_chat_id()
    if TG_CHAT:
        print(f"✅ Auto-detected chat ID: {TG_CHAT}")
        print(f"   export TELEGRAM_CHAT_ID={TG_CHAT}")
    else:
        print("⚠️  Auto-detect failed. Fix:")
        print(f"  1) Send /start to your bot")
        print(f"  2) Open: https://api.telegram.org/bot{TG_TOKEN}/getUpdates")
        print("  3) Copy \"chat\":{\"id\": <NUMBER>}")
        print("  4) export TELEGRAM_CHAT_ID=<NUMBER>")
        print("  ⚠️  Continuing WITHOUT Telegram notifications.\n")

# ============================================================
# 22 ارز
# ============================================================
SYMBOLS = [
    "ADA", "AVAX", "LINK", "LTC",
    "ENA", "ATOM", "NEAR", "ARB", "OP", "PEPE", "SHIB", "SUI",
    "APT", "INJ", "TIA", "SEI", "WIF", "LDO", "UNI", "AAVE", "FIL", "RENDER",
]
DONCHIAN_SYMBOLS = SYMBOLS
DAILY_SYMBOLS    = SYMBOLS
CBT_SYMBOLS      = SYMBOLS

DONCHIAN_PARAMS = {
    "entry_n": 10, "exit_n": 5, "vol_mult": 2.0, "atr_mult": 2.5,
    "ema_period": 100, "min_bars": 200, "tp_rr": 5.0,
}
DAILY_MACD_PARAMS = {
    "pivot_period": 8, "qqe_rsi": 20, "qqe_sf": 9,
    "macd_fast": 5, "macd_slow": 13, "macd_signal": 10,
    "sl_atr_mult": 2.5, "tp_rr": 2.0, "min_bars": 60,
    "vol_period": 30, "kijun_p": 20,
}

# --- C_BE_trail ---
CBT_PVT       = 50
CBT_TL        = 2
CBT_SR_EX     = 3
CBT_KIJUN_LEN = 36
CBT_ATR_LEN   = 14
CBT_ATR_BASE  = 250
CBT_ATR_THR   = 0.90
CBT_QQE_RP    = 14
CBT_QQE_SM    = 5
CBT_QQE_F     = 3.0
CBT_SL_MULT   = 2.5
CBT_TP_RR     = 2.5
CBT_BE_AT_R   = 1.0
CBT_TRAIL_R   = 1.5
CBT_TRAIL_ATR = 1.2
CBT_MAX_BARS  = 40
CBT_MIN_BARS  = 500
# ✅ FIX: واقعی‌ترین حداقل lookback برای همه اندیکاتورهای CBT
CBT_MIN_LOOKBACK = max(CBT_KIJUN_LEN, CBT_ATR_BASE, CBT_PVT * 4 + 20) + 5  # = 255

SCAN_TIMEFRAMES = [
    ("5m",  ["5", "5m"],          1, 365 * 24 * 12),
    ("15m", ["15", "15m"],        2, 365 * 24 * 4),
    ("30m", ["30", "30m"],        3, 365 * 24 * 2),
    ("1h",  ["60", "1h"],         4, 365 * 24),
    ("4h",  ["240", "4h"],        5, 365 * 6),
]
DAILY_MACD_TIMEFRAMES = [
    ("4h", ["240", "4h"],          5, 365 * 6),
    ("1d", ["1440", "1d"],         6, 365),
]

LEVERAGE = 10
MAX_DONCHIAN_POSITIONS = 3
MAX_DAILY_POSITIONS    = 5
MAX_CBT_POSITIONS      = 3
MAX_TOTAL_POSITIONS = MAX_DONCHIAN_POSITIONS + MAX_DAILY_POSITIONS + MAX_CBT_POSITIONS

MAX_NOTIONAL_PCT = 0.75
LOG_FILE    = Path("marginpad_trades_log.json")
LEDGER_FILE = Path("paper_ledger.csv")

KILL_CONSEC_LOSSES, KILL_CONSEC_LOSS_SUM = 3, -15.0
KILL_MAX_DRAWDOWN,  KILL_SILENCE_DAYS     = -20.0, 10

FILTERS_DONCHIAN = {"min_sl": 0.5, "min_tp": 0.3, "min_rr": 0.5}
FILTERS_DAILY    = {"min_sl": 0.5, "min_tp": 1.0, "min_rr": 1.5}
FILTERS_CBT      = {"min_sl": 0.5, "min_tp": 1.0, "min_rr": 1.5}

# ============================================================
# CACHE & VALIDATION
# ============================================================
_KLINES_CACHE = {}
def clear_klines_cache(): _KLINES_CACHE.clear()

def _normalize_klines(data):
    """اعتبارسنجی و نرمال‌سازی پاسخ /klines"""
    if data is None: return None
    if isinstance(data, dict):
        for k in ("klines", "data", "result", "candles", "items"):
            if k in data:
                data = data[k]; break
        else:
            raise ValueError(f"Unknown klines keys: {list(data.keys())[:5]}")
    if not isinstance(data, list):
        raise ValueError(f"Klines is {type(data).__name__}, expected list")
    if data and not isinstance(data[0], dict):
        raise ValueError(f"Kline item not dict: {type(data[0]).__name__}")
    for req in ("time", "open", "high", "low", "close"):
        if data and req not in data[0]:
            raise ValueError(f"Kline missing field '{req}'. Keys: {list(data[0].keys())[:6]}")
    return data

def get_klines_cached(symbol, interval):
    key = (symbol, interval)
    if key in _KLINES_CACHE: return _KLINES_CACHE[key]
    try:
        k = get_klines(symbol, interval)
        _KLINES_CACHE[key] = k
        return k
    except Exception as e:
        if VERBOSE: print(f"  [klines] {symbol} {interval}: {e}")
        _KLINES_CACHE[key] = None
        return None

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
    if sig["sl_pct"] < filters["min_sl"]: return False, f"SL too tight ({sig['sl_pct']:.2f}%)"
    if sig["tp_pct"] < filters["min_tp"]: return False, f"TP too tight ({sig['tp_pct']:.2f}%)"
    rr = sig["tp_pct"] / sig["sl_pct"]
    if rr < filters["min_rr"]: return False, f"R/R too low ({rr:.2f})"
    return True, ""

def compute_dynamic_size(vol30, profile):
    if vol30 is None or np.isnan(vol30) or vol30 <= 0:
        return profile["target_vol"]
    clamped = max(VOL_FLOOR, min(VOL_CEIL, vol30))
    dyn = profile["target_vol"] / clamped
    return max(profile["size_min"], min(profile["size_max"], dyn))

def hours_since_last_entry(log, log_key, current_bar_ts):
    last = log.get("last_entry_bar", {}).get(log_key)
    if last is None: return 999999
    return (current_bar_ts - last) / 3600

def record_entry_bar(log, log_key, bar_ts):
    log.setdefault("last_entry_bar", {})[log_key] = int(bar_ts)

def log_key_sym(log_key):
    if not log_key: return ""
    for pref in ("Donchian:", "CBT:"):
        if log_key.startswith(pref):
            return log_key.split(":", 1)[1]
    if log_key.startswith("PAPER:"):
        parts = log_key.split(":")
        return parts[1] if len(parts) >= 2 else log_key
    if ":" in log_key: return log_key.split(":", 1)[0]
    return log_key

def has_position(open_positions, sym, side):
    pos = open_positions.get(sym)
    if not pos: return False
    return (pos.get("side") or "").lower() == side.lower()

def can_open_more(open_positions, pending_trades, max_total=MAX_TOTAL_POSITIONS):
    return len(open_positions) + len(pending_trades) < max_total

# ============================================================
# TELEGRAM
# ============================================================
def send_telegram(text):
    if not TG_TOKEN or not TG_CHAT: return False
    ok = True
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT, "text": chunk, "parse_mode": "HTML",
                      "disable_web_page_preview": True},
                timeout=15,
            )
            if r.status_code != 200:
                print(f"  [telegram] HTTP {r.status_code}: {r.text[:200]}")
                ok = False
        except Exception as e:
            print(f"  [telegram] error: {e}")
            ok = False
    return ok

# (فرمت‌کننده‌ها بدون تغییر)
def _sizes_block_risk(vol30, active_key):
    block = "\n📐 <b>سایز ۳ پروفایل:</b>\n"
    for k, p in PROFILES.items():
        d = compute_dynamic_size(vol30, p)
        marker = " ← اجرا شد" if k == active_key else ""
        block += f"  • {p['name']}: <b>{d*100:.1f}%</b>{marker}\n"
    return block

def format_donchian_trade(sym, sig, notional, margin, balance, profile_key, profile):
    side = sig["side"]; emoji = "🟢" if side == "LONG" else "🔴"
    rr = sig["tp_pct"] / sig["sl_pct"] if sig["sl_pct"] > 0 else 0
    tf = sig.get("_tf_label", "1h")
    v_raw = sig.get("_vol30", 0); vol30 = (v_raw*100) if not pd.isna(v_raw) else 0
    exit_desc = ("بسته شدن بالای Donchian(5) high" if side == "SHORT"
                 else "بسته شدن زیر Donchian(5) low")
    return (
        f"{emoji} <b>Donchian: {sym} {side}</b>\n"
        f"🧠 <b>Donchian(10/5)+Vol+EMA100</b> | 📊 <b>{tf}</b>\n\n"
        f"📍 ورود: <code>{fmt_price(sig['entry'])}</code>\n"
        f"🛑 SL: <code>{fmt_price(sig['sl'])}</code> ({sig['sl_pct']:.2f}%) — 2.5×ATR\n"
        f"🎯 TP: <code>{fmt_price(sig['tp'])}</code> ({sig['tp_pct']:.2f}%) — 5R\n"
        f"📊 R/R: <b>{rr:.2f}</b> | Vol30: <b>{vol30:.1f}%</b>\n"
        f"💰 اجرا: <b>{profile['name']}</b> | Notional ${notional:,.2f} "
        f"| مارجین ${margin:.2f} ({LEVERAGE}× FUTURES)\n"
        f"🏦 موجودی: ${balance:,.2f}\n"
        f"{_sizes_block_risk(v_raw if not pd.isna(v_raw) else 0, profile_key)}"
        f"ℹ️ خروج اصلی: {exit_desc}\n"
    )

def format_daily_trade(sym, sig, notional, margin, balance, profile_key, profile):
    side = sig["side"]; emoji = "🟢" if side == "LONG" else "🔴"
    rr = sig["tp_pct"] / sig["sl_pct"] if sig["sl_pct"] > 0 else 0
    tf = sig.get("_tf_label", "1d")
    v_raw = sig.get("_vol30", 0); vol30 = (v_raw*100) if not pd.isna(v_raw) else 0
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
        f"{_sizes_block_risk(v_raw if not pd.isna(v_raw) else 0, profile_key)}"
        f"⏱ Cooldown: {DAILY_COOLDOWN_HOURS}h\n"
    )

def format_cbt_trade(sym, sig, notional, margin, balance, profile_key, profile):
    rr = sig["tp_pct"] / sig["sl_pct"] if sig["sl_pct"] > 0 else 0
    tf = sig.get("_tf_label", "30m")
    v_raw = sig.get("_vol30", 0); vol30 = (v_raw*100) if not pd.isna(v_raw) else 0
    return (
        f"🔴 <b>C_BE_trail: {sym} SHORT</b>\n"
        f"🧠 <b>TSR + ATR-Ratio + Kijun(36) + QQE</b> | 📊 <b>{tf}</b>\n"
        f"🚪 Exit: <b>BE@{CBT_BE_AT_R}R → Trail@{CBT_TRAIL_R}R → Max {CBT_MAX_BARS}bars</b>\n\n"
        f"📍 ورود: <code>{fmt_price(sig['entry'])}</code>\n"
        f"🛑 SL: <code>{fmt_price(sig['sl'])}</code> ({sig['sl_pct']:.2f}%) — {CBT_SL_MULT}×ATR\n"
        f"🎯 TP: <code>{fmt_price(sig['tp'])}</code> ({sig['tp_pct']:.2f}%) — {CBT_TP_RR}R\n"
        f"📊 R/R: <b>{rr:.2f}</b> | Vol30: <b>{vol30:.1f}%</b>\n"
        f"💰 اجرا: <b>{profile['name']}</b> | Notional ${notional:,.2f} "
        f"| مارجین ${margin:.2f} ({LEVERAGE}× FUTURES)\n"
        f"🏦 موجودی: ${balance:,.2f}\n"
        f"{_sizes_block_risk(v_raw if not pd.isna(v_raw) else 0, profile_key)}"
        f"⏱ Cooldown: {CBT_COOLDOWN_HOURS}h | ⏳ Max hold: {CBT_MAX_BARS*0.5:.0f}h\n"
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
    total = len(df); wins = len(df[df["pnl_pct"] > 0]); losses = total - wins
    wr = (wins / total) * 100
    total_pnl = df["pnl_pct"].sum(); avg_pnl = total_pnl / total
    long_df = df[df["side"] == "LONG"]; short_df = df[df["side"] == "SHORT"]
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
    if not closed: return False, ""
    # ✅ FIX: فقط live (یا همه اگر KILL_COUNT_PAPER=1)
    dm = [t for t in closed
          if t.get("strategy") == "DailyMACD"
          and (KILL_COUNT_PAPER or not t.get("paper", False))]
    if len(dm) >= KILL_CONSEC_LOSSES:
        last_n = dm[-KILL_CONSEC_LOSSES:]
        if all(t["pnl_pct"] < 0 for t in last_n):
            total = sum(t["pnl_pct"] for t in last_n)
            if total < KILL_CONSEC_LOSS_SUM:
                return True, f"🛑 KILL: {KILL_CONSEC_LOSSES} ضرر متوالی DailyMACD live ({total:+.1f}%)"
    if len(dm) >= 10:
        pnls = [t["pnl_pct"] for t in dm]
        eq = np.cumsum(pnls); peak = np.maximum.accumulate(eq)
        dd = float((eq - peak).min())
        if dd < KILL_MAX_DRAWDOWN:
            return True, f"🛑 KILL: DailyMACD drawdown {dd:.1f}%"
    return False, ""

def check_health(log):
    last_entry = log.get("last_entry_bar", {})
    if not last_entry: return None
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
    return _normalize_klines(r.json())

def get_klines_fallback(symbol, intervals):
    for interval in intervals:
        k = get_klines_cached(symbol, interval)
        if k and len(k) >= 50: return k
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
        try: err = r.json()
        except Exception: err = r.text[:500]
        print(f"  [{symbol}] open HTTP {r.status_code}: {err}")
        print(f"  [{symbol}] payload: {body}")
        return None
    return r.json()

def close_position(pos_dict):
    if not pos_dict: return None
    pos_id = pos_dict.get("id") or pos_dict.get("position_id") or pos_dict.get("order_id")
    symbol = pos_dict.get("symbol")
    if not pos_id or not symbol: return None
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
        try: return json.loads(LOG_FILE.read_text())
        except Exception: pass
    return {"open_entries": {}, "closed_trades": [], "last_entry_bar": {},
            "kill_switch": None}

def save_log(log):
    LOG_FILE.write_text(json.dumps(log, indent=2, default=str))

# ============================================================
# INDICATORS
# ============================================================
def compute_atr(df, length=14):
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)),
                                      np.abs(l - np.roll(c, 1))))
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
    if len(df) < kijun_p + 3: return False
    kijun = (df["high"].rolling(kijun_p).max() + df["low"].rolling(kijun_p).min()) / 2
    last_close = float(df["close"].iloc[-1]); last_kijun = float(kijun.iloc[-1])
    if np.isnan(last_kijun): return False
    if side == "LONG" and last_close < last_kijun:  return True
    if side == "SHORT" and last_close > last_kijun: return True
    return False

# ============================================================
# C_BE_trail
# ============================================================
def _atr_fn(h, l, c, p=14):
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)),
                                      np.abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    return pd.Series(tr).ewm(alpha=1/p, adjust=False).mean().values

def _rsi_fn(c, p):
    d = np.diff(c, prepend=c[0])
    g = pd.Series(np.where(d > 0, d, 0)).ewm(alpha=1/p, adjust=False).mean().values
    lo = pd.Series(np.where(d < 0, -d, 0)).ewm(alpha=1/p, adjust=False).mean().values
    return 100 - 100 / (1 + g / np.where(lo > 0, lo, np.nan))

def _qqe_trend(c, rp=14, sm=5, factor=3.0):
    n = len(c)
    if n == 0: return np.zeros(0, dtype=int)
    r = _rsi_fn(c, rp)
    rma = pd.Series(r).ewm(span=sm, adjust=False).mean().values
    rdiff = np.abs(np.diff(r, prepend=r[0]))
    atr_r = pd.Series(rdiff).ewm(alpha=1/(2*rp-1), adjust=False).mean().values
    dar = atr_r * factor
    trend = np.zeros(n, dtype=int)
    lb = np.full(n, np.nan); sb = np.full(n, np.nan)
    lb[0] = rma[0] - dar[0]; sb[0] = rma[0] + dar[0]; trend[0] = 1
    for i in range(1, n):
        nl, ns = rma[i] - dar[i], rma[i] + dar[i]
        lb[i] = max(lb[i-1], nl) if (rma[i-1] > lb[i-1] and rma[i] > lb[i-1]) else nl
        sb[i] = min(sb[i-1], ns) if (rma[i-1] < sb[i-1] and rma[i] < sb[i-1]) else ns
        if rma[i] > sb[i-1]:   trend[i] = 1
        elif rma[i] < lb[i-1]: trend[i] = -1
        else:                  trend[i] = trend[i-1]
    return trend

def build_tsr_short(high, low, pvt=50, tl=2, sr_except=3):
    n = len(high)
    sm = np.zeros(n, dtype=bool)
    if n < pvt * 4 + 20: return sm

    win = 2 * pvt + 1
    hw = sliding_window_view(high, win)
    ch = hw[:, pvt:pvt+1]
    is_high = (ch > hw[:, :pvt]).all(1) & (ch > hw[:, pvt+1:]).all(1)
    h_bars = np.where(is_high)[0] + pvt

    lw = sliding_window_view(low, win)
    cl = lw[:, pvt:pvt+1]
    is_low = (cl < lw[:, :pvt]).all(1) & (cl < lw[:, pvt+1:]).all(1)
    l_bars = np.where(is_low)[0] + pvt

    low_pivots  = sorted([(int(b), float(low[b]), int(b+pvt)) for b in l_bars], key=lambda p: p[2])
    high_pivots = sorted([(int(b), float(high[b]), int(b+pvt)) for b in h_bars], key=lambda p: p[2])

    for pivots, is_low_side in [(low_pivots, True), (high_pivots, False)]:
        if len(pivots) < 2: continue
        for k in range(len(pivots)):
            start = pivots[k][2]
            if start >= n: break
            end = min(pivots[k+1][2] if k+1 < len(pivots) else n, n)
            if start >= end: continue
            visible = pivots[max(0, k+1-tl):k+1]
            for i in range(len(visible) - 1):
                a_bar, a_px, _ = visible[i]
                for j in range(i+1, len(visible)):
                    b_bar, b_px, _ = visible[j]
                    if is_low_side and a_px >= b_px:   continue
                    if not is_low_side and a_px <= b_px: continue
                    if b_bar <= a_bar:                  continue
                    slope = (b_px - a_px) / (b_bar - a_bar)
                    t_range = np.arange(start, end)
                    price_t = a_px + (t_range - a_bar) * slope
                    cond = (low[start:end] <= price_t) if is_low_side else (high[start:end] >= price_t)
                    if cond.any():
                        bb = start + int(np.argmax(cond))
                        if is_low_side: sm[bb] = True

    for _, p_px, p_conf in low_pivots:
        t0 = p_conf + sr_except + 1
        if t0 >= n: continue
        cond = low[t0:] < p_px
        if cond.any(): sm[t0 + int(np.argmax(cond))] = True
    return sm

def detect_c_be_trail_signals(df):
    """
    ✅ FIXED: حذف باگ `if i < CBT_MIN_BARS: continue` که باعث می‌شد
    همیشه skip شود (i=498 < 500).
    """
    n = len(df)
    if n < CBT_MIN_BARS: return []
    c = df["close"].values; h = df["high"].values; l = df["low"].values

    a = _atr_fn(h, l, c, CBT_ATR_LEN)
    atr_base = pd.Series(a).rolling(CBT_ATR_BASE).mean().values
    ratio = a / np.where(atr_base > 0, atr_base, np.nan)
    kijun = (pd.Series(h).rolling(CBT_KIJUN_LEN).max().values +
             pd.Series(l).rolling(CBT_KIJUN_LEN).min().values) / 2
    q_tr = _qqe_trend(c, CBT_QQE_RP, CBT_QQE_SM, CBT_QQE_F)
    tsr = build_tsr_short(h, l, CBT_PVT, CBT_TL, CBT_SR_EX)
    vol30 = compute_vol30(df, 365 * 24 * 2)

    for offset in range(2, 5):
        i = n - offset
        # ✅ فقط lookback واقعی اندیکاتورها را چک کن
        if i < CBT_MIN_LOOKBACK: continue
        if pd.isna(ratio[i]) or pd.isna(kijun[i]): continue
        if not (tsr[i] and ratio[i] >= CBT_ATR_THR and c[i] < kijun[i] and q_tr[i] == -1):
            continue

        entry = float(c[i]); av = float(a[i])
        if av <= 0 or entry <= 0: continue
        sl = entry + CBT_SL_MULT * av
        risk = sl - entry
        tp = entry - CBT_TP_RR * risk
        sl_pct = (sl - entry) / entry * 100
        tp_pct = (entry - tp) / entry * 100
        if not (0 < sl_pct < 20): continue

        try: bar_ts = int(pd.Timestamp(df.index[i]).timestamp())
        except Exception: bar_ts = None
        vv = float(vol30.iloc[i]) if not pd.isna(vol30.iloc[i]) else 0.30

        return [{"action": "ENTRY_SHORT", "side": "SHORT", "strategy": "CBT",
                 "entry": entry, "sl": sl, "tp": tp,
                 "sl_pct": sl_pct, "tp_pct": tp_pct,
                 "_tf_label": "30m", "_bar_ts": bar_ts,
                 "_atr": av, "_risk": risk, "_vol30": vv}]
    return []

# ============================================================
# Donchian + MACD
# ============================================================
def detect_donchian_signals(df, tf_label, bars_per_year=365 * 24):
    P = DONCHIAN_PARAMS
    n = len(df)
    if n < P["min_bars"] or "volume" not in df.columns: return []

    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]
    tr = pd.concat([high - low, (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()
    don_hi_entry = high.shift(1).rolling(P["entry_n"]).max()
    don_lo_entry = low.shift(1).rolling(P["entry_n"]).min()
    don_hi_exit  = high.shift(1).rolling(P["exit_n"]).max()
    don_lo_exit  = low.shift(1).rolling(P["exit_n"]).min()
    ema = close.ewm(span=P["ema_period"], adjust=False).mean()
    vol_ma = volume.rolling(20).mean()
    vol30 = compute_vol30(df, bars_per_year)

    for offset in range(2, 15):
        i = n - offset
        if i < P["min_bars"]: continue
        if pd.isna(don_hi_entry.iloc[i]) or pd.isna(don_lo_entry.iloc[i]): continue
        if pd.isna(atr.iloc[i]) or pd.isna(ema.iloc[i]) or pd.isna(vol_ma.iloc[i]): continue

        atr_val = float(atr.iloc[i])
        if atr_val <= 0: continue
        close_v = float(close.iloc[i]); ema_v = float(ema.iloc[i])
        vol_ok = float(volume.iloc[i]) > P["vol_mult"] * float(vol_ma.iloc[i])
        vv = float(vol30.iloc[i]) if not pd.isna(vol30.iloc[i]) else 0.30
        try: bar_ts = int(pd.Timestamp(df.index[i]).timestamp())
        except Exception: bar_ts = None

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
                                 "_atr": atr_val, "_vol30": vv})

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
                                 "_atr": atr_val, "_vol30": vv})

        if sigs: return sigs
    return []

def detect_daily_macd_signals(df, tf_label, bars_per_year):
    P = DAILY_MACD_PARAMS
    n = len(df)
    if n < P["min_bars"]: return []

    close, high, low = df["close"], df["high"], df["low"]
    times = df.index
    def _e(s, nn): return s.ewm(span=nn, adjust=False).mean()
    def _r(c, nn):
        d = c.diff(); g = d.clip(lower=0); l = -d.clip(upper=0)
        ag = g.ewm(alpha=1/nn, adjust=False).mean()
        al = l.ewm(alpha=1/nn, adjust=False).mean()
        return 100 - 100/(1 + ag/al.replace(0, np.nan))

    ml = _e(close, P["macd_fast"]) - _e(close, P["macd_slow"])
    sg = _e(ml, P["macd_signal"])
    ph = high.rolling(P["pivot_period"]).max()
    pl = low.rolling(P["pivot_period"]).min()
    pm = (ph + pl) / 2
    rsi = _r(close, P["qqe_rsi"]); rma = _e(rsi, P["qqe_sf"])
    atr = compute_atr(df); vol30 = compute_vol30(df, bars_per_year)

    crosses = []
    for off in (2, 3):
        i = n - off
        if i < 1: continue
        cu = ml.iloc[i-1] <= sg.iloc[i-1] and ml.iloc[i] > sg.iloc[i]
        cd = ml.iloc[i-1] >= sg.iloc[i-1] and ml.iloc[i] < sg.iloc[i]
        try: ts = int(pd.Timestamp(times[i]).timestamp())
        except Exception: ts = None
        if cu: crosses.append(("UP", i, ts))
        if cd: crosses.append(("DOWN", i, ts))

    if not crosses: return []
    crosses.sort(key=lambda x: x[1], reverse=True)
    direction, i, bar_ts = crosses[0]
    av = float(atr[i])
    if av <= 0: return []
    vv = float(vol30.iloc[i]) if not pd.isna(vol30.iloc[i]) else 0.30
    entry = float(close.iloc[i]); out = []

    if direction == "UP":
        out.append({"action": "EXIT_SHORT", "side": "SHORT",
                    "entry": entry, "_tf_label": tf_label, "_bar_ts": bar_ts})
        if close.iloc[i] > pm.iloc[i] and rma.iloc[i] > 50:
            sl = entry - P["sl_atr_mult"] * av
            if sl > 0:
                tp = entry + P["tp_rr"] * (entry - sl)
                slp = (entry - sl) / entry * 100; tpp = (tp - entry) / entry * 100
                if 0 < slp < 20:
                    out.append({"action": "ENTRY_LONG", "side": "LONG", "strategy": "DailyMACD",
                                "entry": entry, "sl": sl, "tp": tp, "sl_pct": slp, "tp_pct": tpp,
                                "_tf_label": tf_label, "_bar_ts": bar_ts, "_vol30": vv})
    else:
        out.append({"action": "EXIT_LONG", "side": "LONG",
                    "entry": entry, "_tf_label": tf_label, "_bar_ts": bar_ts})
        if close.iloc[i] < pm.iloc[i] and rma.iloc[i] < 50:
            sl = entry + P["sl_atr_mult"] * av
            if sl > 0:
                tp = entry - P["tp_rr"] * (sl - entry)
                slp = (sl - entry) / entry * 100; tpp = (entry - tp) / entry * 100
                if 0 < slp < 20:
                    out.append({"action": "ENTRY_SHORT", "side": "SHORT", "strategy": "DailyMACD",
                                "entry": entry, "sl": sl, "tp": tp, "sl_pct": slp, "tp_pct": tpp,
                                "_tf_label": tf_label, "_bar_ts": bar_ts, "_vol30": vv})
    return out

def fetch_timeframes(sym, min_bars_needed, bars=500):
    data = {}
    for tf_label, intervals, _, _ in SCAN_TIMEFRAMES:
        try:
            k_raw = get_klines_fallback(sym, intervals)
            data[tf_label] = klines_to_df(k_raw[-bars:]) if (k_raw and len(k_raw) >= min_bars_needed) else None
        except Exception:
            data[tf_label] = None
    return data

def fetch_daily_macd_tf(sym, tf_label):
    intervals = None; bpy = 365
    for lbl, iv, _, b in DAILY_MACD_TIMEFRAMES:
        if lbl == tf_label: intervals = iv; bpy = b; break
    if intervals is None: intervals = ["1440", "1d"]
    for interval in intervals:
        k = get_klines_cached(sym, interval)
        if k and len(k) >= DAILY_MACD_PARAMS["min_bars"]:
            return klines_to_df(k[-300:]), bpy
    return None, bpy

def _get_price_safe(sym, interval="60", fallback=None):
    k = get_klines_cached(sym, interval)
    if k: return float(k[-1]["close"])
    return fallback

# ============================================================
# MAIN
# ============================================================
def run_scan():
    clear_klines_cache()
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")

    diag = {
        "symbols_scanned": 0, "klines_failed": 0,
        "signals": {}, "skipped_gate": 0, "skipped_price": 0,
        "skipped_margin": 0, "trades": 0,
    }

    print("=" * 72)
    print(f"Triple-Strategy Scan — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Mode: {'DRY_RUN' if DRY_RUN else 'LIVE'} | "
          f"Loop: {INTERVAL_MIN}min | Verbose: {VERBOSE}")
    print(f"  Telegram: {'✅ ' + TG_CHAT if TG_CHAT else '❌ not configured'}")
    print(f"  Donchian: {'ON' if ENABLE_DONCHIAN else 'OFF'} "
          f"({DONCHIAN_PROFILE['name']}, {LEVERAGE}×, max {MAX_DONCHIAN_POSITIONS})")
    print(f"  DailyMACD: {'ON' if ENABLE_DAILY else 'OFF'} "
          f"({DAILY_PROFILE['name']}, {'📝 PAPER' if DAILY_PAPER_MODE else '🟢 LIVE'}, "
          f"max {MAX_DAILY_POSITIONS})")
    print(f"  C_BE_trail: {'ON' if ENABLE_CBT else 'OFF'} "
          f"({CBT_PROFILE['name']}, 30m SHORT, {LEVERAGE}×, max {MAX_CBT_POSITIONS})")
    print(f"  Symbols: {len(SYMBOLS)}")
    print("=" * 72)

    log = load_log()

    if RESET_KILL and log.get("kill_switch"):
        print(f"  🔄 RESET_KILL: clearing previous kill switch")
        log["kill_switch"] = None
        save_log(log)

    kill_on, kill_reason = check_kill_switch(log)
    if kill_on:
        print(f"\n{kill_reason}")
        if log.get("kill_switch") is None:
            log["kill_switch"] = kill_reason; save_log(log)
            send_telegram(f"🛑 <b>KILL SWITCH</b>\n{kill_reason}\n\n"
                          f"روشن‌کردن مجدد: RESET_KILL=1")
        kill_on = True
    else:
        if log.get("kill_switch"):
            log["kill_switch"] = None; save_log(log)

    health_warn = check_health(log)
    if health_warn: print(f"  {health_warn}")

    try:
        bal = get_balance()
        balance_usd = float(bal.get("balance_usd", 10000))
        print(f"Balance: ${balance_usd:,.2f}")
    except Exception as e:
        print(f"FATAL balance: {e}"); return 1

    try:
        open_positions = get_positions()
        paper_open_n = sum(1 for v in log["open_entries"].values() if v.get("paper"))
        print(f"Open (exchange): {len(open_positions)} | Paper: {paper_open_n}")
        if open_positions: print(f"  {list(open_positions.keys())}")
    except Exception as e:
        print(f"FATAL positions: {e}"); return 1

    newly_closed = []
    new_trades = []

    def count_strategy_total(strategy):
        return sum(1 for v in log["open_entries"].values()
                   if v.get("strategy") == strategy)

    # ============ EXITS ============
    if ENABLE_DONCHIAN:
        for log_key in list(log["open_entries"].keys()):
            info = log["open_entries"].get(log_key)
            if not info or info.get("strategy") != "Donchian": continue
            sym = info.get("symbol") or log_key_sym(log_key)
            if not sym or sym not in open_positions: continue
            pos_side = info.get("side", "LONG"); entry_tf = info.get("tf", "1h")
            try:
                tf_intervals = ["60", "1h"]; bpy = 365 * 24
                for tf_label, intervals, _, b in SCAN_TIMEFRAMES:
                    if tf_label == entry_tf:
                        tf_intervals = intervals; bpy = b; break
                k_raw = get_klines_fallback(sym, tf_intervals)
                if not k_raw or len(k_raw) < DONCHIAN_PARAMS["min_bars"]: continue
                df_tf = klines_to_df(k_raw[-500:])
                signals = detect_donchian_signals(df_tf, entry_tf, bpy)
                if not any(s["action"] == f"EXIT_{pos_side}" for s in signals): continue
                if not close_position(open_positions[sym]): continue
                price = _get_price_safe(sym, "60", fallback=info.get("entry"))
                ep = info["entry"]
                pnl = ((price - ep) if pos_side == "LONG" else (ep - price)) / ep * 100
                log["open_entries"].pop(log_key)
                rec = {"symbol": sym, "strategy": "Donchian", "side": pos_side,
                       "entry": ep, "exit": price, "pnl_pct": round(pnl, 2),
                       "tf": entry_tf, "close_reason": f"Donchian-EXIT ({entry_tf})",
                       "paper": False}
                log["closed_trades"].append(rec); newly_closed.append(rec)
                open_positions.pop(sym, None)
                print(f"  [CLOSED] Donchian {sym} {pos_side} -> {pnl:+.2f}%")
            except Exception as e:
                print(f"  [DN {sym}] exit error: {e}")

    if ENABLE_DAILY:
        for log_key in list(log["open_entries"].keys()):
            info = log["open_entries"].get(log_key)
            if not info or info.get("strategy") != "DailyMACD": continue
            sym = info.get("symbol") or log_key_sym(log_key)
            if not sym: continue
            is_paper = info.get("paper", False)
            if not is_paper and sym not in open_positions: continue
            pos_side = info.get("side", "LONG"); entry_tf = info.get("tf", "1d")
            try:
                df_tf, bpy = fetch_daily_macd_tf(sym, entry_tf)
                if df_tf is None or len(df_tf) < DAILY_MACD_PARAMS["min_bars"]: continue
                trig = False; reason = ""
                if DAILY_EXIT_MODE == "macd":
                    sigs = detect_daily_macd_signals(df_tf, entry_tf, bpy)
                    if any(s["action"] == f"EXIT_{pos_side}" for s in sigs):
                        trig = True; reason = f"MACD-EXIT ({entry_tf})"
                elif DAILY_EXIT_MODE == "kijun":
                    if check_kijun_exit(df_tf, pos_side, DAILY_MACD_PARAMS["kijun_p"]):
                        trig = True; reason = f"KIJUN-EXIT ({entry_tf})"
                if not trig: continue
                if not is_paper and not close_position(open_positions[sym]): continue
                price = float(df_tf["close"].iloc[-1]); ep = info["entry"]
                pnl = ((price - ep) if pos_side == "LONG" else (ep - price)) / ep * 100
                log["open_entries"].pop(log_key)
                rec = {"symbol": sym, "strategy": "DailyMACD", "side": pos_side,
                       "entry": ep, "exit": price, "pnl_pct": round(pnl, 2),
                       "tf": entry_tf, "close_reason": reason, "paper": is_paper}
                log["closed_trades"].append(rec); newly_closed.append(rec)
                if not is_paper and sym in open_positions: open_positions.pop(sym, None)
                tag = "[PAPER] " if is_paper else ""
                print(f"  [CLOSED] {tag}DailyMACD {sym} {pos_side} -> {pnl:+.2f}%")
            except Exception as e:
                print(f"  [DM {sym}] exit error: {e}")

    if ENABLE_CBT:
        for log_key in list(log["open_entries"].keys()):
            info = log["open_entries"].get(log_key)
            if not info or info.get("strategy") != "CBT": continue
            sym = info.get("symbol") or log_key_sym(log_key)
            if not sym or sym not in open_positions: continue
            try:
                k30 = get_klines_fallback(sym, ["30", "30m"])
                if not k30 or len(k30) < 50: continue
                df30 = klines_to_df(k30[-200:])
                current_close = float(df30["close"].iloc[-1])
                current_high  = float(df30["high"].iloc[-1])
                current_low   = float(df30["low"].iloc[-1])

                entry = info["entry"]
                risk  = info.get("risk") or abs(info["sl"] - entry)
                if risk <= 0: continue
                current_sl = info.get("current_sl", info["sl"])
                tp = info["tp"]
                entry_ts = info.get("entry_bar_ts") or info.get("_bar_ts")
                bars_held = ((time.time() - entry_ts) / (30*60)) if entry_ts else 0

                exit_reason = None
                if bars_held >= CBT_MAX_BARS:
                    exit_reason = f"CBT MaxBars ({CBT_MAX_BARS})"
                elif current_high >= current_sl:
                    exit_reason = f"CBT SL@{fmt_price(current_sl)}"
                elif current_low <= tp:
                    exit_reason = f"CBT TP@{fmt_price(tp)}"

                if exit_reason is None:
                    r_now = (entry - current_low) / risk
                    atr_now = float(compute_atr(df30)[-1]) if len(df30) > 20 else risk / CBT_SL_MULT
                    be_done = info.get("be_done", False)
                    trail_on = info.get("trail_on", False)
                    if not be_done and r_now >= CBT_BE_AT_R:
                        current_sl = min(current_sl, entry); be_done = True
                    if r_now >= CBT_TRAIL_R:
                        trail_on = True
                        trail_sl = current_close + CBT_TRAIL_ATR * atr_now
                        current_sl = min(current_sl, trail_sl)
                    if (current_sl != info.get("current_sl", info["sl"])
                            or be_done != info.get("be_done", False)):
                        info["current_sl"] = current_sl
                        info["be_done"] = be_done
                        info["trail_on"] = trail_on
                        log["open_entries"][log_key] = info
                    continue

                if not close_position(open_positions[sym]): continue
                pnl = ((entry - current_close) / entry) * 100
                log["open_entries"].pop(log_key)
                rec = {"symbol": sym, "strategy": "CBT", "side": "SHORT",
                       "entry": entry, "exit": current_close, "pnl_pct": round(pnl, 2),
                       "tf": "30m", "close_reason": exit_reason, "paper": False}
                log["closed_trades"].append(rec); newly_closed.append(rec)
                open_positions.pop(sym, None)
                print(f"  [CLOSED] CBT {sym} SHORT -> {pnl:+.2f}% ({exit_reason})")
            except Exception as e:
                print(f"  [CBT {sym}] exit error: {e}")

    # ============ SL/TP & CLEANUP ============
    for log_key in list(log["open_entries"].keys()):
        info = log["open_entries"].get(log_key)
        if not info: continue
        sym = info.get("symbol") or log_key_sym(log_key)
        if not sym: continue
        is_paper = info.get("paper", False)
        strategy = info.get("strategy", "Unknown")
        side = info.get("side", "LONG")
        ep = info.get("entry", 0); sl = info.get("sl", 0); tp = info.get("tp", 0)
        if ep <= 0: continue

        if not is_paper and sym not in open_positions:
            print(f"  [{sym}] LIVE position not on exchange — cleaning log")
            price = _get_price_safe(sym, "60", fallback=ep)
            pnl = ((price - ep) if side == "LONG" else (ep - price)) / ep * 100
            log["open_entries"].pop(log_key)
            rec = {"symbol": sym, "strategy": strategy, "side": side,
                   "entry": ep, "exit": price, "pnl_pct": round(pnl, 2),
                   "tf": info.get("tf", "1h"),
                   "close_reason": "SL/TP (exchange)", "paper": False}
            log["closed_trades"].append(rec); newly_closed.append(rec)
            print(f"  [CLOSED-AUTO] {strategy} {sym} {side} -> {pnl:+.2f}%")
            continue

        if is_paper and sl > 0 and tp > 0:
            try:
                if strategy == "DailyMACD":
                    entry_tf = info.get("tf", "1d")
                    df_tf, _ = fetch_daily_macd_tf(sym, entry_tf)
                    if df_tf is None: continue
                    price = float(df_tf["close"].iloc[-1])
                else:
                    price = _get_price_safe(sym, "60", fallback=None)
                    if price is None: continue
                hit = False; reason = ""
                if side == "LONG":
                    if price <= sl: hit = True; reason = "SL (paper)"
                    elif price >= tp: hit = True; reason = "TP (paper)"
                else:
                    if price >= sl: hit = True; reason = "SL (paper)"
                    elif price <= tp: hit = True; reason = "TP (paper)"
                if not hit: continue
                pnl = ((price - ep) if side == "LONG" else (ep - price)) / ep * 100
                log["open_entries"].pop(log_key)
                rec = {"symbol": sym, "strategy": strategy, "side": side,
                       "entry": ep, "exit": price, "pnl_pct": round(pnl, 2),
                       "tf": info.get("tf", "1d"), "close_reason": reason, "paper": True}
                log["closed_trades"].append(rec); newly_closed.append(rec)
                print(f"  [CLOSED-AUTO-PAPER] {strategy} {sym} {side} -> {pnl:+.2f}% ({reason})")
            except Exception as e:
                print(f"  [{strategy}-paper {sym}] SL/TP check error: {e}")

    # ============ NEW ENTRIES ============
    if kill_on:
        print("\n  ⛔ Entries skipped (kill switch active). Use RESET_KILL=1")
    else:
        newly_opened_symbols = set()

        # -------- Donchian --------
        if ENABLE_DONCHIAN:
            for idx, sym in enumerate(DONCHIAN_SYMBOLS, 1):
                if sym in open_positions or sym in newly_opened_symbols: continue
                if count_strategy_total("Donchian") + len([t for t in new_trades if t[0]=="Donchian"]) >= MAX_DONCHIAN_POSITIONS:
                    print(f"  Max Donchian ({MAX_DONCHIAN_POSITIONS}) reached"); break
                if len(open_positions) + len(new_trades) >= MAX_TOTAL_POSITIONS:
                    print(f"  Max total ({MAX_TOTAL_POSITIONS}) reached"); break

                diag["symbols_scanned"] += 1
                if VERBOSE: print(f"  [DN {idx}/{len(DONCHIAN_SYMBOLS)}] {sym}...", flush=True)
                current_price = _get_price_safe(sym, "60", fallback=None)
                if current_price is None:
                    diag["klines_failed"] += 1
                    if VERBOSE: print(f"    ✗ no 1h price")
                    continue

                tf_data = fetch_timeframes(sym, DONCHIAN_PARAMS["min_bars"] + 50, bars=500)
                all_entries = []
                for tf_label, _, tf_prio, bpy_tf in SCAN_TIMEFRAMES:
                    df_tf = tf_data.get(tf_label)
                    if df_tf is None:
                        if VERBOSE: print(f"    ✗ {tf_label}: not enough bars")
                        continue
                    try:
                        for s in detect_donchian_signals(df_tf, tf_label, bpy_tf):
                            if s["action"] in ("ENTRY_LONG", "ENTRY_SHORT"):
                                s["_tf_prio"] = tf_prio; all_entries.append(s)
                    except Exception as e:
                        if VERBOSE: print(f"    {tf_label} error: {e}")

                if not all_entries:
                    if VERBOSE: print(f"    — no Donchian signals")
                    continue
                if len(set(s["side"] for s in all_entries)) > 1:
                    if VERBOSE: print(f"    ✗ conflicting sides")
                    continue
                all_entries.sort(key=lambda x: x["_tf_prio"], reverse=True)
                sig = all_entries[0]
                diag["signals"]["Donchian"] = diag["signals"].get("Donchian", 0) + 1

                ok, reason = is_signal_ok(sig, FILTERS_DONCHIAN)
                if not ok:
                    diag["skipped_gate"] += 1
                    print(f"  [{sym}] DN SKIP gate: {reason}"); continue
                if sig["side"] == "LONG" and not (sig["sl"] < current_price < sig["tp"]):
                    diag["skipped_price"] += 1
                    if VERBOSE: print(f"    ✗ price outside SL/TP")
                    continue
                if sig["side"] == "SHORT" and not (sig["tp"] < current_price < sig["sl"]):
                    diag["skipped_price"] += 1
                    continue
                min_dist = current_price * 0.003
                if (abs(current_price - sig["sl"]) < min_dist
                        or abs(current_price - sig["tp"]) < min_dist):
                    diag["skipped_price"] += 1
                    continue

                vol30 = sig.get("_vol30", 0.30)
                if pd.isna(vol30): vol30 = 0.30
                size_frac = compute_dynamic_size(vol30, DONCHIAN_PROFILE)
                notional = min(balance_usd * size_frac, balance_usd * MAX_NOTIONAL_PCT)
                margin = max(notional / LEVERAGE, 1.0)
                if margin < MIN_MARGIN_USD:
                    diag["skipped_margin"] += 1
                    print(f"  [{sym}] DN SKIP margin ${margin:.2f} < ${MIN_MARGIN_USD}")
                    continue

                coid = f"Donchian-{sig['side']}-{sym}-{int(now.timestamp())}"
                sl_r = round_price(sig["sl"], sig["entry"]); tp_r = round_price(sig["tp"], sig["entry"])
                side_lc = "long" if sig["side"] == "LONG" else "short"

                if DRY_RUN:
                    print(f"  [DRY] Donchian {sig['side']} {sym} @ {fmt_price(sig['entry'])} | "
                          f"margin ${margin:.2f}")
                    new_trades.append(("Donchian", sym, sig, notional, margin))
                    diag["trades"] += 1
                    continue

                try: resp = open_position(sym, side_lc, margin, LEVERAGE, sl_r, tp_r, coid)
                except Exception as e:
                    print(f"  [DN {sym}] network error: {e}"); continue
                if resp is None or resp.get("idempotent"): continue

                log["open_entries"][f"Donchian:{sym}"] = {
                    "symbol": sym, "strategy": "Donchian", "side": sig["side"],
                    "entry": sig["entry"], "sl": sig["sl"], "tp": sig["tp"],
                    "margin": margin, "tf": sig["_tf_label"],
                    "_bar_ts": sig.get("_bar_ts"),
                    "profile": DONCHIAN_PROFILE_KEY, "paper": False,
                }
                new_trades.append(("Donchian", sym, sig, notional, margin))
                diag["trades"] += 1
                newly_opened_symbols.add(sym)
                print(f"  🟢 [Donchian {sig['_tf_label']}] {sig['side']} {sym} "
                      f"@ {fmt_price(sig['entry'])} | {size_frac*100:.1f}% | {LEVERAGE}×")

        # -------- DailyMACD --------
        if ENABLE_DAILY:
            for idx, sym in enumerate(DAILY_SYMBOLS, 1):
                if not DAILY_PAPER_MODE and (sym in open_positions or sym in newly_opened_symbols): continue
                if DAILY_PAPER_MODE and any(k.startswith(f"PAPER:{sym}:") for k in log["open_entries"]): continue
                if count_strategy_total("DailyMACD") + len([t for t in new_trades if t[0]=="DailyMACD"]) >= MAX_DAILY_POSITIONS:
                    print(f"  Max DailyMACD ({MAX_DAILY_POSITIONS}) reached"); break
                if len(open_positions) + len(new_trades) >= MAX_TOTAL_POSITIONS:
                    print(f"  Max total ({MAX_TOTAL_POSITIONS}) reached"); break

                if VERBOSE: print(f"  [DM {idx}/{len(DAILY_SYMBOLS)}] {sym}...", flush=True)
                sigs_by_side = {"LONG": [], "SHORT": []}
                for tf_label, iv, tf_prio, bpy in DAILY_MACD_TIMEFRAMES:
                    try:
                        df_tf, _bpy = fetch_daily_macd_tf(sym, tf_label)
                        if df_tf is None:
                            if VERBOSE: print(f"    ✗ {tf_label}: no data")
                            continue
                        for s in detect_daily_macd_signals(df_tf, tf_label, bpy):
                            if s["action"].startswith("ENTRY_"):
                                s["_tf_prio"] = tf_prio; s["_tf_label"] = tf_label; s["_bpy"] = bpy
                                sigs_by_side[s["side"]].append(s)
                    except Exception as e:
                        if VERBOSE: print(f"    {tf_label} error: {e}")

                for side in ("LONG", "SHORT"):
                    side_sigs = sigs_by_side[side]
                    if not side_sigs: continue
                    side_sigs.sort(key=lambda x: x["_tf_prio"], reverse=True)
                    sig = side_sigs[0]
                    tf_label = sig["_tf_label"]
                    log_key = f"PAPER:{sym}:{side}" if DAILY_PAPER_MODE else f"{sym}:{side}"
                    if log_key in log["open_entries"]: continue
                    if not DAILY_PAPER_MODE and has_position(open_positions, sym, side): continue
                    if not DAILY_PAPER_MODE and not can_open_more(open_positions, new_trades): break

                    cd_key = f"DailyMACD:{sym}:{side}"
                    bar_ts = sig.get("_bar_ts")
                    if bar_ts and hours_since_last_entry(log, cd_key, bar_ts) < DAILY_COOLDOWN_HOURS:
                        if VERBOSE: print(f"    ✗ cooldown {DAILY_COOLDOWN_HOURS}h")
                        continue
                    ok, reason = is_signal_ok(sig, FILTERS_DAILY)
                    if not ok:
                        diag["skipped_gate"] += 1
                        print(f"  [{sym}] DM SKIP gate: {reason}"); continue

                    try:
                        df_tf_check, _ = fetch_daily_macd_tf(sym, tf_label)
                        if df_tf_check is None: continue
                        cur_price = float(df_tf_check["close"].iloc[-1])
                    except Exception: continue
                    if side == "LONG" and not (sig["sl"] < cur_price < sig["tp"]): continue
                    if side == "SHORT" and not (sig["tp"] < cur_price < sig["sl"]): continue

                    vol30 = sig.get("_vol30", 0.30)
                    if pd.isna(vol30): vol30 = 0.30
                    size_frac = compute_dynamic_size(vol30, DAILY_PROFILE)
                    notional = min(balance_usd * size_frac, balance_usd * MAX_NOTIONAL_PCT)
                    margin = max(notional / LEVERAGE, 1.0)
                    if margin < MIN_MARGIN_USD:
                        diag["skipped_margin"] += 1
                        print(f"  [{sym}] DM SKIP margin ${margin:.2f} < ${MIN_MARGIN_USD}")
                        continue

                    diag["signals"]["DailyMACD"] = diag["signals"].get("DailyMACD", 0) + 1

                    if DAILY_PAPER_MODE:
                        sig["_paper"] = True; sig["_tf_label"] = tf_label
                        log["open_entries"][log_key] = {
                            "symbol": sym, "strategy": "DailyMACD", "side": side,
                            "entry": sig["entry"], "sl": sig["sl"], "tp": sig["tp"],
                            "margin": margin, "tf": tf_label, "paper": True,
                        }
                        if bar_ts: record_entry_bar(log, cd_key, bar_ts)
                        new_trades.append(("DailyMACD", sym, sig, notional, margin))
                        diag["trades"] += 1
                        print(f"  📝 [PAPER {tf_label}] DailyMACD {side} {sym} "
                              f"@ {fmt_price(sig['entry'])} | {size_frac*100:.1f}%")
                    else:
                        if DRY_RUN:
                            print(f"  [DRY] DailyMACD {side} {sym} @ {fmt_price(sig['entry'])}")
                            new_trades.append(("DailyMACD", sym, sig, notional, margin))
                            diag["trades"] += 1
                            continue
                        coid = f"DailyMACD-{tf_label}-{side}-{sym}-{int(now.timestamp())}"
                        sl_r = round_price(sig["sl"], sig["entry"])
                        tp_r = round_price(sig["tp"], sig["entry"])
                        try: resp = open_position(sym, side.lower(), margin, LEVERAGE, sl_r, tp_r, coid)
                        except Exception as e:
                            print(f"  [DM {sym}] open error: {e}"); continue
                        if resp is None or resp.get("idempotent"): continue
                        sig["_paper"] = False; sig["_tf_label"] = tf_label
                        log["open_entries"][log_key] = {
                            "symbol": sym, "strategy": "DailyMACD", "side": side,
                            "entry": sig["entry"], "sl": sig["sl"], "tp": sig["tp"],
                            "margin": margin, "tf": tf_label, "paper": False,
                        }
                        if bar_ts: record_entry_bar(log, cd_key, bar_ts)
                        new_trades.append(("DailyMACD", sym, sig, notional, margin))
                        diag["trades"] += 1
                        newly_opened_symbols.add(sym)
                        print(f"  🟢 [LIVE {tf_label}] DailyMACD {side} {sym} "
                              f"@ {fmt_price(sig['entry'])} | {size_frac*100:.1f}%")

        # -------- CBT --------
        if ENABLE_CBT:
            for idx, sym in enumerate(CBT_SYMBOLS, 1):
                if sym in open_positions or sym in newly_opened_symbols: continue
                if f"CBT:{sym}" in log["open_entries"]: continue
                if count_strategy_total("CBT") + len([t for t in new_trades if t[0]=="CBT"]) >= MAX_CBT_POSITIONS:
                    print(f"  Max CBT ({MAX_CBT_POSITIONS}) reached"); break
                if len(open_positions) + len(new_trades) >= MAX_TOTAL_POSITIONS:
                    print(f"  Max total ({MAX_TOTAL_POSITIONS}) reached"); break

                if VERBOSE: print(f"  [CBT {idx}/{len(CBT_SYMBOLS)}] {sym}...", flush=True)
                k30 = get_klines_fallback(sym, ["30", "30m"])
                if not k30 or len(k30) < CBT_MIN_BARS:
                    if VERBOSE:
                        n = len(k30) if k30 else 0
                        print(f"    ✗ only {n}/{CBT_MIN_BARS} 30m bars")
                    continue
                df30 = klines_to_df(k30[-500:])

                try: sigs = detect_c_be_trail_signals(df30)
                except Exception as e:
                    print(f"  [CBT {sym}] detect error: {e}"); continue
                if not sigs:
                    if VERBOSE: print(f"    — no CBT signal")
                    continue
                sig = sigs[0]
                diag["signals"]["CBT"] = diag["signals"].get("CBT", 0) + 1

                cd_key = f"CBT:{sym}"
                bar_ts = sig.get("_bar_ts")
                if bar_ts and hours_since_last_entry(log, cd_key, bar_ts) < CBT_COOLDOWN_HOURS:
                    if VERBOSE: print(f"    ✗ CBT cooldown {CBT_COOLDOWN_HOURS}h")
                    continue

                ok, reason = is_signal_ok(sig, FILTERS_CBT)
                if not ok:
                    diag["skipped_gate"] += 1
                    print(f"  [{sym}] CBT SKIP gate: {reason}"); continue

                cur_price = _get_price_safe(sym, "30", fallback=None)
                if cur_price is None: continue
                if not (sig["tp"] < cur_price < sig["sl"]):
                    diag["skipped_price"] += 1
                    if VERBOSE: print(f"    ✗ price outside SL/TP")
                    continue

                vol30 = sig.get("_vol30", 0.30)
                if pd.isna(vol30): vol30 = 0.30
                size_frac = compute_dynamic_size(vol30, CBT_PROFILE)
                notional = min(balance_usd * size_frac, balance_usd * MAX_NOTIONAL_PCT)
                margin = max(notional / LEVERAGE, 1.0)
                if margin < MIN_MARGIN_USD:
                    diag["skipped_margin"] += 1
                    print(f"  [{sym}] CBT SKIP margin ${margin:.2f} < ${MIN_MARGIN_USD}")
                    continue

                coid = f"CBT-SHORT-{sym}-{int(now.timestamp())}"
                sl_r = round_price(sig["sl"], sig["entry"])
                tp_r = round_price(sig["tp"], sig["entry"])

                if DRY_RUN:
                    print(f"  [DRY] CBT SHORT {sym} @ {fmt_price(sig['entry'])} | margin ${margin:.2f}")
                    new_trades.append(("CBT", sym, sig, notional, margin))
                    diag["trades"] += 1
                    continue

                try: resp = open_position(sym, "short", margin, LEVERAGE, sl_r, tp_r, coid)
                except Exception as e:
                    print(f"  [CBT {sym}] network error: {e}"); continue
                if resp is None or resp.get("idempotent"): continue

                log["open_entries"][f"CBT:{sym}"] = {
                    "symbol": sym, "strategy": "CBT", "side": "SHORT",
                    "entry": sig["entry"], "sl": sig["sl"], "current_sl": sig["sl"],
                    "tp": sig["tp"], "margin": margin, "tf": "30m",
                    "entry_bar_ts": bar_ts, "_bar_ts": bar_ts,
                    "atr": sig.get("_atr", (sig["sl"] - sig["entry"]) / CBT_SL_MULT),
                    "risk": sig.get("_risk", sig["sl"] - sig["entry"]),
                    "be_done": False, "trail_on": False, "paper": False,
                }
                if bar_ts: record_entry_bar(log, cd_key, bar_ts)
                new_trades.append(("CBT", sym, sig, notional, margin))
                diag["trades"] += 1
                newly_opened_symbols.add(sym)
                print(f"  🔴 [CBT 30m] SHORT {sym} @ {fmt_price(sig['entry'])} "
                      f"| {size_frac*100:.1f}% | {LEVERAGE}×")

    save_log(log)

    for strat in ["DailyMACD", "Donchian", "CBT"]:
        n_open = len([t for t in new_trades if t[0] == strat])
        n_closed = len([t for t in newly_closed if t.get("strategy") == strat])
        if n_open or n_closed:
            log_ledger(today_str, strat, n_open, n_closed, balance_usd)

    # ============ TELEGRAM ============
    msg_blocks = []
    if kill_on: msg_blocks.append(f"🛑 <b>KILL SWITCH: entries paused</b>\n{kill_reason}")
    if health_warn: msg_blocks.append(f"{health_warn}")

    if new_trades:
        dn_new = [t for t in new_trades if t[0] == "Donchian"]
        dm_new = [t for t in new_trades if t[0] == "DailyMACD"]
        cb_new = [t for t in new_trades if t[0] == "CBT"]
        if dn_new:
            msg_blocks.append(f"<b>🔔 Donchian — {now.strftime('%H:%M')} UTC</b>\n"
                              f"<i>{DONCHIAN_PROFILE['name']} | {LEVERAGE}× FUTURES</i>")
            for _, sym, sig, notional, margin in dn_new:
                msg_blocks.append(format_donchian_trade(sym, sig, notional, margin,
                                                        balance_usd, DONCHIAN_PROFILE_KEY, DONCHIAN_PROFILE))
        if dm_new:
            tag = "📝 PAPER " if DAILY_PAPER_MODE else ""
            msg_blocks.append(f"<b>{tag}🔔 DailyMACD — {now.strftime('%H:%M')} UTC</b>\n"
                              f"<i>{DAILY_PROFILE['name']} | Exit: {DAILY_EXIT_MODE.upper()}</i>")
            for _, sym, sig, notional, margin in dm_new:
                msg_blocks.append(format_daily_trade(sym, sig, notional, margin,
                                                     balance_usd, DAILY_PROFILE_KEY, DAILY_PROFILE))
        if cb_new:
            msg_blocks.append(f"<b>🔔 C_BE_trail — {now.strftime('%H:%M')} UTC</b>\n"
                              f"<i>{CBT_PROFILE['name']} | 30m SHORT | {LEVERAGE}× FUTURES</i>")
            for _, sym, sig, notional, margin in cb_new:
                msg_blocks.append(format_cbt_trade(sym, sig, notional, margin,
                                                   balance_usd, CBT_PROFILE_KEY, CBT_PROFILE))

    if newly_closed:
        msg_blocks.append(f"<b>🔒 بسته‌شده — {now.strftime('%H:%M')} UTC</b>")
        for t in newly_closed:
            msg_blocks.append(format_closed_trade(t))

    if new_trades or newly_closed:
        msg_blocks.append(format_strategy_summary(log, "DailyMACD", "📊 DailyMACD Stats"))
        msg_blocks.append(format_strategy_summary(log, "Donchian",  "📊 Donchian Stats"))
        msg_blocks.append(format_strategy_summary(log, "CBT",       "📊 C_BE_trail Stats"))

    if msg_blocks and TG_CHAT:
        if send_telegram("\n\n".join(msg_blocks)):
            print("  [telegram] sent ✓")

    # ============ DIAGNOSTICS ============
    print(f"\n📊 Scan summary:")
    print(f"  Scanned: {diag['symbols_scanned']} | Klines fail: {diag['klines_failed']}")
    if diag["signals"]: print(f"  Signals: {diag['signals']}")
    print(f"  Skipped → gate:{diag['skipped_gate']} | "
          f"price:{diag['skipped_price']} | margin:{diag['skipped_margin']}")
    print(f"  Trades opened: {diag['trades']}" + (" (DRY_RUN)" if DRY_RUN else ""))
    if not diag["signals"] and diag["symbols_scanned"] > 0:
        print(f"\n  ⚠️  No signals found. Try:")
        print(f"     • VERBOSE=1 DRY_RUN=1 python3 scanner.py")
        print(f"     • Market quiet — wait for trend/volatility")
        print(f"     • Check DAILY_PAPER_MODE (currently "
              f"{'ON' if DAILY_PAPER_MODE else 'OFF'})")
    print(f"\nResult: {len(new_trades)} new | {len(newly_closed)} closed"
          f"{' | KILL SWITCH ACTIVE' if kill_on else ''}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    if INTERVAL_MIN <= 0:
        sys.exit(run_scan())
    interval_sec = INTERVAL_MIN * 60
    print(f"Loop mode: every {INTERVAL_MIN} min (Ctrl+C to stop)")
    while True:
        try:
            run_scan()
        except KeyboardInterrupt:
            print("\nStopped by user"); sys.exit(0)
        except Exception as e:
            print(f"WARNING: Scan crashed: {e}")
        print(f"Sleeping {INTERVAL_MIN} min...")
        try:
            time.sleep(interval_sec)
        except KeyboardInterrupt:
            print("\nStopped by user"); sys.exit(0)
