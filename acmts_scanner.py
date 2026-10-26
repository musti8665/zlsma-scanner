#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MarginPad VolTarget Scanner — LONG + SHORT
================================================
Strategy: MACD(6,19,2) + Pivot(8) + QQE(10,5)
Sizing:   Vol Targeting (3 profiles: conservative/balanced/aggressive)
Direction: LONG + SHORT (mirrored logic)
Timeframes: 5m, 15m, 30m, 1h, 4h
Exit: opposite MACD cross OR SL/TP
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
# CONFIG — سه پروفایل ریسک
# ============================================================
PROFILES = {
    "conservative": {
        "name": "🛡️ محافظه‌کار",
        "target_vol": 0.20,
        "size_min": 0.10, "size_max": 0.60,
    },
    "balanced": {
        "name": "⭐ متعادل",
        "target_vol": 0.30,
        "size_min": 0.10, "size_max": 0.75,
    },
    "aggressive": {
        "name": "🚀 تهاجمی",
        "target_vol": 0.40,
        "size_min": 0.10, "size_max": 0.90,
    },
}

# ✅ پروفایل فعال (اینجا عوض کن: "conservative" / "balanced" / "aggressive")
ACTIVE_PROFILE_KEY = "balanced"
ACTIVE_PROFILE = PROFILES[ACTIVE_PROFILE_KEY]

# Vol targeting safety bounds (global)
VOL_FLOOR = 0.15
VOL_CEIL = 0.80

API_KEY = os.environ.get("MARGINPAD_API_KEY", "mpb_d04c938486b9f5e711f9a736084fa9b5").strip()
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8601458987:AAEvcxx2ClOTfQIqEp9Ok6TwcvKRaMfIeqo").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

if not API_KEY:
    print("FATAL: MARGINPAD_API_KEY missing")
    sys.exit(1)

BASE = "https://marginpad.io/api/bot/v1"
HEADERS = {"X-API-Key": API_KEY}
H_JSON = {**HEADERS, "Content-Type": "application/json"}

SYMBOLS = [
    "ADA", "AVAX", "LINK", "LTC",
    "ENA", "ATOM", "NEAR", "ARB", "OP", "PEPE", "SHIB", "SUI",
    "APT", "INJ", "TIA", "SEI", "WIF", "LDO", "UNI", "AAVE", "FIL", "RENDER",
]

PIVOT_QQE_PARAMS = {
    "pivot_period": 8,
    "qqe_rsi": 10, "qqe_sf": 5,
    "macd_fast": 6, "macd_slow": 19, "macd_signal": 2,
    "sl_atr_mult": 2.0,
    "tp_rr": 2.0,
    "min_bars": 60,
    "vol_period": 30,
}

PIVOT_QQE_TIMEFRAMES = [
    ("5m",  ["5", "5m", "M5"],                   1, 365 * 24 * 12),
    ("15m", ["15", "15m", "M15"],                2, 365 * 24 * 4),
    ("30m", ["30", "30m", "M30"],                3, 365 * 24 * 2),
    ("1h",  ["60", "60m", "H1"],                 4, 365 * 24),
    ("4h",  ["240", "4h", "H4"],                 5, 365 * 6),
]

LEVERAGE = 10
MAX_TOTAL_POSITIONS = 8
MAX_NOTIONAL_PCT = 0.75   # سقف notional = ۷۵٪ موجودی (safety cap)
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


def compute_dynamic_size(vol30: float) -> float:
    """Vol Targeting: size = target_vol / vol30، محدود به [size_min, size_max]."""
    if np.isnan(vol30) or vol30 <= 0:
        return ACTIVE_PROFILE["target_vol"]
    clamped = max(VOL_FLOOR, min(VOL_CEIL, vol30))
    dyn = ACTIVE_PROFILE["target_vol"] / clamped
    return max(ACTIVE_PROFILE["size_min"], min(ACTIVE_PROFILE["size_max"], dyn))


# ============================================================
# TELEGRAM
# ============================================================
def send_telegram(text: str) -> bool:
    if not TG_TOKEN or not TG_CHAT:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"  [telegram] error: {e}")
        return False


def format_new_trade(sym, sig, notional, margin, balance):
    side = sig["side"]
    emoji = "🟢" if side == "LONG" else "🔴"
    rr = sig["tp_pct"] / sig["sl_pct"] if sig["sl_pct"] > 0 else 0
    tf = sig.get("_tf_label", "1h")
    vol30 = sig.get("_vol30", 0) * 100
    dyn = sig.get("_dyn_size", 0) * 100
    profile = ACTIVE_PROFILE["name"]
    return (
        f"{emoji} <b>معامله جدید: {sym} {side}</b>\n"
        f"🧠 استراتژی: <b>VolTarget (MACD 6,19,2)</b>\n"
        f"📊 تایم‌فریم: <b>{tf}</b> | پروفایل: <b>{profile}</b>\n\n"
        f"📍 ورود: <code>{fmt_price(sig['entry'])}</code>\n"
        f"🛑 SL: <code>{fmt_price(sig['sl'])}</code> ({sig['sl_pct']:.2f}%)\n"
        f"🎯 TP: <code>{fmt_price(sig['tp'])}</code> ({sig['tp_pct']:.2f}%)\n"
        f"📊 R/R: <b>{rr:.2f}</b> | Vol30: <b>{vol30:.1f}%</b>\n"
        f"💰 Size: <b>{dyn:.1f}%</b> از موجودی\n"
        f"   Notional: ${notional:,.2f}\n"
        f"   مارجین: ${margin:.2f} ({LEVERAGE}×)\n"
        f"🏦 موجودی: ${balance:,.2f}\n"
    )


def format_closed_trade(trade):
    emoji = "✅" if trade["pnl_pct"] > 0 else "❌"
    reason = trade.get("close_reason", "")
    reason_line = f"\n📋 دلیل: <b>{reason}</b>" if reason else ""
    return (
        f"{emoji} <b>معامله بسته شد: {trade['symbol']} {trade['side']}</b>\n"
        f"🧠 VolTarget | 📊 {trade.get('tf', '1h')}{reason_line}\n\n"
        f"📍 ورود: <code>{fmt_price(trade['entry'])}</code>\n"
        f"🏁 خروج: <code>{fmt_price(trade['exit'])}</code>\n"
        f"📊 سود/ضرر: <b>{trade['pnl_pct']:+.2f}%</b>\n"
    )


def format_strategy_summary(log_data):
    closed = log_data.get("closed_trades", [])
    if not closed:
        return "<i>هنوز معامله بسته‌شده‌ای ثبت نشده است.</i>"
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
        f"📈 <b>گزارش VolTarget ({ACTIVE_PROFILE['name']}):</b>\n\n"
        f"• کل: {total} (✅{wins} / ❌{losses})\n"
        f"• وین‌ریت: {wr:.1f}%\n"
        f"• سود/زیان کل: <b>{total_pnl:+.2f}%</b>\n"
        f"• میانگین: {avg_pnl:+.2f}%\n\n"
    )
    if not long_df.empty:
        l_wr = len(long_df[long_df["pnl_pct"] > 0]) / len(long_df) * 100
        report += f"🟢 LONG: {len(long_df)} معامله، وین‌ریت {l_wr:.0f}%\n"
    if not short_df.empty:
        s_wr = len(short_df[short_df["pnl_pct"] > 0]) / len(short_df) * 100
        report += f"🔴 SHORT: {len(short_df)} معامله، وین‌ریت {s_wr:.0f}%\n"
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
            err_body = r.json()
        except Exception:
            err_body = r.text[:800]
        print(f"  [{symbol}] HTTP {r.status_code} — error: {err_body}")
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
    """Vol Targeting: annualized 30-bar rolling std of returns."""
    ret = df["close"].pct_change()
    vol = ret.rolling(PIVOT_QQE_PARAMS["vol_period"]).std() * np.sqrt(bars_per_year)
    return vol


# ============================================================
# STRATEGY: MACD(6,19,2) + Pivot(8) + QQE(10,5) — LONG + SHORT
# ============================================================
def detect_pivot_qqe_signals(df, tf_label, bars_per_year):
    """
    Returns list of signals from the LATEST cross on last 2-3 closed bars.
    Includes vol30 and dynamic_size for Vol Targeting.
    """
    P = PIVOT_QQE_PARAMS
    n = len(df)
    if n < P["min_bars"]:
        return []

    close = df["close"]
    high = df["high"]
    low = df["low"]
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
        if cu:
            crosses.append(("UP", i, bar_ts))
        if cd:
            crosses.append(("DOWN", i, bar_ts))

    if not crosses:
        return []

    crosses.sort(key=lambda x: x[1], reverse=True)
    direction, i, bar_ts = crosses[0]

    atr_val = float(atr[i])
    if atr_val <= 0:
        return []

    vol_val = float(vol30.iloc[i]) if not pd.isna(vol30.iloc[i]) else 0.30
    dyn = compute_dynamic_size(vol_val)

    entry = float(close.iloc[i])
    signals = []

    if direction == "UP":
        signals.append({"action": "EXIT_SHORT", "side": "SHORT",
                        "entry": entry, "_tf_label": tf_label, "_bar_ts": bar_ts})
        pivot_ok = close.iloc[i] > pivot_mid.iloc[i]
        qqe_ok = rsi_ma.iloc[i] > 50
        if pivot_ok and qqe_ok:
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
                        "_vol30": vol_val, "_dyn_size": dyn,
                    })
    else:  # DOWN
        signals.append({"action": "EXIT_LONG", "side": "LONG",
                        "entry": entry, "_tf_label": tf_label, "_bar_ts": bar_ts})
        pivot_ok = close.iloc[i] < pivot_mid.iloc[i]
        qqe_ok = rsi_ma.iloc[i] < 50
        if pivot_ok and qqe_ok:
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
                        "_vol30": vol_val, "_dyn_size": dyn,
                    })

    return signals


# ============================================================
# FETCH ALL TIMEFRAMES
# ============================================================
def fetch_timeframes(sym):
    needed = PIVOT_QQE_PARAMS["min_bars"] + 50
    data = {}
    for tf_label, intervals, _, _bpy in PIVOT_QQE_TIMEFRAMES:
        try:
            k_raw = get_klines_fallback(sym, intervals)
            if k_raw and len(k_raw) >= needed:
                data[tf_label] = klines_to_df(k_raw[-300:])
            else:
                data[tf_label] = None
        except Exception:
            data[tf_label] = None
    return data


# ============================================================
# MAIN
# ============================================================
def run_scan() -> int:
    now = datetime.now(timezone.utc)
    print("=" * 70)
    print(f"VolTarget Scan (LONG+SHORT) — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Profile: {ACTIVE_PROFILE['name']} | target_vol={ACTIVE_PROFILE['target_vol']*100:.0f}%")
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
        print(f"Open: {len(open_positions)}")
    except Exception as e:
        print(f"FATAL positions: {e}"); return 1

    # ==========================================================
    # PHASE 1: EXIT check for open VolTarget positions
    # ==========================================================
    newly_closed = []
    for sym in list(log["open_entries"].keys()):
        entry_info = log["open_entries"].get(sym)
        if not entry_info or entry_info.get("strategy") != "VolTarget":
            continue
        if sym not in open_positions:
            continue

        pos_side = entry_info.get("side", "LONG")
        entry_tf = entry_info.get("tf", "1h")

        try:
            tf_intervals, tf_bpy = None, 365
            for tf_label, intervals, _, bpy in PIVOT_QQE_TIMEFRAMES:
                if tf_label == entry_tf:
                    tf_intervals, tf_bpy = intervals, bpy
                    break
            if not tf_intervals:
                tf_intervals, tf_bpy = ["60", "60m", "H1"], 365 * 24

            k_raw = get_klines_fallback(sym, tf_intervals)
            if not k_raw or len(k_raw) < 60:
                continue
            df_tf = klines_to_df(k_raw[-300:])
            signals = detect_pivot_qqe_signals(df_tf, entry_tf, tf_bpy)

            exit_action = f"EXIT_{pos_side}"
            exit_sig = next((s for s in signals if s["action"] == exit_action), None)
            if not exit_sig:
                continue

            pos = open_positions[sym]
            result = close_position(pos)
            if not result:
                continue

            try:
                k1h = get_klines(sym, "60")
                current_price = float(k1h[-1]["close"])
                entry_price = entry_info["entry"]
                if pos_side == "LONG":
                    pnl = (current_price - entry_price) / entry_price * 100
                else:
                    pnl = (entry_price - current_price) / entry_price * 100

                log["open_entries"].pop(sym)
                closed_record = {
                    "symbol": sym, "strategy": "VolTarget", "side": pos_side,
                    "entry": entry_price, "exit": current_price,
                    "pnl_pct": round(pnl, 2),
                    "tf": entry_tf,
                    "close_reason": f"MACD-EXIT ({entry_tf})",
                }
                log["closed_trades"].append(closed_record)
                newly_closed.append(closed_record)
                open_positions.pop(sym, None)
                print(f"  [CLOSED-MANUAL] {sym} {pos_side} (VolTarget {entry_tf}) → PnL: {pnl:+.2f}%")
            except Exception as e:
                print(f"  [{sym}] close pnl error: {e}")
        except Exception as e:
            print(f"  [{sym}] exit-check error: {e}")

    # ==========================================================
    # PHASE 2: Detect SL/TP auto-close
    # ==========================================================
    for sym in list(log["open_entries"].keys()):
        if sym not in open_positions:
            entry_info = log["open_entries"].pop(sym)
            try:
                k = get_klines(sym, "60")
                current_price = float(k[-1]["close"])
                entry_price = entry_info["entry"]
                pos_side = entry_info.get("side", "LONG")
                if pos_side == "LONG":
                    pnl = (current_price - entry_price) / entry_price * 100
                else:
                    pnl = (entry_price - current_price) / entry_price * 100
                closed_record = {
                    "symbol": sym, "strategy": "VolTarget", "side": pos_side,
                    "entry": entry_price, "exit": current_price,
                    "pnl_pct": round(pnl, 2),
                    "tf": entry_info.get("tf", "1h"),
                    "close_reason": "SL/TP",
                }
                log["closed_trades"].append(closed_record)
                newly_closed.append(closed_record)
                print(f"  [CLOSED-AUTO] {sym} {pos_side} (VolTarget) → PnL: {pnl:+.2f}%")
            except Exception as e:
                print(f"  [{sym}] close calc error: {e}")

    # ==========================================================
    # PHASE 3: New entry signals with Vol Targeting sizing
    # ==========================================================
    new_trades = []

    for sym in SYMBOLS:
        if sym in open_positions:
            continue
        if len(open_positions) + len(new_trades) >= MAX_TOTAL_POSITIONS:
            print(f"  Max total positions ({MAX_TOTAL_POSITIONS}) reached — stopping")
            break

        try:
            k1h = get_klines(sym, "60")
            if not k1h or len(k1h) < 5:
                continue
            current_price = float(k1h[-1]["close"])
        except Exception:
            continue

        tf_data = fetch_timeframes(sym)
        all_entries = []

        for tf_label, _, tf_prio, bpy in PIVOT_QQE_TIMEFRAMES:
            df_tf = tf_data.get(tf_label)
            if df_tf is None:
                continue
            try:
                signals = detect_pivot_qqe_signals(df_tf, tf_label, bpy)
                for s in signals:
                    if s["action"].startswith("ENTRY_"):
                        s["_tf_prio"] = tf_prio
                        all_entries.append(s)
            except Exception:
                continue

        if not all_entries:
            continue

        sides = set(s["side"] for s in all_entries)
        if len(sides) > 1:
            print(f"  [{sym}] SKIP: conflicting directions {list(sides)}")
            continue

        all_entries.sort(key=lambda x: x["_tf_prio"], reverse=True)
        sig = all_entries[0]

        ok, reason = is_signal_ok(sig)
        if not ok:
            print(f"  [{sym}] SKIP ({sig['side']} {sig['_tf_label']}): {reason}")
            continue

        if sig["side"] == "LONG":
            if not (sig["sl"] < current_price < sig["tp"]):
                print(f"  [{sym}] SKIP: price out of SL/TP range (LONG)")
                continue
        else:
            if not (sig["tp"] < current_price < sig["sl"]):
                print(f"  [{sym}] SKIP: price out of SL/TP range (SHORT)")
                continue

        min_dist = current_price * 0.003
        if abs(current_price - sig["sl"]) < min_dist or abs(current_price - sig["tp"]) < min_dist:
            print(f"  [{sym}] SKIP: SL/TP too close")
            continue

        # ✅ Vol Targeting sizing
        dyn_size = sig.get("_dyn_size", ACTIVE_PROFILE["target_vol"])
        notional = balance_usd * dyn_size
        notional = min(notional, balance_usd * MAX_NOTIONAL_PCT)   # safety cap
        margin = max(notional / LEVERAGE, 1.0)

        if margin < 1.0:
            print(f"  [{sym}] SKIP: margin ${margin:.2f} too small")
            continue

        coid = f"VolTarget-{sig['side']}-{sym}-{int(now.timestamp())}"
        sl_r = round_price(sig["sl"], sig["entry"])
        tp_r = round_price(sig["tp"], sig["entry"])
        side_lc = "long" if sig["side"] == "LONG" else "short"

        try:
            resp = open_position(sym, side_lc, margin, LEVERAGE, sl_r, tp_r, coid)
        except Exception as e:
            print(f"  [{sym}] network error: {e}")
            continue

        if resp is None or resp.get("idempotent"):
            continue

        tf_saved = sig["_tf_label"]
        log["open_entries"][sym] = {
            "strategy": "VolTarget", "side": sig["side"], "entry": sig["entry"],
            "sl": sig["sl"], "tp": sig["tp"], "margin": margin, "tf": tf_saved,
            "_bar_ts": sig.get("_bar_ts"),
        }
        new_trades.append((sym, sig, notional, margin))

        emoji = "🟢" if sig["side"] == "LONG" else "🔴"
        rr = sig["tp_pct"] / sig["sl_pct"] if sig["sl_pct"] > 0 else 0
        print(f"  {emoji} [VolTarget {tf_saved}] {sig['side']} {sym} "
              f"@ {fmt_price(sig['entry'])} | R/R {rr:.2f} | "
              f"Size {dyn_size*100:.1f}% | Margin ${margin:.2f}")

    save_log(log)

    # ==========================================================
    # Telegram
    # ==========================================================
    msg_blocks = []
    if new_trades:
        msg_blocks.append(f"<b>🔔 معاملات جدید — {now.strftime('%H:%M')} UTC</b>")
        for sym, sig, notional, margin in new_trades:
            msg_blocks.append(format_new_trade(sym, sig, notional, margin, balance_usd))
    if newly_closed:
        msg_blocks.append(f"<b>🔒 معاملات بسته‌شده — {now.strftime('%H:%M')} UTC</b>")
        for t in newly_closed:
            msg_blocks.append(format_closed_trade(t))
    msg_blocks.append(format_strategy_summary(log))

    if msg_blocks and (new_trades or newly_closed):
        if send_telegram("\n\n".join(msg_blocks)):
            print(f"  [telegram] sent")

    print(f"\nResult: {len(new_trades)} new | {len(newly_closed)} closed")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(run_scan())
