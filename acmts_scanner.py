#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MarginPad Winning Combo Scanner — 5 Strategies
================================================
  1. StrategyD   (30m) — SHORT only — RSI+QQE+Supertrend+Kijun+ATR ratio
  2. GoldenCombo (1D)  — LONG only  — MA200+LSMA+RSI2+Pivot+TwinRange
  3. COV-Pivot1  (5m/15m/30m/1h/4h/1D) — LONG only — Dual COV OR + Pivot1
  4. ACMTS       (1H)  — L/S        — Regime-based Donchian/BB
  5. ParkF       (1H)  — L/S        — Linear regression reversion (TP/RR filtered)

Selection: pick highest priority; skip on LONG/SHORT conflict.
"""

import os
import sys
import json
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
from numpy.lib.stride_tricks import sliding_window_view

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

# --- ACMTS ---
ACMTS_PARAMS = {
    "dc_4h_period": 15, "width_lookback": 100,
    "trend_threshold": 1.5, "range_threshold": 0.5,
    "dc_1h_period": 15, "bb_1h_period": 30, "bb_1h_std": 2.0,
    "sl_atr_mult": 4.5, "tp_rr_trend": 2.5, "max_bars": 20,
}

# --- ParkF ---
PARKF_PARAMS = {
    "length": 50, "sigma": 1.0, "sl_atr_mult": 2.0,
    "min_tp_dist_pct": 0.3, "min_rr": 0.5,
}

# --- GoldenCombo ---
GOLDEN_PARAMS = {
    "per1": 15, "mult1": 2.0, "per2": 85, "mult2": 2.0,
    "sl_mult": 4.0, "lsma_period": 50, "rsi_period": 2,
    "pivot_period": 4, "tp_rr": 2.0, "min_bars": 220,
}

# --- Strategy D (Config D Final Winner — SHORT 30m) ---
SD_PARAMS = {
    "rsi_period": 2, "pivot_lookback": 4, "rsi_threshold": 6.5,
    "kijun_period": 42,
    "qqe_rsi": 10, "qqe_smooth": 6, "qqe_factor": 4.236,
    "atr_ratio_win": 200, "atr_ratio_min": 0.9,
    "st_period": 10, "st_mult": 3.0,
    "sl_atr": 2.25, "tp_rr": 4.0,
    "min_bars": 220,
}

# --- COV-Pivot1 (Dual COV OR + Pivot1) — LONG only, multi-timeframe ---
COV_PARAMS = {
    "cov1_n": 21, "cov1_m": 120,
    "cov2_n": 5,  "cov2_m": 500,
    "pivot1_p": 10,
    "atr_p": 5, "sl_mult": 2.0,
    "tp_rr": 2.0,        # TP = 2R (اضافه شده برای MarginPad)
    "min_bars": 550,
}

COV_TIMEFRAMES = [
    ("5m",  ["5", "5m", "M5"],                    1),
    ("15m", ["15", "15m", "M15"],                 2),
    ("30m", ["30", "30m", "M30"],                 3),
    ("1h",  ["60", "60m", "H1"],                  4),
    ("4h",  ["240", "4h", "H4"],                  5),
    ("1D",  ["1440", "D", "1d", "1D", "day"],     6),
]

# --- Risk / Sizing ---
RISK_PCT_PER_TRADE = 0.00125
LEVERAGE = 10
MAX_NOTIONAL_PCT = 0.15
MAX_TOTAL_POSITIONS = 8
LOG_FILE = Path("marginpad_trades_log.json")

# --- Global quality gates ---
MIN_SL_PCT = 0.5
MIN_TP_PCT = 0.3
MIN_RR = 0.5

# --- Priority & TF mapping ---
PRIORITY = {
    "StrategyD": 4,
    "GoldenCombo": 3,
    "COV-Pivot1": 3,     # tie with GoldenCombo — R/R breaks tie
    "ACMTS": 2,
    "ParkF": 1,
}

TF_MAP = {"StrategyD": "30m", "GoldenCombo": "1D", "ACMTS": "1h", "ParkF": "1h"}


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


def pick_best_signal(signals):
    if not signals:
        return None
    return sorted(
        signals,
        key=lambda s: (PRIORITY.get(s["strategy"], 0), s["tp_pct"] / max(s["sl_pct"], 1e-9)),
        reverse=True,
    )[0]


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


def format_new_trade(sym, sig, margin, balance):
    emoji = "🟢" if sig["side"] == "LONG" else "🔴"
    # تایم‌فریم از سیگنال یا TF_MAP
    tf = sig.get("_tf_label") or TF_MAP.get(sig["strategy"], "1h")
    rr = sig["tp_pct"] / sig["sl_pct"] if sig["sl_pct"] > 0 else 0
    return (
        f"{emoji} <b>معامله جدید: {sym} {sig['side']}</b>\n"
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
    return (
        f"{emoji} <b>معامله بسته شد: {trade['symbol']} {trade['side']}</b>\n"
        f"🧠 استراتژی: <b>{trade['strategy']}</b>\n"
        f"📊 تایم‌فریم: <b>{tf}</b>\n\n"
        f"📍 قیمت ورود: <code>{fmt_price(trade['entry'])}</code>\n"
        f"🏁 قیمت خروج: <code>{fmt_price(trade['exit'])}</code>\n"
        f"📊 سود/ضرر: <b>{trade['pnl_pct']:+.2f}%</b>\n"
    )


def format_strategy_summary(log_data):
    closed = log_data.get("closed_trades", [])
    if not closed:
        return "<i>هنوز معامله بسته‌شده‌ای ثبت نشده است.</i>"

    df = pd.DataFrame(closed)
    report = "📈 <b>گزارش بازدهی استراتژی‌ها:</b>\n\n"

    for strat in ["StrategyD", "GoldenCombo", "COV-Pivot1", "ACMTS", "ParkF"]:
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
# MARGINPAD API
# ============================================================
def get_klines(symbol, interval):
    r = requests.get(f"{BASE}/klines?symbol={symbol}&interval={interval}", timeout=15)
    r.raise_for_status()
    return r.json()


def get_klines_fallback(symbol, intervals):
    """اولین فرمت interval که کار کند را برمی‌گرداند."""
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
    body = {
        "symbol": symbol, "side": side, "margin_usd": round(margin, 2),
        "leverage": leverage, "sl": sl, "tp": tp, "client_order_id": coid,
    }
    r = requests.post(f"{BASE}/open", headers=H_JSON, json=body, timeout=15)
    if r.status_code not in (200, 201):
        try:
            err_body = r.json()
        except Exception:
            err_body = r.text[:800]
        print(f"  [{symbol}] HTTP {r.status_code} — error: {err_body}")
        print(f"  [{symbol}] payload sent: {body}")
        return None
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


# --- GoldenCombo indicators ---
def compute_lsma(series, period):
    x = np.arange(period); x_sum = x.sum(); x2_sum = (x**2).sum()
    y_sum = series.rolling(period).sum()
    xy_sum = series.rolling(period).apply(lambda y: (y * x).sum(), raw=True)
    slope = (period * xy_sum - x_sum * y_sum) / (period * x2_sum - x_sum**2)
    return slope * (period - 1) + (y_sum - slope * x_sum) / period


def compute_rsi_gc(series, period):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    return 100 - (100 / (1 + gain / loss))


def compute_pivot_high(series, n):
    pivot = series == series.rolling(2 * n + 1, center=True).max()
    return pivot.shift(n).fillna(False)


def apply_twin_range_filter(close, per1, mult1, per2, mult2):
    def smoothrng(x, t, m):
        wper = t * 2 - 1
        avrng = x.diff().abs().ewm(span=t, adjust=False).mean()
        return avrng.ewm(span=wper, adjust=False).mean() * m

    smrng = (smoothrng(close, per1, mult1) + smoothrng(close, per2, mult2)) / 2
    filt = np.zeros(len(close)); filt[0] = close.iloc[0]
    for i in range(1, len(close)):
        pf = filt[i-1]; x = close.iloc[i]; r = smrng.iloc[i]
        filt[i] = (pf if (x > pf and (x - r) < pf) else
                   (x - r) if (x > pf) else
                   pf if (x + r) > pf else (x + r))
    filt = pd.Series(filt, index=close.index)

    up = np.zeros(len(close)); dn = np.zeros(len(close))
    for i in range(1, len(close)):
        if filt.iloc[i] > filt.iloc[i-1]: up[i] = up[i-1] + 1; dn[i] = 0
        elif filt.iloc[i] < filt.iloc[i-1]: up[i] = 0; dn[i] = dn[i-1] + 1
        else: up[i] = up[i-1]; dn[i] = dn[i-1]
    up = pd.Series(up, index=close.index); dn = pd.Series(dn, index=close.index)

    long_cond = (close > filt) & (up > 0)
    short_cond = (close < filt) & (dn > 0)

    cond_ini = np.zeros(len(close))
    for i in range(1, len(close)):
        cond_ini[i] = 1 if long_cond.iloc[i] else -1 if short_cond.iloc[i] else cond_ini[i-1]
    cond_ini = pd.Series(cond_ini, index=close.index)

    return long_cond & (cond_ini.shift(1) == -1), short_cond & (cond_ini.shift(1) == 1)


# --- Strategy D indicators ---
def sd_rsi(close, period):
    d = np.diff(close, prepend=close[0])
    g = pd.Series(np.where(d > 0, d, 0)).ewm(alpha=1/period, adjust=False).mean().values
    lo = pd.Series(np.where(d < 0, -d, 0)).ewm(alpha=1/period, adjust=False).mean().values
    return 100 - 100 / (1 + g / np.where(lo > 0, lo, np.nan))


def sd_pivots_low(series, lookback):
    n = len(series)
    out = np.zeros(n, dtype=bool)
    if n < 2*lookback + 1:
        return out
    w = sliding_window_view(series, 2*lookback + 1)
    c = w[:, lookback]
    out[lookback:lookback+len(c)] = (
        (c < w[:, :lookback].min(axis=1)) &
        (c < w[:, lookback+1:].min(axis=1))
    )
    return out


def sd_atr(high, low, close, period=14):
    tr = np.maximum(high - low,
                    np.maximum(np.abs(high - np.roll(close, 1)),
                               np.abs(low - np.roll(close, 1))))
    tr[0] = high[0] - low[0]
    return pd.Series(tr).ewm(alpha=1/period, adjust=False).mean().values


def sd_kijun(high, low, period):
    hh = pd.Series(high).rolling(period).max().values
    ll = pd.Series(low).rolling(period).min().values
    return (hh + ll) / 2


def sd_qqe_flip(close, rsi_period, smooth, factor=4.236):
    n = len(close)
    r = sd_rsi(close, rsi_period)
    rma = pd.Series(r).ewm(span=smooth, adjust=False).mean().values
    rdiff = np.abs(np.diff(r, prepend=r[0]))
    wilder = 2*rsi_period - 1
    atr_r = pd.Series(rdiff).ewm(alpha=1/wilder, adjust=False).mean().values
    dar = atr_r * factor
    trend = np.zeros(n, dtype=int)
    if n == 0:
        return np.zeros(n, dtype=bool)
    lb = np.full(n, np.nan); sb = np.full(n, np.nan)
    lb[0] = rma[0] - dar[0]; sb[0] = rma[0] + dar[0]; trend[0] = 1
    for i in range(1, n):
        nl = rma[i] - dar[i]; ns = rma[i] + dar[i]
        if rma[i-1] > lb[i-1] and rma[i] > lb[i-1]:
            lb[i] = max(lb[i-1], nl)
        else:
            lb[i] = nl
        if rma[i-1] < sb[i-1] and rma[i] < sb[i-1]:
            sb[i] = min(sb[i-1], ns)
        else:
            sb[i] = ns
        if rma[i] > sb[i-1]:
            trend[i] = 1
        elif rma[i] < lb[i-1]:
            trend[i] = -1
        else:
            trend[i] = trend[i-1]
    flip = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if trend[i-1] == 1 and trend[i] == -1:
            flip[i] = True
    return flip


def sd_supertrend(high, low, close, period, mult):
    n = len(close)
    atr_s = pd.Series(sd_atr(high, low, close)).rolling(period).mean().values
    hl2 = (high + low) / 2
    upper = hl2 + mult * atr_s
    lower = hl2 - mult * atr_s
    trend = np.ones(n, dtype=int)
    fu = np.full(n, np.nan); fl = np.full(n, np.nan)
    for i in range(period, n):
        if np.isnan(upper[i]) or np.isnan(lower[i]):
            continue
        if np.isnan(fu[i-1]):
            fu[i] = upper[i]; fl[i] = lower[i]
            trend[i] = 1 if close[i] > fl[i] else -1
            continue
        fu[i] = upper[i] if (upper[i] < fu[i-1] or close[i-1] > fu[i-1]) else fu[i-1]
        fl[i] = lower[i] if (lower[i] > fl[i-1] or close[i-1] < fl[i-1]) else fl[i-1]
        if trend[i-1] == 1:
            trend[i] = -1 if close[i] < fl[i] else 1
        else:
            trend[i] = 1 if close[i] > fu[i] else -1
    return trend


# --- COV-Pivot1 indicators ---
def cov_compute_cov(c, n, m):
    """بازگشت: (up_begins, is_ranging, downtrend)"""
    sma = c.rolling(int(n)).mean()
    std = c.rolling(int(n)).std(ddof=0)
    cov = std / sma.replace(0, np.nan)
    med = cov.rolling(int(m)).median()
    is_r = cov < med
    up = (c > sma) & (~is_r)
    dn = (c < sma) & (~is_r)
    up_beg = (up & is_r.shift(1).fillna(False)).fillna(False)
    return up_beg, is_r, dn


def cov_atr(df, period=5):
    h = df["high"]; l = df["low"]; c = df["close"]
    tr = np.maximum(h - l, np.maximum((h - c.shift()).abs(), (l - c.shift()).abs()))
    return tr.rolling(int(period)).mean()


# ============================================================
# STRATEGY 1: ACMTS
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
    regime = pd.Series(reg_raw, index=df_4h.index).reindex(
        df_1h.index, method="ffill").fillna(0).values.astype(int)

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
# STRATEGY 2: ParkF
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
        entry = p; sl = p - sl_dist; tp = base[i]
        tp_dist = tp - entry; sl_dist_actual = entry - sl
        min_tp = entry * P["min_tp_dist_pct"] / 100.0
        if (0 < sl_dist_actual / entry < 0.15
                and tp_dist >= min_tp
                and tp_dist >= P["min_rr"] * sl_dist_actual):
            return {"strategy": "ParkF", "side": "LONG", "entry": entry, "sl": sl, "tp": tp,
                    "sl_pct": (entry-sl)/entry*100, "tp_pct": (tp-entry)/entry*100}

    if hi >= upper[i] and p < upper[i]:
        entry = p; sl = p + sl_dist; tp = base[i]
        tp_dist = entry - tp; sl_dist_actual = sl - entry
        min_tp = entry * P["min_tp_dist_pct"] / 100.0
        if (0 < sl_dist_actual / entry < 0.15
                and tp_dist >= min_tp
                and tp_dist >= P["min_rr"] * sl_dist_actual):
            return {"strategy": "ParkF", "side": "SHORT", "entry": entry, "sl": sl, "tp": tp,
                    "sl_pct": (sl-entry)/entry*100, "tp_pct": (entry-tp)/entry*100}
    return None


# ============================================================
# STRATEGY 3: GoldenCombo (1D, LONG only)
# ============================================================
def detect_golden_combo(df_daily):
    P = GOLDEN_PARAMS
    n = len(df_daily)
    if n < P["min_bars"]:
        return None

    close = df_daily["close"]
    high = df_daily["high"]
    low = df_daily["low"]

    ma200 = close.rolling(200).mean()
    lsma = compute_lsma(close, P["lsma_period"])
    rsi = compute_rsi_gc(close, P["rsi_period"])
    pivot_h = compute_pivot_high(high, P["pivot_period"])
    last_pivot_val = high.where(pivot_h).ffill()

    tr = np.maximum(high - low, np.maximum(np.abs(high - close.shift(1)), np.abs(low - close.shift(1))))
    atr = tr.rolling(14).mean()

    long_sig, _short_sig = apply_twin_range_filter(close, P["per1"], P["mult1"], P["per2"], P["mult2"])

    i = n - 2
    price = float(close.iloc[i])

    if (pd.isna(ma200.iloc[i]) or pd.isna(lsma.iloc[i])
            or pd.isna(rsi.iloc[i]) or pd.isna(atr.iloc[i])):
        return None

    is_200ma_ok = price > float(ma200.iloc[i])
    is_lsma_ok = (price > float(lsma.iloc[i])) and (float(lsma.iloc[i]) > float(lsma.iloc[i-1]))
    is_rsi_ok = float(rsi.iloc[i]) > 50
    is_pivot_ok = (not pd.isna(last_pivot_val.iloc[i])) and (price > float(last_pivot_val.iloc[i]))
    recent_long = bool(long_sig.iloc[i]) or bool(long_sig.iloc[i-1])

    if is_200ma_ok and is_lsma_ok and is_rsi_ok and is_pivot_ok and recent_long:
        sl = price - P["sl_mult"] * float(atr.iloc[i])
        sl_dist = price - sl
        if sl_dist <= 0:
            return None
        tp = price + P["tp_rr"] * sl_dist
        sl_pct = sl_dist / price * 100
        tp_pct = (tp - price) / price * 100
        if 0 < sl_pct < 15 and tp_pct > 0:
            return {"strategy": "GoldenCombo", "side": "LONG", "entry": price, "sl": sl, "tp": tp,
                    "sl_pct": sl_pct, "tp_pct": tp_pct}
    return None


# ============================================================
# STRATEGY 4: Strategy D (30m, SHORT only)
# ============================================================
def detect_strategy_d(df_30m):
    P = SD_PARAMS
    n = len(df_30m)
    if n < P["min_bars"]:
        return None

    close = df_30m["close"].values
    high = df_30m["high"].values
    low = df_30m["low"].values

    a = sd_atr(high, low, close)
    r = sd_rsi(close, P["rsi_period"])

    piv = sd_pivots_low(r, P["pivot_lookback"])
    lpl = np.full(n, np.nan)
    cur = np.nan
    for t in range(n):
        p_idx = t - P["pivot_lookback"]
        if p_idx >= 0 and piv[p_idx]:
            cur = r[p_idx]
        lpl[t] = cur

    kj = sd_kijun(high, low, P["kijun_period"])
    flip = sd_qqe_flip(close, P["qqe_rsi"], P["qqe_smooth"], P["qqe_factor"])
    atr_ratio = a / pd.Series(a).rolling(P["atr_ratio_win"]).mean().values
    st = sd_supertrend(high, low, close, P["st_period"], P["st_mult"])

    i = n - 2
    if i < P["min_bars"] - 1:
        return None
    if (np.isnan(lpl[i]) or np.isnan(r[i]) or np.isnan(kj[i])
            or np.isnan(atr_ratio[i]) or np.isnan(a[i])):
        return None

    cond_rsi = r[i] < lpl[i] - P["rsi_threshold"]
    cond_kijun = close[i] < kj[i]
    cond_qqe = bool(flip[i])
    cond_atr = atr_ratio[i] >= P["atr_ratio_min"]
    cond_st = st[i] == -1

    if not (cond_rsi and cond_kijun and cond_qqe and cond_atr and cond_st):
        return None

    entry = float(close[i])
    sl = entry + P["sl_atr"] * float(a[i])
    risk = sl - entry
    if risk <= 0 or risk / entry > 0.20:
        return None
    tp = entry - P["tp_rr"] * risk

    return {
        "strategy": "StrategyD", "side": "SHORT", "entry": entry, "sl": sl, "tp": tp,
        "sl_pct": (sl - entry) / entry * 100,
        "tp_pct": (entry - tp) / entry * 100,
    }


# ============================================================
# STRATEGY 5: COV-Pivot1 (Dual COV OR + Pivot1) — LONG only, multi-TF
# ============================================================
def detect_cov_pivot1(df, tf_label):
    """
    ورود LONG اگر همه شرایط برقرار باشد:
      - (COV1_up OR COV2_up) — یکی از دو رژیم شکست
      - Pivot1_ok — قیمت بالای آخرین سقف محلی (10-bar)
      - NOT is_ranging — نه در رنج (COV1 و COV2)
      - NOT downtrend — نه نزولی (COV1 و COV2)

    SL = entry - 2.0 × ATR(5)
    TP = entry + 2R (اضافه شده برای MarginPad)
    """
    P = COV_PARAMS
    n = len(df)
    if n < P["min_bars"]:
        return None

    close_s = df["close"]
    high_s = df["high"]

    # COV1 (slow: 21/120)
    up1, is_r1, dn1 = cov_compute_cov(close_s, P["cov1_n"], P["cov1_m"])
    # COV2 (fast: 5/500)
    up2, is_r2, dn2 = cov_compute_cov(close_s, P["cov2_n"], P["cov2_m"])

    # Pivot1 (10-bar high)
    ph1 = compute_pivot_high(high_s, P["pivot1_p"])
    piv1_ok = (close_s > high_s.where(ph1).ffill()).fillna(False)

    atr_series = cov_atr(df, P["atr_p"])

    i = n - 2
    if i < P["min_bars"] - 1:
        return None

    price = float(close_s.iloc[i])

    c_cov1 = bool(up1.iloc[i] or up1.iloc[i-1])
    c_cov2 = bool(up2.iloc[i] or up2.iloc[i-1])
    c_pivot = bool(piv1_ok.iloc[i])
    is_ranging = bool(is_r1.iloc[i] and is_r2.iloc[i])
    is_dn = bool(dn1.iloc[i] and dn2.iloc[i])

    if not ((c_cov1 or c_cov2) and c_pivot and not is_ranging and not is_dn):
        return None

    atr_val = atr_series.iloc[i]
    if pd.isna(atr_val) or float(atr_val) <= 0:
        return None

    sl = price - P["sl_mult"] * float(atr_val)
    sl_dist = price - sl
    if sl_dist <= 0:
        return None

    tp = price + P["tp_rr"] * sl_dist
    sl_pct = sl_dist / price * 100
    tp_pct = (tp - price) / price * 100

    if not (0 < sl_pct < 15):
        return None

    return {
        "strategy": "COV-Pivot1", "side": "LONG",
        "entry": price, "sl": sl, "tp": tp,
        "sl_pct": sl_pct, "tp_pct": tp_pct,
        "_tf_label": tf_label,
    }


# ============================================================
# MAIN
# ============================================================
def run_scan() -> int:
    now = datetime.now(timezone.utc)
    print("=" * 70)
    print(f"Winning Combo Scan (5 strategies) — {now.strftime('%Y-%m-%d %H:%M UTC')}")
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

    # --- Closed trades ---
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
                    "entry": entry_price, "exit": current_price, "pnl_pct": round(pnl, 2),
                    "tf": entry_info.get("tf", TF_MAP.get(entry_info["strategy"], "1h")),
                }
                log["closed_trades"].append(closed_record)
                newly_closed.append(closed_record)
                print(f"  [CLOSED] {sym} ({entry_info['strategy']}) -> PnL: {pnl:+.2f}%")
            except Exception as e:
                print(f"  [{sym}] close calc error: {e}")

    new_trades = []
    max_notional = balance_usd * MAX_NOTIONAL_PCT

    for sym in SYMBOLS:
        if sym in open_positions:
            continue

        if len(open_positions) + len(new_trades) >= MAX_TOTAL_POSITIONS:
            print(f"  Max total positions ({MAX_TOTAL_POSITIONS}) reached — stopping")
            break

        try:
            k1h = get_klines(sym, "60")
            df_1h = klines_to_df(k1h[-300:])
        except Exception:
            continue

        signals = []

        # Priority 4: StrategyD (30m, SHORT only)
        try:
            k30_raw = get_klines_fallback(sym, ["30", "30m", "M30"])
            if k30_raw:
                df_30m = klines_to_df(k30_raw[-500:])
                s = detect_strategy_d(df_30m)
                if s:
                    signals.append(s)
        except Exception:
            pass

        # Priority 3: GoldenCombo (1D, LONG only)
        try:
            k1d_raw = get_klines_fallback(sym, ["1440", "D", "1d", "1D", "day"])
            if k1d_raw:
                df_1d = klines_to_df(k1d_raw[-500:])
                s = detect_golden_combo(df_1d)
                if s:
                    signals.append(s)
        except Exception:
            pass

        # Priority 3 (tie): COV-Pivot1 (LONG only, multi-TF — highest TF wins)
        try:
            best_cov = None
            best_cov_tf_prio = -1
            for tf_label, interval_variants, tf_prio in COV_TIMEFRAMES:
                try:
                    k_raw = get_klines_fallback(sym, interval_variants)
                    if not k_raw:
                        continue
                    needed = COV_PARAMS["min_bars"] + 20
                    if len(k_raw) < needed:
                        continue
                    df_tf = klines_to_df(k_raw[-needed:])
                    s = detect_cov_pivot1(df_tf, tf_label)
                    if s and tf_prio > best_cov_tf_prio:
                        best_cov = s
                        best_cov_tf_prio = tf_prio
                except Exception:
                    continue
            if best_cov:
                signals.append(best_cov)
        except Exception:
            pass

        # Priority 2: ACMTS (1h + 4h)
        try:
            k4h = get_klines(sym, "240")
            df_4h = klines_to_df(k4h[-200:])
            s = detect_acmts(df_1h, df_4h)
            if s:
                signals.append(s)
        except Exception:
            pass

        # Priority 1: ParkF (1h)
        try:
            s = detect_parkf(df_1h)
            if s:
                signals.append(s)
        except Exception:
            pass

        if not signals:
            continue

        # Conflict detection
        if len(signals) > 1:
            sides = set(s["side"] for s in signals)
            if len(sides) > 1:
                strats = [s["strategy"] for s in signals]
                print(f"  [{sym}] SKIP: conflicting signals {strats}")
                continue

        sig = pick_best_signal(signals)

        # Quality gate
        ok, reason = is_signal_ok(sig)
        if not ok:
            tf_show = sig.get("_tf_label", TF_MAP.get(sig["strategy"], "1h"))
            print(f"  [{sym}] SKIP signal ({sig['strategy']} {tf_show}): {reason}")
            continue

        # Live price validation
        try:
            current_price = float(k1h[-1]["close"])
        except Exception:
            continue

        if sig["side"] == "LONG":
            if current_price <= sig["sl"]:
                print(f"  [{sym}] SKIP: price hit SL")
                continue
            if current_price >= sig["tp"]:
                print(f"  [{sym}] SKIP: price passed TP")
                continue
        else:
            if current_price >= sig["sl"]:
                print(f"  [{sym}] SKIP: price hit SL")
                continue
            if current_price <= sig["tp"]:
                print(f"  [{sym}] SKIP: price passed TP")
                continue

        min_dist = current_price * 0.003
        if abs(current_price - sig["sl"]) < min_dist or abs(current_price - sig["tp"]) < min_dist:
            print(f"  [{sym}] SKIP: SL/TP too close to current price")
            continue

        # Sizing
        risk_amount = balance_usd * RISK_PCT_PER_TRADE
        sl_frac = sig["sl_pct"] / 100.0
        if sl_frac <= 0:
            continue
        notional_raw = risk_amount / sl_frac
        notional = min(notional_raw, max_notional)
        margin = max(notional / LEVERAGE, 1.0)
        coid = f"{sig['strategy']}-{sym}-{int(now.timestamp())}"

        sl_r = round_price(sig["sl"], sig["entry"])
        tp_r = round_price(sig["tp"], sig["entry"])

        try:
            resp = open_position(sym, sig["side"].lower(), margin, LEVERAGE, sl_r, tp_r, coid)
        except Exception as e:
            print(f"  [{sym}] network error: {e}")
            continue

        if resp is None:
            continue
        if resp.get("idempotent"):
            continue

        tf_saved = sig.get("_tf_label", TF_MAP.get(sig["strategy"], "1h"))
        log["open_entries"][sym] = {
            "strategy": sig["strategy"], "side": sig["side"], "entry": sig["entry"],
            "sl": sig["sl"], "tp": sig["tp"], "margin": margin, "tf": tf_saved,
        }
        new_trades.append((sym, sig, margin))

        emoji = "🟢" if sig["side"] == "LONG" else "🔴"
        capped = " [capped]" if notional < notional_raw else ""
        rr = sig["tp_pct"] / sig["sl_pct"] if sig["sl_pct"] > 0 else 0
        print(f"  {emoji} [{sig['strategy']} {tf_saved}] {sig['side']} {sym} @ {fmt_price(sig['entry'])} "
              f"| R/R {rr:.2f} | Margin: ${margin:.2f}{capped}")

    save_log(log)

    # Telegram
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
        if send_telegram("\n\n".join(msg_blocks)):
            print(f"  [telegram] sent")

    print(f"\nResult: {len(new_trades)} new | {len(newly_closed)} closed")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(run_scan())
