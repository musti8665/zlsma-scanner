#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ACMTS × MarginPad × Telegram — Final Production Scanner
========================================================
Strategy: Adaptive Context Multi-Timeframe Strategy
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
# CONFIG
# ============================================================
API_KEY = os.environ.get(
    "MARGINPAD_API_KEY",
    "mpb_d04c938486b9f5e711f9a736084fa9b5",
).strip()

TG_TOKEN = os.environ.get(
    "TELEGRAM_TOKEN",
    "8601458987:AAEvcxx2ClOTfQIqEp9Ok6TwcvKRaMfIeqo",
).strip()

TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

if not API_KEY:
    print("FATAL: MARGINPAD_API_KEY missing")
    sys.exit(1)

BASE = "https://marginpad.io/api/bot/v1"
HEADERS = {"X-API-Key": API_KEY}
H_JSON = {**HEADERS, "Content-Type": "application/json"}

# ============================================================
# لیست ارزهای پشتیبانی شده توسط MarginPad
# ============================================================
SYMBOLS = [
    "BTC", "ETH", "SOL", "AVAX", "DOGE", "SHIB",
    "WIF", "LINK", "AAVE", "ARB", "INJ", "FET",
]

PARAMS = {
    "dc_4h_period": 15,
    "width_lookback": 100,
    "trend_threshold": 1.5,
    "range_threshold": 0.5,
    "dc_1h_period": 15,
    "bb_1h_period": 30,
    "bb_1h_std": 2.0,
    "sl_atr_mult": 4.5,
    "tp_rr_trend": 2.5,
    "max_bars": 20,
}

RISK_PCT_PER_TRADE = 0.00125
LEVERAGE = 10
MAX_MARGIN_PCT = 0.15
LOG_FILE = Path("/tmp/acmts_paper_log.json")

# ============================================================
# TELEGRAM
# ============================================================
def send_telegram(text: str) -> bool:
    if not TG_TOKEN or not TG_CHAT:
        print("  [telegram] skipped — TELEGRAM_CHAT_ID not set")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={
                "chat_id": TG_CHAT,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if r.status_code == 200:
            return True
        print(f"  [telegram] HTTP {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        print(f"  [telegram] error: {e}")
        return False

def format_signal_message(sym, sig, margin):
    return (
        f"🟢 <b>{sym} LONG</b>\n\n"
        f"📍 Entry: <code>{sig['entry']:.6f}</code>\n"
        f"🛑 SL:    <code>{sig['sl']:.6f}</code>  (-{sig['sl_pct']:.2f}%)\n"
        f"🎯 TP:    <code>{sig['tp']:.6f}</code>  (+{sig['tp_pct']:.2f}%)\n"
        f"⚖️ Regime: <b>{sig['regime']}</b>\n"
        f"💰 Margin: <b>${margin:.2f}</b> @ {LEVERAGE}×\n"
        f"🕒 {sig['bar_time'][:16]}"
    )

def format_time_exit_message(sym, hours):
    return (
        f"⏰ <b>{sym} CLOSED</b> (time exit)\n"
        f"Held: {hours:.0f}h (max {PARAMS['max_bars']}h)"
    )

# ============================================================
# MARGINPAD API
# ============================================================
def get_klines(symbol, interval):
    # MarginPad از دقیقه استفاده می‌کند: 60 برای 1h و 240 برای 4h
    interval_map = {"60": "60", "240": "240"}
    api_interval = interval_map.get(str(interval), str(interval))
    
    r = requests.get(f"{BASE}/klines?symbol={symbol}&interval={api_interval}", timeout=15)
    r.raise_for_status()
    return r.json()

def get_positions():
    r = requests.get(f"{BASE}/positions?status=open", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return {p["symbol"]: p for p in r.json().get("positions", [])}

def get_balance():
    r = requests.get(f"{BASE}/balance", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()

def open_position(symbol, side, margin, leverage, sl, tp, coid):
    body = {
        "symbol": symbol, "side": side,
        "margin_usd": round(margin, 2),
        "leverage": leverage, "sl": sl, "tp": tp,
        "client_order_id": coid,
    }
    r = requests.post(f"{BASE}/open", headers=H_JSON, json=body, timeout=15)
    r.raise_for_status()
    return r.json()

def close_position(pos_id, symbol):
    r = requests.post(
        f"{BASE}/close", headers=H_JSON,
        json={"id": pos_id, "symbol": symbol}, timeout=15,
    )
    r.raise_for_status()
    return r.json()

# ============================================================
# LOG
# ============================================================
def load_log():
    if LOG_FILE.exists():
        try:
            return json.loads(LOG_FILE.read_text())
        except Exception:
            pass
    return {"entries": {}, "history": []}

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

# ============================================================
# ACMTS SIGNAL
# ============================================================
def acmts_signal(df_1h, df_4h):
    n = len(df_1h)
    if n < PARAMS["dc_1h_period"] + 20:
        return None
    if df_4h is None or len(df_4h) < (
        PARAMS["dc_4h_period"] + PARAMS["width_lookback"]
    ):
        return None

    close = df_1h["close"].values
    high = df_1h["high"].values
    low = df_1h["low"].values
    atr = compute_atr(df_1h)

    # 4h regime
    h4, l4 = df_4h["high"].values, df_4h["low"].values
    dc_h4 = pd.Series(h4).rolling(PARAMS["dc_4h_period"]).max().values
    dc_l4 = pd.Series(l4).rolling(PARAMS["dc_4h_period"]).min().values
    mid4 = (dc_h4 + dc_l4) / 2
    w4 = (dc_h4 - dc_l4) / np.where(mid4 > 0, mid4, 1)
    wm4 = pd.Series(w4).rolling(PARAMS["width_lookback"]).median().values
    r4 = w4 / np.where(wm4 > 0, wm4, 1)

    reg_raw = np.zeros(len(df_4h), dtype=int)
    reg_raw[r4 > PARAMS["trend_threshold"]] = 1
    reg_raw[r4 < PARAMS["range_threshold"]] = -1
    regime = (pd.Series(reg_raw, index=df_4h.index)
              .reindex(df_1h.index, method="ffill")
              .fillna(0).values.astype(int))

    # 1h indicators
    dc_h1 = pd.Series(high).rolling(PARAMS["dc_1h_period"]).max().shift(1).values
    bb_mid = pd.Series(close).rolling(PARAMS["bb_1h_period"]).mean().values
    bb_std = pd.Series(close).rolling(PARAMS["bb_1h_period"]).std().values
    bb_l = bb_mid - PARAMS["bb_1h_std"] * bb_std
    prev = np.roll(close, 1); prev[0] = np.nan

    i = n - 2  # last CLOSED bar
    if i < 1 or np.isnan(atr[i]):
        return None

    trend_long = ((regime[i] == 1) and (prev[i] <= dc_h1[i]) and (close[i] > dc_h1[i]))
    range_long = ((regime[i] == -1) and (low[i] <= bb_l[i]) and
                  (close[i] > bb_l[i]) and (bb_mid[i] > close[i]))

    if not (trend_long or range_long):
        return None

    entry = float(close[i])
    sl_dist = PARAMS["sl_atr_mult"] * float(atr[i])
    if sl_dist <= 0:
        return None
    sl = entry - sl_dist
    tp = entry + PARAMS["tp_rr_trend"] * (entry - sl) if trend_long else float(bb_mid[i])

    if sl >= entry or tp <= entry:
        return None
    if (entry - sl) / entry > 0.15:
        return None

    return {
        "signal": "LONG", "entry": entry, "sl": sl, "tp": tp,
        "sl_pct": (entry - sl) / entry * 100,
        "tp_pct": (tp - entry) / entry * 100,
        "bar_time": df_1h.index[i].isoformat(),
        "bar_ts": int(df_1h.index[i].timestamp()),
        "regime": "trend" if trend_long else "range",
    }

# ============================================================
# MAIN SCAN
# ============================================================
def run_scan() -> int:
    now = datetime.now(timezone.utc)
    print("=" * 70)
    print(f"ACMTS Scan — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 70)

    log = load_log()

    try:
        bal = get_balance()
        balance_usd = float(bal.get("balance_usd", 10000))
        print(f"Balance: ${balance_usd:,.2f}")
    except Exception as e:
        print(f"FATAL balance: {e}")
        return 1

    try:
        open_positions = get_positions()
        print(f"Open: {len(open_positions)}")
    except Exception as e:
        print(f"FATAL positions: {e}")
        return 1

    # Detect server-side closes
    for sym in list(log["entries"].keys()):
        if sym not in open_positions:
            e = log["entries"].pop(sym)
            e["closed_at"] = now.isoformat()
            e["close_reason"] = "server"
            log["history"].append(e)
            print(f"  [closed] {sym}")

    new_trades = []
    time_exits = []

    for sym in SYMBOLS:
        try:
            k1h = get_klines(sym, "60")
            k4h = get_klines(sym, "240")
            df_1h = klines_to_df(k1h[-300:])
            df_4h = klines_to_df(k4h[-200:])
        except Exception as e:
            print(f"  [{sym}] data: {e}")
            continue

        # Time-based exit
        if sym in open_positions:
            entry_info = log["entries"].get(sym)
            if entry_info:
                opened = pd.to_datetime(entry_info["opened_at"]).to_pydatetime()
                hours_held = (now - opened).total_seconds() / 3600
                if hours_held >= PARAMS["max_bars"]:
                    try:
                        close_position(open_positions[sym]["id"], sym)
                        time_exits.append((sym, hours_held))
                        log["entries"].pop(sym, None)
                        print(f"  [time-exit] {sym} after {hours_held:.0f}h")
                    except Exception as e:
                        print(f"  [{sym}] time-close: {e}")
            continue

        # New signal
        sig = acmts_signal(df_1h, df_4h)
        if sig is None:
            continue

        # Sizing
        risk_amount = balance_usd * RISK_PCT_PER_TRADE
        sl_frac = sig["sl_pct"] / 100.0
        notional = risk_amount / sl_frac
        margin = min(notional / LEVERAGE, balance_usd * MAX_MARGIN_PCT)
        margin = max(margin, 1.0)

        # Idempotent client_order_id
        coid = f"acmts-{sym}-{sig['bar_ts']}"

        try:
            resp = open_position(sym, "long", margin, LEVERAGE,
                                  sig["sl"], sig["tp"], coid)
        except Exception as e:
            print(f"  [{sym}] open: {e}")
            continue

        # MarginPad returns idempotent:true if this bar already traded
        if resp.get("idempotent"):
            print(f"  [{sym}] skip (bar already traded, idempotent)")
            continue

        pos_id = resp["position"]["id"]
        log["entries"][sym] = {
            "position_id": pos_id,
            "opened_at": now.isoformat(),
            "bar_time": sig["bar_time"],
            "bar_ts": sig["bar_ts"],
            "entry": sig["entry"],
            "sl": sig["sl"],
            "tp": sig["tp"],
            "margin_usd": margin,
            "notional": notional,
            "regime": sig["regime"],
        }
        new_trades.append((sym, sig, margin))
        print(f"  [OPEN] {sym} LONG @ {sig['entry']:.6f} | "
              f"SL -{sig['sl_pct']:.1f}% | TP +{sig['tp_pct']:.1f}% | ${margin:.2f}")

    save_log(log)

    # Telegram — only if actual changes
    if new_trades or time_exits:
        blocks = [
            f"<b>🎯 ACMTS — {now.strftime('%Y-%m-%d %H:%M')} UTC</b>\n"
            f"Balance: ${balance_usd:,.2f} | "
            f"Open: {len(open_positions) + len(new_trades) - len(time_exits)}\n"
            + "─" * 28
        ]
        for sym, sig, margin in new_trades:
            blocks.append(format_signal_message(sym, sig, margin))
        for sym, hours in time_exits:
            blocks.append(format_time_exit_message(sym, hours))

        if send_telegram("\n\n".join(blocks)):
            print(f"  [telegram] sent ({len(new_trades) + len(time_exits)} items)")

    print(f"Result: {len(new_trades)} new | {len(time_exits)} time-exits | "
          f"{len(open_positions) + len(new_trades) - len(time_exits)} open")
    print("=" * 70)
    return 0

if __name__ == "__main__":
    sys.exit(run_scan())
