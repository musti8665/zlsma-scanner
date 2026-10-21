#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MarginPad Multi-Strategy Scanner: ACMTS + ParkF (LONG/SHORT)
Includes detailed Telegram reporting for new trades, closed trades, and strategy stats.
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
API_KEY = os.environ.get("MARGINPAD_API_KEY", "mpb_d04c938486b9f5e711f9a736084fa9b5").strip()
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8601458987:AAEvcxx2ClOTfQIqEp9Ok6TwcvKRaMfIeqo").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

if not API_KEY:
    print("FATAL: MARGINPAD_API_KEY missing")
    sys.exit(1)

BASE = "https://marginpad.io/api/bot/v1"
HEADERS = {"X-API-Key": API_KEY}
H_JSON = {**HEADERS, "Content-Type": "application/json"}

# ✅ ۳۰ ارز پشتیبانی‌شده توسط MarginPad
SYMBOLS = [
    "BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX", "LINK", "LTC",
    "ENA", "TRX", "DOT", "ATOM", "NEAR", "ARB", "OP", "PEPE", "SHIB", "SUI",
    "APT", "INJ", "TIA", "SEI", "WIF", "LDO", "UNI", "AAVE", "FIL", "RENDER",
]

# --- پارامترهای استراتژی ACMTS (دست‌نخورده) ---
ACMTS_PARAMS = {
    "dc_4h_period": 15, "width_lookback": 100, "trend_threshold": 1.5, "range_threshold": 0.5,
    "dc_1h_period": 15, "bb_1h_period": 30, "bb_1h_std": 2.0,
    "sl_atr_mult": 4.5, "tp_rr_trend": 2.5, "max_bars": 20,
}

# --- پارامترهای استراتژی ParkF (دست‌نخورده) ---
PARKF_PARAMS = {
    "length": 50, "sigma": 1.0, "sl_atr_mult": 2.0
}

RISK_PCT_PER_TRADE = 0.00125
LEVERAGE = 10
MAX_MARGIN_PCT = 0.15
LOG_FILE = Path("marginpad_trades_log.json")

# ============================================================
# TELEGRAM
# ============================================================
def send_telegram(text: str) -> bool:
    if not TG_TOKEN or not TG_CHAT:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"  [telegram] error: {e}")
        return False

def format_new_trade(sym, sig, margin, balance):
    emoji = "🟢" if sig["side"] == "LONG" else "🔴"
    return (
        f"{emoji} <b>معامله جدید: {sym} {sig['side']}</b>\n"
        f"🧠 استراتژی: <b>{sig['strategy']}</b>\n"
        f"📊 تایم‌فریم: <b>1h</b>\n\n"
        f"📍 قیمت ورود: <code>{sig['entry']:.6f}</code>\n"
        f"🛑 حد ضرر: <code>{sig['sl']:.6f}</code> (-{sig['sl_pct']:.2f}%)\n"
        f"🎯 حد سود: <code>{sig['tp']:.6f}</code> (+{sig['tp_pct']:.2f}%)\n"
        f"💰 مارجین: <b>${margin:.2f}</b> (اهرم {LEVERAGE}×)\n"
        f"🏦 موجودی کل: ${balance:,.2f}\n"
    )

def format_closed_trade(trade):
    emoji = "✅" if trade["pnl_pct"] > 0 else "❌"
    return (
        f"{emoji} <b>معامله بسته شد: {trade['symbol']} {trade['side']}</b>\n"
        f"🧠 استراتژی: <b>{trade['strategy']}</b>\n\n"
        f"📍 قیمت ورود: <code>{trade['entry']:.6f}</code>\n"
        f"🏁 قیمت خروج: <code>{trade['exit']:.6f}</code>\n"
        f"📊 سود/ضرر: <b>{trade['pnl_pct']:+.2f}%</b>\n"
    )

def format_strategy_summary(log_data):
    closed = log_data.get("closed_trades", [])
    if not closed:
        return "<i>هنوز معامله بسته‌شده‌ای ثبت نشده است.</i>"

    df = pd.DataFrame(closed)
    report = "📈 <b>گزارش بازدهی استراتژی‌ها:</b>\n\n"

    for strat in ["ACMTS", "ParkF"]:
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

        report += f"<b>🔹 {strat}</b>\n"
        report += f"• کل معاملات: {total} (✅{wins} / ❌{losses})\n"
        report += f"• وین‌ریت: {wr:.1f}%\n"
        report += f"• سود/زیان کل: <b>{total_pnl:+.2f}%</b>\n"
        report += f"• میانگین هر معامله: {avg_pnl:+.2f}%\n\n"

    return report

# ============================================================
# HELPERS
# ============================================================
def round_price(price: float, ref_price: float) -> float:
    """قیمت را بر اساس تعداد اعشار قیمت مرجع رُند می‌کند (حداکثر ۸ رقم)."""
    s = f"{ref_price:.10f}".rstrip("0")
    if "." in s:
        dec = min(len(s.split(".")[1]), 8)
    else:
        dec = 0
    return round(price, dec)

# ============================================================
# MARGINPAD API
# ============================================================
def get_klines(symbol, interval):
    r = requests.get(f"{BASE}/klines?symbol={symbol}&interval={interval}", timeout=15)
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
    """ارسال سفارش. در صورت خطا، بدنه پاسخ کامل چاپ می‌شود."""
    body = {
        "symbol": symbol,
        "side": side,
        "margin_usd": round(margin, 2),
        "leverage": leverage,
        "sl": sl,
        "tp": tp,
        "client_order_id": coid,
    }
    r = requests.post(f"{BASE}/open", headers=H_JSON, json=body, timeout=15)
    if r.status_code not in (200, 201):
        # ✅ نمایش بدنه کامل خطا برای تشخیص دلیل دقیق 400
        try:
            err_body = r.json()
        except Exception:
            err_body = r.text[:800]
        print(f"  [{symbol}] HTTP {r.status_code} — error: {err_body}")
        print(f"  [{symbol}] payload sent: {body}")
        return None
    return r.json()

# ============================================================
# LOG MANAGEMENT
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

# ============================================================
# STRATEGY 1: ACMTS (بدون تغییر)
# ============================================================
def detect_acmts(df_1h, df_4h):
    P = ACMTS_PARAMS
    n = len(df_1h)
    if n < P["dc_1h_period"] + 20 or len(df_4h) < (P["dc_4h_period"] + P["width_lookback"]):
        return None

    close, high, low = df_1h["close"].values, df_1h["high"].values, df_1h["low"].values
    atr = compute_atr(df_1h)

    h4, l4 = df_4h["high"].values, df_4h["low"].values
    dc_h4 = pd.Series(h4).rolling(P["dc_4h_period"]).max().values
    dc_l4 = pd.Series(l4).rolling(P["dc_4h_period"]).min().values
    mid4 = (dc_h4 + dc_l4) / 2
    w4 = (dc_h4 - dc_l4) / np.where(mid4 > 0, mid4, 1)
    wm4 = pd.Series(w4).rolling(P["width_lookback"]).median().values
    r4 = w4 / np.where(wm4 > 0, wm4, 1)

    reg_raw = np.zeros(len(df_4h), dtype=int)
    reg_raw[r4 > P["trend_threshold"]] = 1
    reg_raw[r4 < P["range_threshold"]] = -1
    regime = pd.Series(reg_raw, index=df_4h.index).reindex(df_1h.index, method="ffill").fillna(0).values.astype(int)

    dc_h1 = pd.Series(high).rolling(P["dc_1h_period"]).max().shift(1).values
    dc_l1 = pd.Series(low).rolling(P["dc_1h_period"]).min().shift(1).values
    bb_mid = pd.Series(close).rolling(P["bb_1h_period"]).mean().values
    bb_std = pd.Series(close).rolling(P["bb_1h_period"]).std().values
    bb_u = bb_mid + P["bb_1h_std"] * bb_std
    bb_l = bb_mid - P["bb_1h_std"] * bb_std
    prev = np.roll(close, 1); prev[0] = np.nan

    i = n - 2
    if i < 1 or np.isnan(atr[i]):
        return None
    sl_dist = P["sl_atr_mult"] * float(atr[i])
    if sl_dist <= 0:
        return None

    if regime[i] == 1:
        if prev[i] <= dc_h1[i] and close[i] > dc_h1[i]:
            entry, sl, tp = float(close[i]), float(close[i]) - sl_dist, float(close[i]) + P["tp_rr_trend"] * sl_dist
            if 0 < (entry - sl) / entry < 0.15 and tp > entry:
                return {"strategy": "ACMTS", "side": "LONG", "entry": entry, "sl": sl, "tp": tp,
                        "sl_pct": (entry-sl)/entry*100, "tp_pct": (tp-entry)/entry*100}
        if prev[i] >= dc_l1[i] and close[i] < dc_l1[i]:
            entry, sl, tp = float(close[i]), float(close[i]) + sl_dist, float(close[i]) - P["tp_rr_trend"] * sl_dist
            if 0 < (sl - entry) / entry < 0.15 and tp < entry:
                return {"strategy": "ACMTS", "side": "SHORT", "entry": entry, "sl": sl, "tp": tp,
                        "sl_pct": (sl-entry)/entry*100, "tp_pct": (entry-tp)/entry*100}
    elif regime[i] == -1:
        if low[i] <= bb_l[i] and close[i] > bb_l[i] and bb_mid[i] > close[i]:
            entry, sl, tp = float(close[i]), float(close[i]) - sl_dist, float(bb_mid[i])
            if 0 < (entry - sl) / entry < 0.15 and tp > entry:
                return {"strategy": "ACMTS", "side": "LONG", "entry": entry, "sl": sl, "tp": tp,
                        "sl_pct": (entry-sl)/entry*100, "tp_pct": (tp-entry)/entry*100}
        if high[i] >= bb_u[i] and close[i] < bb_u[i] and bb_mid[i] < close[i]:
            entry, sl, tp = float(close[i]), float(close[i]) + sl_dist, float(bb_mid[i])
            if 0 < (sl - entry) / entry < 0.15 and tp < entry:
                return {"strategy": "ACMTS", "side": "SHORT", "entry": entry, "sl": sl, "tp": tp,
                        "sl_pct": (sl-entry)/entry*100, "tp_pct": (entry-tp)/entry*100}
    return None

# ============================================================
# STRATEGY 2: ParkF (بدون تغییر)
# ============================================================
def detect_parkf(df):
    P = PARKF_PARAMS
    n = len(df)
    if n < P["length"] + 5:
        return None
    close, high, low = df["close"].values, df["high"].values, df["low"].values
    atr = compute_atr(df)

    shape = (n - P["length"] + 1, P["length"])
    strides = (close.strides[0], close.strides[0])
    y_win = np.lib.stride_tricks.as_strided(close, shape=shape, strides=strides)
    x = np.arange(P["length"], dtype=float)
    xm = x.mean()
    xv = ((x - xm) ** 2).sum()
    ym = y_win.mean(axis=1, keepdims=True)
    slopes = ((x - xm) * (y_win - ym)).sum(axis=1) / xv
    ints = ym.flatten() - slopes * xm
    tv = ints[:, None] + slopes[:, None] * x
    resid = y_win - tv
    stds = np.sqrt((resid ** 2).mean(axis=1))

    base = np.full(n, np.nan); base[P["length"] - 1:] = ints
    std_arr = np.full(n, np.nan); std_arr[P["length"] - 1:] = stds
    upper = base + P["sigma"] * std_arr
    lower = base - P["sigma"] * std_arr

    i = n - 2
    if i < P["length"] or np.isnan(base[i]):
        return None
    p, hi, lo, a = close[i], high[i], low[i], atr[i]

    sl_dist = P["sl_atr_mult"] * a
    if sl_dist <= 0:
        return None

    if lo <= lower[i] and p > lower[i]:
        entry, sl, tp = p, p - sl_dist, base[i]
        if 0 < (entry - sl) / entry < 0.15 and tp > entry:
            return {"strategy": "ParkF", "side": "LONG", "entry": entry, "sl": sl, "tp": tp,
                    "sl_pct": (entry-sl)/entry*100, "tp_pct": (tp-entry)/entry*100}

    if hi >= upper[i] and p < upper[i]:
        entry, sl, tp = p, p + sl_dist, base[i]
        if 0 < (sl - entry) / entry < 0.15 and tp < entry:
            return {"strategy": "ParkF", "side": "SHORT", "entry": entry, "sl": sl, "tp": tp,
                    "sl_pct": (sl-entry)/entry*100, "tp_pct": (entry-tp)/entry*100}
    return None

# ============================================================
# MAIN
# ============================================================
def run_scan() -> int:
    now = datetime.now(timezone.utc)
    print("=" * 70)
    print(f"Multi-Strategy Scan — {now.strftime('%Y-%m-%d %H:%M UTC')}")
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

    # --- تشخیص معاملات بسته‌شده و محاسبه PnL ---
    newly_closed = []
    for sym in list(log["open_entries"].keys()):
        if sym not in open_positions:
            entry_info = log["open_entries"].pop(sym)
            try:
                k = get_klines(sym, "60")
                current_price = float(k[-1]["close"])
                entry_price = entry_info["entry"]
                side = entry_info["side"]

                if side == "LONG":
                    pnl = (current_price - entry_price) / entry_price * 100
                else:
                    pnl = (entry_price - current_price) / entry_price * 100

                closed_record = {
                    "symbol": sym, "strategy": entry_info["strategy"], "side": side,
                    "entry": entry_price, "exit": current_price, "pnl_pct": round(pnl, 2)
                }
                log["closed_trades"].append(closed_record)
                newly_closed.append(closed_record)
                print(f"  [CLOSED] {sym} ({entry_info['strategy']}) -> PnL: {pnl:+.2f}%")
            except Exception as e:
                print(f"  [{sym}] close calc error: {e}")

    new_trades = []

    for sym in SYMBOLS:
        if sym in open_positions:
            continue

        try:
            k1h = get_klines(sym, "60")
            df_1h = klines_to_df(k1h[-300:])
        except Exception:
            continue

        signals = []

        # بررسی ACMTS
        try:
            k4h = get_klines(sym, "240")
            df_4h = klines_to_df(k4h[-200:])
            s = detect_acmts(df_1h, df_4h)
            if s:
                signals.append(s)
        except Exception:
            pass

        # بررسی ParkF
        try:
            s = detect_parkf(df_1h)
            if s:
                signals.append(s)
        except Exception:
            pass

        if not signals:
            continue

        sig = signals[0]  # اولویت با ACMTS

        # --- ✅ اعتبارسنجی با قیمت لحظه‌ای ---
        try:
            current_price = float(k1h[-1]["close"])
        except Exception:
            continue

        if sig["side"] == "LONG":
            if current_price <= sig["sl"]:
                print(f"  [{sym}] SKIP: price {current_price:.6f} hit SL {sig['sl']:.6f}")
                continue
            if current_price >= sig["tp"]:
                print(f"  [{sym}] SKIP: price {current_price:.6f} passed TP {sig['tp']:.6f}")
                continue
        else:  # SHORT
            if current_price >= sig["sl"]:
                print(f"  [{sym}] SKIP: price {current_price:.6f} hit SL {sig['sl']:.6f}")
                continue
            if current_price <= sig["tp"]:
                print(f"  [{sym}] SKIP: price {current_price:.6f} passed TP {sig['tp']:.6f}")
                continue

        # ✅ حداقل فاصله ۰.۳٪ از قیمت فعلی تا SL/TP
        min_dist = current_price * 0.003
        if abs(current_price - sig["sl"]) < min_dist or abs(current_price - sig["tp"]) < min_dist:
            print(f"  [{sym}] SKIP: SL/TP too close to current price ({current_price:.6f})")
            continue

        # محاسبه مارجین
        risk_amount = balance_usd * RISK_PCT_PER_TRADE
        sl_frac = sig["sl_pct"] / 100.0
        if sl_frac <= 0:
            continue
        notional = risk_amount / sl_frac
        margin = max(min(notional / LEVERAGE, balance_usd * MAX_MARGIN_PCT), 1.0)
        coid = f"{sig['strategy']}-{sym}-{int(now.timestamp())}"

        # ✅ رُند کردن قیمت‌ها به دقت نماد
        sl_r = round_price(sig["sl"], sig["entry"])
        tp_r = round_price(sig["tp"], sig["entry"])

        # ارسال سفارش
        try:
            resp = open_position(sym, sig["side"].lower(), margin, LEVERAGE, sl_r, tp_r, coid)
        except Exception as e:
            print(f"  [{sym}] network error: {e}")
            continue

        if resp is None:
            continue
        if resp.get("idempotent"):
            continue

        log["open_entries"][sym] = {
            "strategy": sig["strategy"], "side": sig["side"], "entry": sig["entry"],
            "sl": sig["sl"], "tp": sig["tp"], "margin": margin
        }
        new_trades.append((sym, sig, margin))

        emoji = "🟢" if sig["side"] == "LONG" else "🔴"
        print(f"  {emoji} [{sig['strategy']}] {sig['side']} {sym} @ {sig['entry']:.6f} | Margin: ${margin:.2f}")

    save_log(log)

    # ==========================================
    # ساخت گزارش تلگرام
    # ==========================================
    msg_blocks = []

    if new_trades:
        msg_blocks.append(f"<b>🔔 معاملات جدید — {now.strftime('%H:%M')} UTC</b>")
        for sym, sig, margin in new_trades:
            msg_blocks.append(format_new_trade(sym, sig, margin, balance_usd))

    if newly_closed:
        msg_blocks.append(f"<b>🔒 معاملات بسته‌شده — {now.strftime('%H:%M')} UTC</b>")
        for t in newly_closed:
            msg_blocks.append(format_closed_trade(t))

    msg_blocks.append(format_strategy_summary(log))

    if msg_blocks and (new_trades or newly_closed):
        final_msg = "\n\n".join(msg_blocks)
        if send_telegram(final_msg):
            print(f"  [telegram] sent")

    print(f"\nResult: {len(new_trades)} new | {len(newly_closed)} closed")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(run_scan())
