#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MarginPad Dual-Strategy Scanner — Donchian + DailyMACD
======================================================
  1. Donchian    (5m/15m/30m/1h/4h/1D)    — L/S
  2. DailyMACD   (5m/15m/30m/1h/4h/1d)    — L/S

✅ HEDGE MODE: اگر روی یک نماد هم سیگنال LONG و هم SHORT بیاید،
   هر دو معامله باز می‌شوند (مشروط به پشتیبانی صرافی).
"""

import os
import sys
import json
import time
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

# ============================================================
# CONFIG
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

# ✅ ۲۲ نماد
SYMBOLS = [
    "ADA", "AVAX", "LINK", "LTC",
    "ENA", "ATOM", "NEAR", "ARB", "OP", "PEPE", "SHIB", "SUI",
    "APT", "INJ", "TIA", "SEI", "WIF", "LDO", "UNI", "AAVE", "FIL", "RENDER",
]
DONCHIAN_SYMBOLS = sorted(set(SYMBOLS + ["BTC", "ETH", "SOL"]))
DAILY_SYMBOLS    = SYMBOLS

# --- Donchian ---
DONCHIAN_PARAMS = {
    "entry_n": 8, "exit_n": 2,
    "vol_mult": 1.5, "atr_mult": 3.0, "ema_period": 150,
    "min_bars": 200, "tp_rr": 5.0,
}

DONCHIAN_TIMEFRAMES = [
    ("5m",  ["5", "5m", "M5"],                    1),
    ("15m", ["15", "15m", "M15"],                 2),
    ("30m", ["30", "30m", "M30"],                 3),
    ("1h",  ["60", "60m", "H1"],                  4),
    ("4h",  ["240", "4h", "H4"],                  5),
    ("1d",  ["1440", "1d", "D", "D1", "day"],     6),
]

# --- DailyMACD ---
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

DAILY_MACD_TIMEFRAMES = [
    ("5m",  ["5", "5m", "M5"],                    1, 365 * 24 * 12),
    ("15m", ["15", "15m", "M15"],                 2, 365 * 24 * 4),
    ("30m", ["30", "30m", "M30"],                 3, 365 * 24 * 2),
    ("1h",  ["60", "60m", "H1"],                  4, 365 * 24),
    ("4h",  ["240", "4h", "H4"],                  5, 365 * 6),
    ("1d",  ["1440", "1d", "D", "D1", "day"],     6, 365),
]

DAILY_PAPER_MODE     = os.environ.get("DAILY_PAPER_MODE", "false").lower() == "true"
DAILY_EXIT_MODE      = os.environ.get("DAILY_EXIT_MODE", "macd").lower()
DAILY_COOLDOWN_HOURS = 30

# --- Risk / Sizing ---
RISK_PCT_PER_TRADE = 0.00125
LEVERAGE = 10
MAX_NOTIONAL_PCT = 0.15
MAX_TOTAL_POSITIONS = 8
MAX_DAILY_PAPER = 5
LOG_FILE = Path("marginpad_trades_log.json")

# --- Global quality gates ---
MIN_SL_PCT = 0.5
MIN_TP_PCT = 0.3
MIN_RR = 0.5

# --- Kill switch (DailyMACD live only) ---
KILL_CONSEC_LOSSES   = 3
KILL_CONSEC_LOSS_SUM = -15.0
KILL_MAX_DRAWDOWN    = -20.0

TF_MAP = {"Donchian": "1h", "DailyMACD": "1d"}


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
        return False, f"SL too tight ({sig['sl_pct']:.2f}% < {MIN_SL_PCT}%)"
    if sig["tp_pct"] < MIN_TP_PCT:
        return False, f"TP too tight ({sig['tp_pct']:.2f}% < {MIN_TP_PCT}%)"
    rr = sig["tp_pct"] / sig["sl_pct"]
    if rr < MIN_RR:
        return False, f"R/R too low ({rr:.2f} < {MIN_RR})"
    return True, ""


def hours_since_last_entry(log, log_key, current_bar_ts):
    last = log.get("last_entry_bar", {}).get(log_key)
    if last is None: return 999999
    return (current_bar_ts - last) / 3600


def record_entry_bar(log, log_key, bar_ts):
    log.setdefault("last_entry_bar", {})[log_key] = int(bar_ts)


# ---- Hedge-aware position helpers ----
def has_position(open_positions, sym, side):
    side_lc = side.lower()
    if (sym, side_lc) in open_positions: return True
    if (sym, None) in open_positions: return True
    if sym in open_positions: return True
    return False


def get_position(open_positions, sym, side):
    side_lc = side.lower()
    if (sym, side_lc) in open_positions: return open_positions[(sym, side_lc)]
    if (sym, None) in open_positions: return open_positions[(sym, None)]
    if sym in open_positions: return open_positions[sym]
    return None


def remove_position(open_positions, sym, side):
    side_lc = side.lower()
    open_positions.pop((sym, side_lc), None)
    open_positions.pop((sym, None), None)
    open_positions.pop(sym, None)


def extract_sym_from_key(key, info):
    sym = info.get("symbol")
    if sym: return sym
    parts = key.split(":")
    if parts[0] in ("DailyMACD", "PAPER") and len(parts) >= 2:
        return parts[1]
    return parts[0]


# ============================================================
# TELEGRAM
# ============================================================
def send_telegram(text: str) -> bool:
    if not TG_TOKEN or not TG_CHAT: return False
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


def format_new_trade(sym, sig, margin, balance):
    emoji = "🟢" if sig["side"] == "LONG" else "🔴"
    tf = sig.get("_tf_label") or TF_MAP.get(sig["strategy"], "1h")
    rr = sig["tp_pct"] / sig["sl_pct"] if sig["sl_pct"] > 0 else 0
    paper = "📝 PAPER | " if sig.get("_paper") else ""
    return (
        f"{paper}{emoji} <b>معامله جدید: {sym} {sig['side']}</b>\n"
        f"🧠 استراتژی: <b>{sig['strategy']}</b>\n"
        f"📊 تایم‌فریم: <b>{tf}</b>\n\n"
        f"📍 قیمت ورود: <code>{fmt_price(sig['entry'])}</code>\n"
        f"🛑 حد ضرر: <code>{fmt_price(sig['sl'])}</code> (-{sig['sl_pct']:.2f}%)\n"
        f"🎯 حد سود: <code>{fmt_price(sig['tp'])}</code> (+{sig['tp_pct']:.2f}%)\n"
        f"📊 R/R: <b>{rr:.2f}</b>\n"
        f"💰 مارجین: <b>${margin:.2f}</b> (اهرم {LEVERAGE}×)\n"
        f"🏦 موجودی کل: ${balance:,.2f}\n"
    )


def format_closed_trade(trade):
    emoji = "✅" if trade["pnl_pct"] > 0 else "❌"
    tf = trade.get("tf", TF_MAP.get(trade["strategy"], "1h"))
    reason = trade.get("close_reason", "")
    reason_line = f"\n📋 دلیل: <b>{reason}</b>" if reason else ""
    paper = "📝 " if trade.get("paper") else ""
    return (
        f"{paper}{emoji} <b>معامله بسته شد: {trade['symbol']} {trade['side']}</b>\n"
        f"🧠 استراتژی: <b>{trade['strategy']}</b>\n"
        f"📊 تایم‌فریم: <b>{tf}</b>{reason_line}\n\n"
        f"📍 قیمت ورود: <code>{fmt_price(trade['entry'])}</code>\n"
        f"🏁 قیمت خروج: <code>{fmt_price(trade['exit'])}</code>\n"
        f"📊 سود/ضرر: <b>{trade['pnl_pct']:+.2f}%</b>\n"
    )


def format_strategy_summary(log_data):
    closed = log_data.get("closed_trades", [])
    if not closed: return "<i>هنوز معامله بسته‌شده‌ای ثبت نشده است.</i>"
    df = pd.DataFrame(closed)
    report = "📈 <b>گزارش بازدهی استراتژی‌ها:</b>\n\n"

    for strat in ["Donchian", "DailyMACD"]:
        strat_df = df[df["strategy"] == strat]
        if strat_df.empty:
            report += f"<b>🔹 {strat}</b>: هنوز معامله‌ای ندارد.\n\n"
            continue
        total = len(strat_df)
        wins = len(strat_df[strat_df["pnl_pct"] > 0])
        losses = total - wins
        wr = (wins / total) * 100
        total_pnl = strat_df["pnl_pct"].sum()
        avg_pnl = total_pnl / total
        paper_n = int(strat_df["paper"].sum()) if "paper" in strat_df.columns else 0

        report += f"<b>🔹 {strat}</b>"
        if paper_n: report += f" (📝{paper_n} paper)"
        report += "\n"
        report += f"• کل معاملات: {total} (✅{wins} / ❌{losses})\n"
        report += f"• وین‌ریت: {wr:.1f}%\n"
        report += f"• سود/زیان کل: <b>{total_pnl:+.2f}%</b>\n"
        report += f"• میانگین هر معامله: {avg_pnl:+.2f}%\n\n"

    return report


# ============================================================
# MARGINPAD API
# ============================================================
def get_klines(symbol, interval):
    r = requests.get(f"{BASE}/klines?symbol={symbol}&interval={interval}", timeout=15)
    r.raise_for_status()
    return r.json()


def get_klines_fallback(symbol, intervals):
    for interval in intervals:
        try:
            k = get_klines(symbol, interval)
            if k and len(k) >= 50: return k
        except Exception:
            continue
    return None


def get_positions():
    r = requests.get(f"{BASE}/positions?status=open", headers=HEADERS, timeout=15)
    r.raise_for_status()
    out = {}
    for p in r.json().get("positions", []):
        sym = p.get("symbol")
        if not sym: continue
        side_raw = (p.get("side") or p.get("direction") or "").strip().lower()
        if side_raw in ("long", "short"):
            out[(sym, side_raw)] = p
        else:
            out[(sym, None)] = p
    return out


def get_balance():
    r = requests.get(f"{BASE}/balance", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def open_position(symbol, side, margin, leverage, sl, tp, coid):
    body = {
        "symbol": symbol, "side": side, "margin_usd": round(margin, 2),
        "leverage": leverage, "sl": sl, "tp": tp, "client_order_id": coid,
    }
    r = requests.post(f"{BASE}/open", headers=H_JSON, json=body, timeout=15)
    if r.status_code not in (200, 201):
        try: err_body = r.json()
        except Exception: err_body = r.text[:800]
        print(f"  [{symbol}] HTTP {r.status_code} — error: {err_body}")
        print(f"  [{symbol}] payload sent: {body}")
        return None
    return r.json()


def close_position(pos_dict):
    if not pos_dict: return None
    pos_id = pos_dict.get("id") or pos_dict.get("position_id") or pos_dict.get("order_id")
    symbol = pos_dict.get("symbol")
    side = (pos_dict.get("side") or pos_dict.get("direction") or "").lower()
    if not pos_id or not symbol: return None
    for key in ("id", "position_id", "order_id"):
        try:
            body = {key: pos_id, "symbol": symbol}
            if side in ("long", "short"): body["side"] = side
            r = requests.post(f"{BASE}/close", headers=H_JSON, json=body, timeout=15)
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
            data = json.loads(LOG_FILE.read_text())
            data.setdefault("open_entries", {})
            data.setdefault("closed_trades", [])
            data.setdefault("last_entry_bar", {})
            return data
        except Exception:
            pass
    return {"open_entries": {}, "closed_trades": [], "last_entry_bar": {}}


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
    if len(df) < kijun_p + 3: return False
    kijun = (df["high"].rolling(kijun_p).max() + df["low"].rolling(kijun_p).min()) / 2
    last_close = float(df["close"].iloc[-1])
    last_kijun = float(kijun.iloc[-1])
    if np.isnan(last_kijun): return False
    if side == "LONG" and last_close < last_kijun: return True
    if side == "SHORT" and last_close > last_kijun: return True
    return False


# ============================================================
# STRATEGY 1: Donchian Breakout
# ============================================================
def detect_donchian_signals(df, tf_label):
    P = DONCHIAN_PARAMS
    n = len(df)
    if n < P["min_bars"] or "volume" not in df.columns: return []

    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()

    don_hi_entry = high.shift(1).rolling(P["entry_n"]).max()
    don_lo_entry = low.shift(1).rolling(P["entry_n"]).min()
    don_hi_exit  = high.shift(1).rolling(P["exit_n"]).max()
    don_lo_exit  = low.shift(1).rolling(P["exit_n"]).min()
    ema = close.ewm(span=P["ema_period"], adjust=False).mean()
    vol_ma = volume.rolling(20).mean()

    for offset in (2, 3):
        i = n - offset
        if i < P["min_bars"] or pd.isna(don_hi_entry.iloc[i]) or pd.isna(don_lo_entry.iloc[i]): continue
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
            sigs.append({"action": "EXIT_LONG", "side": "LONG", "entry": close_v, "_tf_label": tf_label, "_bar_ts": bar_ts})
        if close_v > float(don_hi_exit.iloc[i]):
            sigs.append({"action": "EXIT_SHORT", "side": "SHORT", "entry": close_v, "_tf_label": tf_label, "_bar_ts": bar_ts})

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
                                 "sl_pct": sl_pct, "tp_pct": tp_pct, "_tf_label": tf_label, "_bar_ts": bar_ts})

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
                                 "sl_pct": sl_pct, "tp_pct": tp_pct, "_tf_label": tf_label, "_bar_ts": bar_ts})
        if sigs: return sigs
    return []


# ============================================================
# STRATEGY 2: DailyMACD
# ============================================================
def detect_daily_macd_signals(df, tf_label, bars_per_year):
    P = DAILY_MACD_PARAMS
    n = len(df)
    if n < P["min_bars"]: return []

    close, high, low = df["close"], df["high"], df["low"]
    times = df.index

    def _e(s, nn): return s.ewm(span=nn, adjust=False).mean()
    def _r(c, nn):
        d = c.diff(); g = d.clip(lower=0); l = -d.clip(upper=0)
        ag = g.ewm(alpha=1/nn, adjust=False).mean(); al = l.ewm(alpha=1/nn, adjust=False).mean()
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
    entry = float(close.iloc[i])
    out = []

    if direction == "UP":
        out.append({"action": "EXIT_SHORT", "side": "SHORT", "entry": entry, "_tf_label": tf_label, "_bar_ts": bar_ts})
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
        out.append({"action": "EXIT_LONG", "side": "LONG", "entry": entry, "_tf_label": tf_label, "_bar_ts": bar_ts})
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


def check_daily_kill_switch(log):
    closed = log.get("closed_trades", [])
    if not closed: return False, ""
    dm = [t for t in closed if t.get("strategy") == "DailyMACD" and not t.get("paper")]
    if len(dm) >= KILL_CONSEC_LOSSES:
        last_n = dm[-KILL_CONSEC_LOSSES:]
        if all(t["pnl_pct"] < 0 for t in last_n):
            total = sum(t["pnl_pct"] for t in last_n)
            if total < KILL_CONSEC_LOSS_SUM:
                return True, f"🛑 KILL: {KILL_CONSEC_LOSSES} ضرر متوالی DailyMACD ({total:+.1f}%)"
    if len(dm) >= 10:
        pnls = [t["pnl_pct"] for t in dm]
        eq = np.cumsum(pnls); peak = np.maximum.accumulate(eq); dd = float((eq - peak).min())
        if dd < KILL_MAX_DRAWDOWN: return True, f"🛑 KILL: DailyMACD drawdown {dd:.1f}%"
    return False, ""


# ============================================================
# MAIN
# ============================================================
def run_scan() -> int:
    now = datetime.now(timezone.utc)
    print("=" * 70)
    print(f"Dual-Strategy Scan (Donchian + DailyMACD) — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  DailyMACD: {'PAPER' if DAILY_PAPER_MODE else 'LIVE'} | "
          f"TFs: 5m/15m/30m/1h/4h/1d | exit={DAILY_EXIT_MODE}")
    print(f"  Donchian TFs: 5m/15m/30m/1h/4h/1d")
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
        paper_n = sum(1 for v in log["open_entries"].values() if v.get("paper"))
        print(f"Open (exchange): {len(open_positions)} | Paper: {paper_n}")
        if open_positions: print(f"  {list(open_positions.keys())}")
    except Exception as e:
        print(f"FATAL positions: {e}"); return 1

    newly_closed = []
    new_trades = []

    def count_paper_daily():
        return sum(1 for v in log["open_entries"].values()
                   if v.get("strategy") == "DailyMACD" and v.get("paper"))

    # PHASE 1a: Donchian exits
    for key in list(log["open_entries"].keys()):
        info = log["open_entries"].get(key)
        if not info or info.get("strategy") != "Donchian": continue
        sym = extract_sym_from_key(key, info)
        side = info.get("side", "LONG")
        if not has_position(open_positions, sym, side): continue
        entry_tf = info.get("tf", "1h")
        try:
            intervals = ["60", "60m", "H1"]
            for tf_label, iv, _ in DONCHIAN_TIMEFRAMES:
                if tf_label == entry_tf: intervals = iv; break
            k_raw = get_klines_fallback(sym, intervals)
            if not k_raw or len(k_raw) < DONCHIAN_PARAMS["min_bars"]: continue
            df_tf = klines_to_df(k_raw[-500:])
            sigs = detect_donchian_signals(df_tf, entry_tf)
            exit_action = f"EXIT_{side}"
            if not any(s["action"] == exit_action for s in sigs): continue
            pos = get_position(open_positions, sym, side)
            if not close_position(pos): continue
            price = float(k_raw[-1]["close"])
            ep = info["entry"]
            pnl = ((price - ep) if side == "LONG" else (ep - price)) / ep * 100
            log["open_entries"].pop(key)
            rec = {"symbol": sym, "strategy": "Donchian", "side": side,
                   "entry": ep, "exit": price, "pnl_pct": round(pnl, 2),
                   "tf": entry_tf, "paper": False, "close_reason": f"Donchian-EXIT ({entry_tf})"}
            log["closed_trades"].append(rec); newly_closed.append(rec)
            remove_position(open_positions, sym, side)
            print(f"  [CLOSED-DONCHIAN] {sym} {side} ({entry_tf}) -> {pnl:+.2f}%")
        except Exception as e: print(f"  [DN {sym}] exit error: {e}")

    # PHASE 1b: DailyMACD exits
    for key in list(log["open_entries"].keys()):
        info = log["open_entries"].get(key)
        if not info or info.get("strategy") != "DailyMACD": continue
        sym = extract_sym_from_key(key, info)
        is_paper = info.get("paper", False)
        side = info.get("side", "LONG")
        if not is_paper and not has_position(open_positions, sym, side): continue
        entry_tf = info.get("tf", "1d")
        try:
            df_tf, bpy = fetch_daily_macd_tf(sym, entry_tf)
            if df_tf is None: continue
            trig = False; reason = ""
            if DAILY_EXIT_MODE == "macd":
                sigs = detect_daily_macd_signals(df_tf, entry_tf, bpy)
                if any(s["action"] == f"EXIT_{side}" for s in sigs): trig = True; reason = f"MACD-EXIT ({entry_tf})"
            elif DAILY_EXIT_MODE == "kijun":
                if check_kijun_exit(df_tf, side, DAILY_MACD_PARAMS["kijun_p"]): trig = True; reason = f"KIJUN-EXIT ({entry_tf})"
            if not trig: continue
            if not is_paper:
                pos = get_position(open_positions, sym, side)
                if not close_position(pos): continue
                remove_position(open_positions, sym, side)
            price = float(df_tf["close"].iloc[-1])
            ep = info["entry"]
            pnl = ((price - ep) if side == "LONG" else (ep - price)) / ep * 100
            log["open_entries"].pop(key)
            rec = {"symbol": sym, "strategy": "DailyMACD", "side": side,
                   "entry": ep, "exit": price, "pnl_pct": round(pnl, 2),
                   "tf": entry_tf, "paper": is_paper, "close_reason": reason}
            log["closed_trades"].append(rec); newly_closed.append(rec)
            tag = "[PAPER] " if is_paper else ""
            print(f"  [CLOSED-MACD] {tag}{sym} {side} ({entry_tf}) -> {pnl:+.2f}%")
        except Exception as e: print(f"  [DM {sym}] exit error: {e}")

    # PHASE 2: SL/TP auto-close
    for key in list(log["open_entries"].keys()):
        info = log["open_entries"].get(key)
        if not info: continue
        sym = extract_sym_from_key(key, info)
        is_paper = info.get("paper", False)
        strategy = info.get("strategy", "Unknown")
        side = info.get("side", "LONG")
        ep = info.get("entry", 0); sl = info.get("sl", 0); tp = info.get("tp", 0)
        if ep <= 0: continue

        if is_paper:
            try:
                if strategy == "DailyMACD":
                    entry_tf = info.get("tf", "1d")
                    df_tf, _ = fetch_daily_macd_tf(sym, entry_tf)
                    if df_tf is None: continue
                    price = float(df_tf["close"].iloc[-1])
                else:
                    k = get_klines(sym, "60")
                    if not k: continue
                    price = float(k[-1]["close"])
                hit = False; reason = ""
                if side == "LONG":
                    if price <= sl: hit, reason = True, "SL (paper)"
                    elif price >= tp: hit, reason = True, "TP (paper)"
                else:
                    if price >= sl: hit, reason = True, "SL (paper)"
                    elif price <= tp: hit, reason = True, "TP (paper)"
                if not hit: continue
                pnl = ((price - ep) if side == "LONG" else (ep - price)) / ep * 100
                log["open_entries"].pop(key)
                rec = {"symbol": sym, "strategy": strategy, "side": side,
                       "entry": ep, "exit": price, "pnl_pct": round(pnl, 2),
                       "tf": info.get("tf", "1d"), "paper": True, "close_reason": reason}
                log["closed_trades"].append(rec); newly_closed.append(rec)
                print(f"  [CLOSED-PAPER] {strategy} {sym} {side} -> {pnl:+.2f}% ({reason})")
            except Exception as e: print(f"  [paper {sym}] SL/TP error: {e}")
            continue

        if not has_position(open_positions, sym, side):
            try:
                k = get_klines(sym, "60")
                price = float(k[-1]["close"])
                pnl = ((price - ep) if side == "LONG" else (ep - price)) / ep * 100
                log["open_entries"].pop(key)
                rec = {"symbol": sym, "strategy": strategy, "side": side,
                       "entry": ep, "exit": price, "pnl_pct": round(pnl, 2),
                       "tf": info.get("tf", TF_MAP.get(strategy, "1h")),
                       "paper": False, "close_reason": "SL/TP"}
                log["closed_trades"].append(rec); newly_closed.append(rec)
                print(f"  [CLOSED-AUTO] {strategy} {sym} {side} -> {pnl:+.2f}%")
            except Exception as e: print(f"  [{sym}] close calc error: {e}")

    # PHASE 3a: Donchian Real Trades
    max_notional = balance_usd * MAX_NOTIONAL_PCT
    newly_opened_keys = set()
    def can_open_more(): return len(open_positions) + len(new_trades) < MAX_TOTAL_POSITIONS

    for sym in DONCHIAN_SYMBOLS:
        if not can_open_more(): break
        try:
            k1h = get_klines(sym, "60")
            if not k1h: continue
            df_1h = klines_to_df(k1h[-300:])
            current_price = float(k1h[-1]["close"])
        except Exception: continue

        sigs_by_side = {"LONG": [], "SHORT": []}
        # Donchian
        try:
            best_per_side = {"LONG": (None, -1), "SHORT": (None, -1)}
            for tf_label, iv, tf_prio in DONCHIAN_TIMEFRAMES:
                try:
                    k_raw = get_klines_fallback(sym, iv)
                    if not k_raw or len(k_raw) < DONCHIAN_PARAMS["min_bars"] + 20: continue
                    df_tf = klines_to_df(k_raw[-500:])
                    sigs = detect_donchian_signals(df_tf, tf_label)
                    for s in sigs:
                        if s["action"] in ("ENTRY_LONG", "ENTRY_SHORT"):
                            side = s["side"]
                            if tf_prio > best_per_side[side][1]: best_per_side[side] = (s, tf_prio)
                except Exception: continue
            for side in ("LONG", "SHORT"):
                s, _p = best_per_side[side]
                if s: sigs_by_side[side].append(s)
        except Exception: pass

        for side in ("LONG", "SHORT"):
            side_sigs = sigs_by_side[side]
            if not side_sigs or not can_open_more(): continue
            if f"{sym}:{side}" in newly_opened_keys or has_position(open_positions, sym, side): continue

            sig = side_sigs[0]
            ok, reason = is_signal_ok(sig)
            if not ok: continue
            if side == "LONG":
                if current_price <= sig["sl"] or current_price >= sig["tp"]: continue
            else:
                if current_price >= sig["sl"] or current_price <= sig["tp"]: continue
            if abs(current_price - sig["sl"]) < current_price * 0.003 or abs(current_price - sig["tp"]) < current_price * 0.003: continue

            risk_amount = balance_usd * RISK_PCT_PER_TRADE
            sl_frac = sig["sl_pct"] / 100.0
            if sl_frac <= 0: continue
            notional = min(risk_amount / sl_frac, max_notional)
            margin = max(notional / LEVERAGE, 1.0)
            coid = f"Donchian-{sym}-{side}-{int(now.timestamp())}"
            sl_r = round_price(sig["sl"], sig["entry"]); tp_r = round_price(sig["tp"], sig["entry"])

            try:
                resp = open_position(sym, side.lower(), margin, LEVERAGE, sl_r, tp_r, coid)
            except Exception as e:
                print(f"  [{sym} {side}] network error: {e}"); continue
            if resp is None or resp.get("idempotent"): continue

            log_key = f"{sym}:{side}"
            log["open_entries"][log_key] = {
                "symbol": sym, "strategy": sig["strategy"], "side": side,
                "entry": sig["entry"], "sl": sig["sl"], "tp": sig["tp"],
                "margin": margin, "tf": sig.get("_tf_label"), "paper": False,
            }
            new_trades.append((sym, sig, margin))
            newly_opened_keys.add(f"{sym}:{side}")
            print(f"  🟢 [Donchian {sig.get('_tf_label')}] {side} {sym} @ {fmt_price(sig['entry'])} | Margin: ${margin:.2f}")

    # PHASE 3b: DailyMACD Trades
    kill_on, kill_reason = check_daily_kill_switch(log)
    if not kill_on:
        for sym in DAILY_SYMBOLS:
            if DAILY_PAPER_MODE:
                if (count_paper_daily() + len([t for t in new_trades if t[1].get("strategy") == "DailyMACD" and t[1].get("_paper")])) >= MAX_DAILY_PAPER: break
            else:
                if not can_open_more(): break

            sigs_by_side = {"LONG": [], "SHORT": []}
            for tf_label, iv, tf_prio, bpy in DAILY_MACD_TIMEFRAMES:
                try:
                    df_tf, _bpy = fetch_daily_macd_tf(sym, tf_label)
                    if df_tf is None: continue
                    sigs = detect_daily_macd_signals(df_tf, tf_label, bpy)
                    for s in sigs:
                        if s["action"].startswith("ENTRY_"):
                            s["_tf_prio"] = tf_prio; s["_tf_label"] = tf_label; s["_bpy"] = bpy
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

                ok, reason = is_signal_ok(sig)
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

    # TELEGRAM
    msg_blocks = []
    if kill_on: msg_blocks.append(f"🛑 <b>DailyMACD KILL SWITCH</b>\n{kill_reason}")
    if new_trades:
        msg_blocks.append(f"<b>🔔 معاملات جدید — {now.strftime('%H:%M')} UTC</b>")
        for sym, sig, margin in new_trades:
            msg_blocks.append(format_new_trade(sym, sig, margin, balance_usd))
    if newly_closed:
        msg_blocks.append(f"<b>🔒 معاملات بسته‌شده — {now.strftime('%H:%M')} UTC</b>")
        for t in newly_closed: msg_blocks.append(format_closed_trade(t))
    msg_blocks.append(format_strategy_summary(log))

    if msg_blocks and (new_trades or newly_closed or kill_on):
        send_telegram("\n\n".join(msg_blocks))

    print(f"\nResult: {len(new_trades)} new | {len(newly_closed)} closed")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    INTERVAL_MIN = int(os.environ.get("INTERVAL_MIN", "30"))
    if INTERVAL_MIN <= 0:
        sys.exit(run_scan())
    
    interval_sec = INTERVAL_MIN * 60
    while True:
        try: run_scan()
        except KeyboardInterrupt: sys.exit(0)
        except Exception as e: print(f"⚠️  Scan crashed: {e}")
        try: time.sleep(interval_sec)
        except KeyboardInterrupt: sys.exit(0)
