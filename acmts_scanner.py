#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Binance Futures Testnet — Triple-Strategy Scanner
==================================================
Donchian (multi-TF) + DailyMACD (4h/1d) + C_BE_trail (30m SHORT)

ENV:
  BINANCE_API_KEY, BINANCE_API_SECRET   (required)
  BINANCE_TESTNET=1                     (1=testnet, 0=live)
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID      (optional)
  DRY_RUN=1                             (detect only)
  VERBOSE=1
  INTERVAL_MIN=5                        (0=single run)
  MIN_NOTIONAL_USD=5                    (skip orders below)
  DAILY_PAPER_MODE=false                (default false for testnet)
"""

import os, sys, time, hmac, hashlib, json, csv
import urllib.parse
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
from numpy.lib.stride_tricks import sliding_window_view

# ============================================================
# CONFIG
# ============================================================
API_KEY    = os.environ.get("BINANCE_API_KEY", "").strip()
API_SECRET = os.environ.get("BINANCE_API_SECRET", "").strip()
if not API_KEY or not API_SECRET:
    print("FATAL: BINANCE_API_KEY / BINANCE_API_SECRET required")
    sys.exit(1)

TESTNET = os.environ.get("BINANCE_TESTNET", "1") == "1"
BASE = "https://testnet.binancefuture.com" if TESTNET else "https://fapi.binance.com"

TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TG_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

VERBOSE     = os.environ.get("VERBOSE", "0") == "1"
DRY_RUN     = os.environ.get("DRY_RUN", "0") == "1"
INTERVAL_MIN = int(os.environ.get("INTERVAL_MIN", "0"))
MIN_NOTIONAL_USD = float(os.environ.get("MIN_NOTIONAL_USD", "5.0"))

DAILY_PAPER_MODE = os.environ.get("DAILY_PAPER_MODE", "false").lower() == "true"
DAILY_EXIT_MODE  = os.environ.get("DAILY_EXIT_MODE", "macd").lower()

LEVERAGE = 10

# ============================================================
# PROFILES
# ============================================================
PROFILES = {
    "conservative": {"name": "🛡️", "target_vol": 0.20, "size_min": 0.10, "size_max": 0.60},
    "balanced":     {"name": "⭐", "target_vol": 0.30, "size_min": 0.10, "size_max": 0.75},
    "aggressive":   {"name": "🚀", "target_vol": 0.40, "size_min": 0.10, "size_max": 0.90},
}
def _get_profile(env_key, default="conservative"):
    key = os.environ.get(env_key, default).strip()
    return key, PROFILES.get(key, PROFILES["conservative"])

DAILY_PROFILE_KEY, DAILY_PROFILE = _get_profile("DAILY_PROFILE")
DONCHIAN_PROFILE_KEY, DONCHIAN_PROFILE = _get_profile("DONCHIAN_PROFILE")
CBT_PROFILE_KEY, CBT_PROFILE = _get_profile("CBT_PROFILE")

VOL_FLOOR, VOL_CEIL = 0.15, 0.80
DAILY_COOLDOWN_HOURS = 30
CBT_COOLDOWN_HOURS   = 15

# ============================================================
# SYMBOLS (without USDT suffix — added automatically)
# ============================================================
SYMBOLS = [
    "ADA", "AVAX", "LINK", "LTC",
    "ENA", "ATOM", "NEAR", "ARB", "OP", "PEPE", "SHIB", "SUI",
    "APT", "INJ", "TIA", "SEI", "WIF", "LDO", "UNI", "AAVE", "FIL", "RENDER",
]
DONCHIAN_SYMBOLS = SYMBOLS
DAILY_SYMBOLS    = SYMBOLS
CBT_SYMBOLS      = SYMBOLS

QUOTE = "USDT"
def bnsym(sym): return f"{sym}{QUOTE}"
def baseof(bnsym_str): return bnsym_str.replace(QUOTE, "")

# ============================================================
# PARAMS
# ============================================================
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

CBT_PVT = 50; CBT_TL = 2; CBT_SR_EX = 3
CBT_KIJUN_LEN = 36; CBT_ATR_LEN = 14; CBT_ATR_BASE = 250
CBT_ATR_THR = 0.90
CBT_QQE_RP = 14; CBT_QQE_SM = 5; CBT_QQE_F = 3.0
CBT_SL_MULT = 2.5; CBT_TP_RR = 2.5
CBT_BE_AT_R = 1.0; CBT_TRAIL_R = 1.5; CBT_TRAIL_ATR = 1.2
CBT_MAX_BARS = 40
CBT_MIN_BARS = 500
CBT_MIN_LOOKBACK = max(CBT_KIJUN_LEN, CBT_ATR_BASE, CBT_PVT * 4 + 20) + 5

# Binance Futures intervals
SCAN_TIMEFRAMES = [
    ("5m",  ["5m"],   1, 365 * 24 * 12),
    ("15m", ["15m"],  2, 365 * 24 * 4),
    ("30m", ["30m"],  3, 365 * 24 * 2),
    ("1h",  ["1h"],   4, 365 * 24),
    ("4h",  ["4h"],   5, 365 * 6),
]
DAILY_MACD_TIMEFRAMES = [
    ("4h", ["4h"],   5, 365 * 6),
    ("1d", ["1d"],   6, 365),
]

MAX_DONCHIAN_POSITIONS = 3
MAX_DAILY_POSITIONS    = 5
MAX_CBT_POSITIONS      = 3
MAX_TOTAL_POSITIONS = MAX_DONCHIAN_POSITIONS + MAX_DAILY_POSITIONS + MAX_CBT_POSITIONS

MAX_NOTIONAL_PCT = 0.75
LOG_FILE    = Path("binance_trades_log.json")

KILL_CONSEC_LOSSES, KILL_CONSEC_LOSS_SUM = 3, -15.0
KILL_MAX_DRAWDOWN,  KILL_SILENCE_DAYS     = -20.0, 10

FILTERS_DONCHIAN = {"min_sl": 0.5, "min_tp": 0.3, "min_rr": 0.5}
FILTERS_DAILY    = {"min_sl": 0.5, "min_tp": 1.0, "min_rr": 1.5}
FILTERS_CBT      = {"min_sl": 0.5, "min_tp": 1.0, "min_rr": 1.5}

# ============================================================
# BINANCE API CLIENT
# ============================================================
_TIME_OFFSET = 0
_EXINFO = None
_KLINES_CACHE = {}

def _sync_time():
    global _TIME_OFFSET
    try:
        r = requests.get(f"{BASE}/fapi/v1/time", timeout=10)
        r.raise_for_status()
        server_ms = int(r.json()["serverTime"])
        _TIME_OFFSET = server_ms - int(time.time() * 1000)
        if VERBOSE:
            print(f"  [time] offset: {_TIME_OFFSET}ms")
    except Exception as e:
        print(f"  [time] sync failed: {e}")

def _sign(params):
    params = dict(params)
    params["timestamp"] = int(time.time() * 1000) + _TIME_OFFSET
    params["recvWindow"] = 10000
    query = urllib.parse.urlencode(params, doseq=True)
    sig = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query + "&signature=" + sig

def _signed_request(method, path, params=None):
    params = params or {}
    qs = _sign(params)
    url = f"{BASE}{path}?{qs}"
    headers = {"X-MBX-APIKEY": API_KEY}
    if method == "GET":
        r = requests.get(url, headers=headers, timeout=20)
    elif method == "POST":
        r = requests.post(url, headers=headers, timeout=20)
    elif method == "DELETE":
        r = requests.delete(url, headers=headers, timeout=20)
    else:
        raise ValueError(f"bad method {method}")
    return r

def _public_get(path, params=None):
    r = requests.get(f"{BASE}{path}", params=params or {}, timeout=20)
    return r

def get_exchange_info():
    global _EXINFO
    if _EXINFO is not None: return _EXINFO
    r = _public_get("/fapi/v1/exchangeInfo")
    r.raise_for_status()
    _EXINFO = {}
    for s in r.json().get("symbols", []):
        f = {x["filterType"]: x for x in s.get("filters", [])}
        _EXINFO[s["symbol"]] = {
            "stepSize":    float(f.get("LOT_SIZE", {}).get("stepSize", "0.001")),
            "minQty":      float(f.get("LOT_SIZE", {}).get("minQty", "0.001")),
            "tickSize":    float(f.get("PRICE_FILTER", {}).get("tickSize", "0.00001")),
            "minNotional": float(f.get("MIN_NOTIONAL", {}).get("notional", "5.0")),
            "pricePrec":   int(f.get("PRICE_FILTER", {}).get("tickSize", "0.00001").count("0") - 1)
                            if "." in f.get("PRICE_FILTER", {}).get("tickSize", "0.00001") else 0,
        }
    return _EXINFO

def has_symbol(sym):
    return bnsym(sym) in get_exchange_info()

def get_klines(sym, interval, limit=500):
    """Returns pandas-friendly list of dicts {time, open, high, low, close, volume}"""
    key = (sym, interval, limit)
    if key in _KLINES_CACHE: return _KLINES_CACHE[key]
    try:
        r = _public_get("/fapi/v1/klines",
                        {"symbol": bnsym(sym), "interval": interval, "limit": limit})
        r.raise_for_status()
        raw = r.json()
        data = [
            {
                "time": int(k[0] // 1000),
                "open": float(k[1]),
                "high": float(k[2]),
                "low":  float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
            for k in raw
        ]
        _KLINES_CACHE[key] = data
        return data
    except Exception as e:
        if VERBOSE: print(f"  [klines] {sym} {interval}: {e}")
        _KLINES_CACHE[key] = None
        return None

def get_klines_fallback(sym, intervals):
    for iv in intervals:
        k = get_klines(sym, iv)
        if k and len(k) >= 50: return k
    return None

def get_balance():
    r = _signed_request("GET", "/fapi/v2/account")
    r.raise_for_status()
    return r.json()

def get_positions():
    """Returns dict: {'ADA': {symbol, side, qty, entry, mark, ...}, ...}"""
    r = _signed_request("GET", "/fapi/v2/positionRisk")
    r.raise_for_status()
    out = {}
    for p in r.json():
        amt = float(p.get("positionAmt", 0))
        if abs(amt) < 1e-9: continue
        base = baseof(p["symbol"])
        out[base] = {
            "symbol": p["symbol"],
            "side": "LONG" if amt > 0 else "SHORT",
            "qty": abs(amt),
            "entry": float(p.get("entryPrice", 0)),
            "mark": float(p.get("markPrice", 0)),
            "upnl": float(p.get("unRealizedProfit", 0)),
            "leverage": int(p.get("leverage", LEVERAGE)),
        }
    return out

def set_leverage(sym, lev=LEVERAGE):
    try:
        r = _signed_request("POST", "/fapi/v1/leverage",
                            {"symbol": bnsym(sym), "leverage": lev})
        return r.status_code == 200
    except Exception as e:
        if VERBOSE: print(f"  [leverage] {sym}: {e}")
        return False

def place_market_order(sym, side, quantity):
    """side: 'BUY' or 'SELL'"""
    params = {
        "symbol": bnsym(sym), "side": side, "type": "MARKET",
        "quantity": f"{quantity:.8f}".rstrip("0").rstrip("."),
    }
    r = _signed_request("POST", "/fapi/v1/order", params)
    if r.status_code != 200:
        print(f"  [{sym}] market order HTTP {r.status_code}: {r.text[:300]}")
        return None
    return r.json()

def place_stop_order(sym, side, stop_price):
    """side: BUY/SELL opposite of position. Uses closePosition=true."""
    info = get_exchange_info().get(bnsym(sym))
    if not info: return None
    tp_prec = info["pricePrec"]
    sp = f"{stop_price:.{tp_prec}f}"
    params = {
        "symbol": bnsym(sym), "side": side, "type": "STOP_MARKET",
        "stopPrice": sp, "closePosition": "true",
        "workingType": "MARK_PRICE",
    }
    r = _signed_request("POST", "/fapi/v1/order", params)
    if r.status_code != 200:
        print(f"  [{sym}] SL HTTP {r.status_code}: {r.text[:200]}")
        return None
    return r.json()

def place_tp_order(sym, side, stop_price):
    info = get_exchange_info().get(bnsym(sym))
    if not info: return None
    tp_prec = info["pricePrec"]
    sp = f"{stop_price:.{tp_prec}f}"
    params = {
        "symbol": bnsym(sym), "side": side, "type": "TAKE_PROFIT_MARKET",
        "stopPrice": sp, "closePosition": "true",
        "workingType": "MARK_PRICE",
    }
    r = _signed_request("POST", "/fapi/v1/order", params)
    if r.status_code != 200:
        print(f"  [{sym}] TP HTTP {r.status_code}: {r.text[:200]}")
        return None
    return r.json()

def cancel_all_orders(sym):
    try:
        r = _signed_request("DELETE", "/fapi/v1/allOpenOrders",
                            {"symbol": bnsym(sym)})
        return r.status_code == 200
    except Exception:
        return False

def close_position(sym, qty, side):
    """side = current position side ('LONG'/'SHORT'). Places opposite market order."""
    close_side = "SELL" if side == "LONG" else "BUY"
    info = get_exchange_info().get(bnsym(sym))
    step = info["stepSize"] if info else 0.001
    q = _round_step(qty, step)
    if q <= 0: return None
    params = {
        "symbol": bnsym(sym), "side": close_side, "type": "MARKET",
        "quantity": f"{q:.8f}".rstrip("0").rstrip("."),
        "reduceOnly": "true",
    }
    r = _signed_request("POST", "/fapi/v1/order", params)
    if r.status_code != 200:
        print(f"  [{sym}] close HTTP {r.status_code}: {r.text[:200]}")
        return None
    cancel_all_orders(sym)
    return r.json()

def _round_step(qty, step):
    if step <= 0: return qty
    return (qty // step) * step

def _fmt_qty(qty, step):
    if step >= 1: return str(int(qty))
    d = max(0, -int(round(np.log10(step))))
    return f"{qty:.{d}f}"

def compute_quantity(sym, notional_usd, price):
    info = get_exchange_info().get(bnsym(sym))
    if not info: return None, "symbol not in exchangeInfo"
    if price <= 0: return None, "invalid price"
    qty_raw = notional_usd / price
    step = info["stepSize"]
    qty = _round_step(qty_raw, step)
    qty = float(_fmt_qty(qty, step))
    if qty < info["minQty"]:
        return None, f"qty {qty} < minQty {info['minQty']}"
    if qty * price < info["minNotional"]:
        return None, f"notional {qty*price:.2f} < minNotional {info['minNotional']}"
    if qty * price < MIN_NOTIONAL_USD:
        return None, f"notional {qty*price:.2f} < MIN_NOTIONAL_USD {MIN_NOTIONAL_USD}"
    return qty, ""

# ============================================================
# HELPERS
# ============================================================
def fmt_price(p):
    if p >= 1000:   return f"{p:.2f}"
    if p >= 1:      return f"{p:.4f}"
    if p >= 0.01:   return f"{p:.6f}"
    if p >= 0.0001: return f"{p:.8f}"
    return f"{p:.10f}".rstrip('0')

def is_signal_ok(sig, filters):
    if sig["sl_pct"] < filters["min_sl"]: return False, f"SL tight ({sig['sl_pct']:.2f}%)"
    if sig["tp_pct"] < filters["min_tp"]: return False, f"TP tight ({sig['tp_pct']:.2f}%)"
    rr = sig["tp_pct"] / sig["sl_pct"]
    if rr < filters["min_rr"]: return False, f"R/R low ({rr:.2f})"
    return True, ""

def compute_dynamic_size(vol30, profile):
    if vol30 is None or np.isnan(vol30) or vol30 <= 0:
        return profile["target_vol"]
    clamped = max(VOL_FLOOR, min(VOL_CEIL, vol30))
    dyn = profile["target_vol"] / clamped
    return max(profile["size_min"], min(profile["size_max"], dyn))

def hours_since_last_entry(log, key, bar_ts):
    last = log.get("last_entry_bar", {}).get(key)
    if last is None: return 999999
    return (bar_ts - last) / 3600

def record_entry_bar(log, key, bar_ts):
    log.setdefault("last_entry_bar", {})[key] = int(bar_ts)

def log_key_sym(key):
    if not key: return ""
    for pref in ("Donchian:", "CBT:"):
        if key.startswith(pref): return key.split(":", 1)[1]
    if key.startswith("PAPER:"):
        parts = key.split(":")
        return parts[1] if len(parts) >= 2 else key
    if ":" in key: return key.split(":", 1)[0]
    return key

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
                print(f"  [telegram] HTTP {r.status_code}")
                ok = False
        except Exception as e:
            print(f"  [telegram] {e}")
            ok = False
    return ok

def format_trade(sym, sig, qty, notional, balance):
    emoji = "🟢" if sig["side"] == "LONG" else "🔴"
    rr = sig["tp_pct"] / sig["sl_pct"] if sig["sl_pct"] > 0 else 0
    tf = sig.get("_tf_label", "?")
    return (
        f"{emoji} <b>{sig['strategy']}: {sym} {sig['side']}</b>\n"
        f"📊 <b>{tf}</b> | R/R {rr:.2f}\n\n"
        f"📍 ورود: <code>{fmt_price(sig['entry'])}</code>\n"
        f"🛑 SL: <code>{fmt_price(sig['sl'])}</code> ({sig['sl_pct']:.2f}%)\n"
        f"🎯 TP: <code>{fmt_price(sig['tp'])}</code> ({sig['tp_pct']:.2f}%)\n"
        f"📦 مقدار: <b>{qty}</b> ({notional:.2f}$)\n"
        f"🏦 موجودی: ${balance:,.2f}\n"
    )

def format_closed(t):
    emoji = "✅" if t["pnl_pct"] > 0 else "❌"
    return (
        f"{emoji} <b>{t['strategy']}: {t['symbol']} {t['side']} بسته</b>\n"
        f"📍 {fmt_price(t['entry'])} → {fmt_price(t['exit'])}\n"
        f"📊 <b>{t['pnl_pct']:+.2f}%</b>  {t.get('close_reason','')}\n"
    )

def format_summary(log, strategy, title):
    closed = [t for t in log.get("closed_trades", []) if t.get("strategy") == strategy]
    if not closed: return f"<i>{title}: بدون معامله</i>"
    df = pd.DataFrame(closed)
    total = len(df); wins = len(df[df["pnl_pct"] > 0])
    return (
        f"<b>{title}</b>: {total} (✅{wins}/{total-wins}) | "
        f"WR {wins/total*100:.0f}% | Σ {df['pnl_pct'].sum():+.2f}%"
    )

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

def _atr_fn(h, l, c, p=14):
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
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

# ============================================================
# STRATEGY DETECTORS
# ============================================================
def detect_donchian_signals(df, tf_label, bars_per_year=365 * 24):
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
            sigs.append({"action": "EXIT_LONG", "side": "LONG", "entry": close_v, "_tf_label": tf_label})
        if close_v > float(don_hi_exit.iloc[i]):
            sigs.append({"action": "EXIT_SHORT", "side": "SHORT", "entry": close_v, "_tf_label": tf_label})
        if close_v > float(don_hi_entry.iloc[i]) and vol_ok and close_v > ema_v:
            sl_p = close_v - P["atr_mult"] * atr_val
            sl_d = close_v - sl_p
            if sl_d > 0:
                tp_p = close_v + P["tp_rr"] * sl_d
                slp = sl_d / close_v * 100; tpp = (tp_p - close_v) / close_v * 100
                if 0 < slp < 25:
                    sigs.append({"action": "ENTRY_LONG", "side": "LONG", "strategy": "Donchian",
                                 "entry": close_v, "sl": sl_p, "tp": tp_p,
                                 "sl_pct": slp, "tp_pct": tpp,
                                 "_tf_label": tf_label, "_bar_ts": bar_ts, "_vol30": vv})
        if close_v < float(don_lo_entry.iloc[i]) and vol_ok and close_v < ema_v:
            sl_p = close_v + P["atr_mult"] * atr_val
            sl_d = sl_p - close_v
            if sl_d > 0:
                tp_p = close_v - P["tp_rr"] * sl_d
                slp = sl_d / close_v * 100; tpp = (close_v - tp_p) / close_v * 100
                if 0 < slp < 25:
                    sigs.append({"action": "ENTRY_SHORT", "side": "SHORT", "strategy": "Donchian",
                                 "entry": close_v, "sl": sl_p, "tp": tp_p,
                                 "sl_pct": slp, "tp_pct": tpp,
                                 "_tf_label": tf_label, "_bar_ts": bar_ts, "_vol30": vv})
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
        out.append({"action": "EXIT_SHORT", "side": "SHORT", "entry": entry, "_tf_label": tf_label})
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
        out.append({"action": "EXIT_LONG", "side": "LONG", "entry": entry, "_tf_label": tf_label})
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

def detect_c_be_trail_signals(df):
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
        if i < CBT_MIN_LOOKBACK: continue
        if pd.isna(ratio[i]) or pd.isna(kijun[i]): continue
        if not (tsr[i] and ratio[i] >= CBT_ATR_THR and c[i] < kijun[i] and q_tr[i] == -1):
            continue
        entry = float(c[i]); av = float(a[i])
        if av <= 0 or entry <= 0: continue
        sl = entry + CBT_SL_MULT * av
        risk = sl - entry
        tp = entry - CBT_TP_RR * risk
        slp = (sl - entry) / entry * 100; tpp = (entry - tp) / entry * 100
        if not (0 < slp < 20): continue
        try: bar_ts = int(pd.Timestamp(df.index[i]).timestamp())
        except Exception: bar_ts = None
        vv = float(vol30.iloc[i]) if not pd.isna(vol30.iloc[i]) else 0.30
        return [{"action": "ENTRY_SHORT", "side": "SHORT", "strategy": "CBT",
                 "entry": entry, "sl": sl, "tp": tp,
                 "sl_pct": slp, "tp_pct": tpp,
                 "_tf_label": "30m", "_bar_ts": bar_ts,
                 "_atr": av, "_risk": risk, "_vol30": vv}]
    return []

def fetch_timeframes(sym, min_bars, bars=500):
    out = {}
    for tf_label, intervals, _, _ in SCAN_TIMEFRAMES:
        try:
            k = get_klines_fallback(sym, intervals)
            out[tf_label] = klines_to_df(k[-bars:]) if (k and len(k) >= min_bars) else None
        except Exception:
            out[tf_label] = None
    return out

def fetch_daily_tf(sym, tf_label):
    intervals = ["4h"]; bpy = 365 * 6
    for lbl, iv, _, b in DAILY_MACD_TIMEFRAMES:
        if lbl == tf_label: intervals = iv; bpy = b; break
    for iv in intervals:
        k = get_klines(sym, iv)
        if k and len(k) >= DAILY_MACD_PARAMS["min_bars"]:
            return klines_to_df(k[-300:]), bpy
    return None, bpy

def _current_price(sym, interval="1h", fallback=None):
    k = get_klines(sym, interval)
    if k: return float(k[-1]["close"])
    return fallback

# ============================================================
# KILL SWITCH
# ============================================================
def check_kill_switch(log):
    closed = log.get("closed_trades", [])
    dm = [t for t in closed if t.get("strategy") == "DailyMACD"]
    if len(dm) >= KILL_CONSEC_LOSSES:
        last_n = dm[-KILL_CONSEC_LOSSES:]
        if all(t["pnl_pct"] < 0 for t in last_n):
            total = sum(t["pnl_pct"] for t in last_n)
            if total < KILL_CONSEC_LOSS_SUM:
                return True, f"🛑 {KILL_CONSEC_LOSSES} ضرر متوالی DailyMACD ({total:+.1f}%)"
    if len(dm) >= 10:
        pnls = [t["pnl_pct"] for t in dm]
        eq = np.cumsum(pnls); peak = np.maximum.accumulate(eq)
        dd = float((eq - peak).min())
        if dd < KILL_MAX_DRAWDOWN:
            return True, f"🛑 DailyMACD drawdown {dd:.1f}%"
    return False, ""

# ============================================================
# MAIN
# ============================================================
def run_scan():
    _KLINES_CACHE.clear()
    _sync_time()
    now = datetime.now(timezone.utc)

    print("=" * 72)
    print(f"Binance {'TESTNET' if TESTNET else 'LIVE'} — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  DRY_RUN={DRY_RUN} | VERBOSE={VERBOSE} | Loop={INTERVAL_MIN}min")
    print(f"  Telegram: {'✅' if TG_CHAT else '❌'}")
    print(f"  Leverage: {LEVERAGE}× | MinNotional: ${MIN_NOTIONAL_USD}")
    print("=" * 72)

    log = load_log()

    kill_on, kill_reason = check_kill_switch(log)
    if kill_on:
        print(f"\n{kill_reason}")
        if log.get("kill_switch") is None:
            log["kill_switch"] = kill_reason; save_log(log)
            send_telegram(f"🛑 KILL\n{kill_reason}")
    else:
        if log.get("kill_switch"):
            log["kill_switch"] = None; save_log(log)

    # Balance
    try:
        acc = get_balance()
        balance_usd = float(acc.get("availableBalance", 0))
        total_usd = float(acc.get("totalWalletBalance", 0))
        print(f"Balance: available=${balance_usd:,.2f} | total=${total_usd:,.2f}")
        if balance_usd < 10:
            print(f"⚠️  ⚠️  Low available balance. Testnet may need funding.")
            print(f"    Get testnet funds: https://testnet.binancefuture.com → Faucet")
    except Exception as e:
        print(f"FATAL balance: {e}"); return 1

    # Positions
    try:
        open_positions = get_positions()
        print(f"Open: {len(open_positions)} {list(open_positions.keys())}")
    except Exception as e:
        print(f"FATAL positions: {e}"); return 1

    newly_closed, new_trades = [], []

    def count_strat(s): return sum(1 for v in log["open_entries"].values() if v.get("strategy") == s)

    # ============ Detect EXTERNALLY CLOSED positions ============
    for log_key in list(log["open_entries"].keys()):
        info = log["open_entries"].get(log_key)
        if not info or info.get("paper"): continue
        sym = info.get("symbol") or log_key_sym(log_key)
        if not sym: continue
        if sym not in open_positions:
            # Closed on exchange (SL/TP hit or manual)
            price = _current_price(sym, "1h", fallback=info.get("entry", 0))
            ep = info["entry"]; side = info["side"]
            pnl = ((price - ep) if side == "LONG" else (ep - price)) / ep * 100 if ep > 0 else 0
            log["open_entries"].pop(log_key)
            rec = {"symbol": sym, "strategy": info.get("strategy", "?"), "side": side,
                   "entry": ep, "exit": price, "pnl_pct": round(pnl, 2),
                   "tf": info.get("tf", "?"), "close_reason": "SL/TP (exchange)", "paper": False}
            log["closed_trades"].append(rec); newly_closed.append(rec)
            print(f"  [CLOSED-EXCH] {info.get('strategy')} {sym} {side} → {pnl:+.2f}%")
            cancel_all_orders(sym)

    # ============ Strategy EXITS (Donchian / DailyMACD / CBT) ============
    # Donchian exit
    for log_key in list(log["open_entries"].keys()):
        info = log["open_entries"].get(log_key)
        if not info or info.get("strategy") != "Donchian": continue
        sym = info.get("symbol") or log_key_sym(log_key)
        if sym not in open_positions: continue
        pos_side = info.get("side", "LONG"); entry_tf = info.get("tf", "1h")
        try:
            ivs = ["1h"]; bpy = 365 * 24
            for lbl, iv, _, b in SCAN_TIMEFRAMES:
                if lbl == entry_tf: ivs = iv; bpy = b; break
            k = get_klines_fallback(sym, ivs)
            if not k or len(k) < DONCHIAN_PARAMS["min_bars"]: continue
            sigs = detect_donchian_signals(klines_to_df(k[-500:]), entry_tf, bpy)
            if not any(s["action"] == f"EXIT_{pos_side}" for s in sigs): continue
            pos = open_positions[sym]
            if DRY_RUN:
                print(f"  [DRY] would close Donchian {sym} {pos_side}")
                continue
            if not close_position(sym, pos["qty"], pos_side): continue
            price = _current_price(sym, "1h", fallback=info["entry"])
            ep = info["entry"]
            pnl = ((price - ep) if pos_side == "LONG" else (ep - price)) / ep * 100
            log["open_entries"].pop(log_key)
            rec = {"symbol": sym, "strategy": "Donchian", "side": pos_side,
                   "entry": ep, "exit": price, "pnl_pct": round(pnl, 2),
                   "tf": entry_tf, "close_reason": f"Donchian-EXIT", "paper": False}
            log["closed_trades"].append(rec); newly_closed.append(rec)
            open_positions.pop(sym, None)
            print(f"  [CLOSED] Donchian {sym} {pos_side} → {pnl:+.2f}%")
        except Exception as e:
            print(f"  [DN {sym}] exit error: {e}")

    # DailyMACD exit
    for log_key in list(log["open_entries"].keys()):
        info = log["open_entries"].get(log_key)
        if not info or info.get("strategy") != "DailyMACD": continue
        sym = info.get("symbol") or log_key_sym(log_key)
        if not sym: continue
        is_paper = info.get("paper", False)
        if not is_paper and sym not in open_positions: continue
        pos_side = info.get("side", "LONG"); entry_tf = info.get("tf", "1d")
        try:
            df_tf, bpy = fetch_daily_tf(sym, entry_tf)
            if df_tf is None or len(df_tf) < DAILY_MACD_PARAMS["min_bars"]: continue
            trig = False; reason = ""
            if DAILY_EXIT_MODE == "macd":
                sigs = detect_daily_macd_signals(df_tf, entry_tf, bpy)
                if any(s["action"] == f"EXIT_{pos_side}" for s in sigs):
                    trig = True; reason = f"MACD-EXIT"
            if not trig: continue
            price = float(df_tf["close"].iloc[-1]); ep = info["entry"]
            pnl = ((price - ep) if pos_side == "LONG" else (ep - price)) / ep * 100
            if is_paper:
                log["open_entries"].pop(log_key)
                rec = {"symbol": sym, "strategy": "DailyMACD", "side": pos_side,
                       "entry": ep, "exit": price, "pnl_pct": round(pnl, 2),
                       "tf": entry_tf, "close_reason": reason, "paper": True}
                log["closed_trades"].append(rec); newly_closed.append(rec)
                print(f"  [CLOSED-PAPER] DailyMACD {sym} → {pnl:+.2f}%")
            else:
                if DRY_RUN: continue
                pos = open_positions[sym]
                if not close_position(sym, pos["qty"], pos_side): continue
                log["open_entries"].pop(log_key)
                rec = {"symbol": sym, "strategy": "DailyMACD", "side": pos_side,
                       "entry": ep, "exit": price, "pnl_pct": round(pnl, 2),
                       "tf": entry_tf, "close_reason": reason, "paper": False}
                log["closed_trades"].append(rec); newly_closed.append(rec)
                open_positions.pop(sym, None)
                print(f"  [CLOSED] DailyMACD {sym} → {pnl:+.2f}%")
        except Exception as e:
            print(f"  [DM {sym}] exit error: {e}")

    # CBT BE/Trail/MaxBars
    for log_key in list(log["open_entries"].keys()):
        info = log["open_entries"].get(log_key)
        if not info or info.get("strategy") != "CBT": continue
        sym = info.get("symbol") or log_key_sym(log_key)
        if sym not in open_positions: continue
        try:
            k = get_klines(sym, "30m", limit=200)
            if not k: continue
            df30 = klines_to_df(k)
            cur_high = float(df30["high"].iloc[-1])
            cur_low  = float(df30["low"].iloc[-1])
            cur_close= float(df30["close"].iloc[-1])

            entry = info["entry"]; risk = info.get("risk") or abs(info["sl"] - entry)
            if risk <= 0: continue
            cur_sl = info.get("current_sl", info["sl"])
            tp = info["tp"]
            entry_ts = info.get("entry_bar_ts") or info.get("_bar_ts")
            bars_held = ((time.time() - entry_ts) / (30*60)) if entry_ts else 0

            exit_reason = None
            if bars_held >= CBT_MAX_BARS: exit_reason = f"MaxBars"
            elif cur_high >= cur_sl: exit_reason = f"SL@{fmt_price(cur_sl)}"
            elif cur_low <= tp: exit_reason = f"TP@{fmt_price(tp)}"

            if exit_reason is None:
                # Check BE / trail
                r_now = (entry - cur_low) / risk
                atr_now = float(compute_atr(df30)[-1]) if len(df30) > 20 else risk / CBT_SL_MULT
                be_done = info.get("be_done", False)
                trail_on = info.get("trail_on", False)
                new_sl = cur_sl
                if not be_done and r_now >= CBT_BE_AT_R:
                    new_sl = min(new_sl, entry); be_done = True
                if r_now >= CBT_TRAIL_R:
                    trail_on = True
                    trail_sl = cur_close + CBT_TRAIL_ATR * atr_now
                    new_sl = min(new_sl, trail_sl)

                if new_sl != cur_sl:
                    if VERBOSE:
                        print(f"  [CBT {sym}] updating SL {fmt_price(cur_sl)} → {fmt_price(new_sl)}")
                    if not DRY_RUN:
                        cancel_all_orders(sym)
                        place_stop_order(sym, "BUY", new_sl)   # SHORT: BUY stop above
                        place_tp_order(sym, "BUY", tp)
                    info["current_sl"] = new_sl
                    info["be_done"] = be_done
                    info["trail_on"] = trail_on
                    log["open_entries"][log_key] = info
                continue

            # Exit
            if DRY_RUN:
                print(f"  [DRY] would close CBT {sym} ({exit_reason})")
                continue
            pos = open_positions[sym]
            if not close_position(sym, pos["qty"], "SHORT"): continue
            pnl = ((entry - cur_close) / entry) * 100
            log["open_entries"].pop(log_key)
            rec = {"symbol": sym, "strategy": "CBT", "side": "SHORT",
                   "entry": entry, "exit": cur_close, "pnl_pct": round(pnl, 2),
                   "tf": "30m", "close_reason": f"CBT {exit_reason}", "paper": False}
            log["closed_trades"].append(rec); newly_closed.append(rec)
            open_positions.pop(sym, None)
            print(f"  [CLOSED] CBT {sym} → {pnl:+.2f}% ({exit_reason})")
        except Exception as e:
            print(f"  [CBT {sym}] exit error: {e}")

    # ============ NEW ENTRIES ============
    if kill_on:
        print("\n  ⛔ Kill switch — no new entries")
    else:
        newly_opened = set()
        info_exch = get_exchange_info()

        # Donchian
        for idx, sym in enumerate(DONCHIAN_SYMBOLS, 1):
            if sym in open_positions or sym in newly_opened: continue
            if bnsym(sym) not in info_exch:
                if VERBOSE: print(f"  [DN {sym}] not on exchange")
                continue
            if count_strat("Donchian") + len([t for t in new_trades if t[0]=="Donchian"]) >= MAX_DONCHIAN_POSITIONS: break
            if len(open_positions) + len(new_trades) >= MAX_TOTAL_POSITIONS: break

            if VERBOSE: print(f"  [DN {idx}] {sym}...", flush=True)
            price = _current_price(sym, "1h", None)
            if price is None: continue
            tf_data = fetch_timeframes(sym, DONCHIAN_PARAMS["min_bars"] + 50)
            all_e = []
            for tf_label, _, prio, bpy in SCAN_TIMEFRAMES:
                df_tf = tf_data.get(tf_label)
                if df_tf is None: continue
                try:
                    for s in detect_donchian_signals(df_tf, tf_label, bpy):
                        if s["action"] in ("ENTRY_LONG", "ENTRY_SHORT"):
                            s["_tf_prio"] = prio; all_e.append(s)
                except Exception: continue
            if not all_e: continue
            if len(set(s["side"] for s in all_e)) > 1: continue
            all_e.sort(key=lambda x: x["_tf_prio"], reverse=True)
            sig = all_e[0]
            ok, reason = is_signal_ok(sig, FILTERS_DONCHIAN)
            if not ok:
                print(f"  [{sym}] DN skip: {reason}"); continue
            if sig["side"] == "LONG" and not (sig["sl"] < price < sig["tp"]): continue
            if sig["side"] == "SHORT" and not (sig["tp"] < price < sig["sl"]): continue

            vol30 = sig.get("_vol30", 0.30)
            if pd.isna(vol30): vol30 = 0.30
            size_frac = compute_dynamic_size(vol30, DONCHIAN_PROFILE)
            notional = min(balance_usd * size_frac * LEVERAGE, balance_usd * MAX_NOTIONAL_PCT * LEVERAGE)
            qty, qerr = compute_quantity(sym, notional, sig["entry"])
            if qty is None:
                print(f"  [{sym}] DN skip qty: {qerr}"); continue

            # Place orders
            if DRY_RUN:
                print(f"  [DRY] Donchian {sig['side']} {sym} @ {fmt_price(sig['entry'])} qty={qty}")
                new_trades.append(("Donchian", sym, sig, qty, notional))
                continue

            set_leverage(sym, LEVERAGE)
            side = "BUY" if sig["side"] == "LONG" else "SELL"
            resp = place_market_order(sym, side, qty)
            if not resp:
                print(f"  [{sym}] DN market order failed"); continue
            # SL/TP
            close_side = "SELL" if sig["side"] == "LONG" else "BUY"
            place_stop_order(sym, close_side, sig["sl"])
            place_tp_order(sym, close_side, sig["tp"])

            log["open_entries"][f"Donchian:{sym}"] = {
                "symbol": sym, "strategy": "Donchian", "side": sig["side"],
                "entry": sig["entry"], "sl": sig["sl"], "tp": sig["tp"],
                "qty": qty, "notional": notional, "tf": sig["_tf_label"],
                "_bar_ts": sig.get("_bar_ts"), "paper": False,
            }
            new_trades.append(("Donchian", sym, sig, qty, notional))
            newly_opened.add(sym)
            print(f"  🟢 [Donchian {sig['_tf_label']}] {sig['side']} {sym} @ {fmt_price(sig['entry'])} qty={qty}")

        # DailyMACD
        for idx, sym in enumerate(DAILY_SYMBOLS, 1):
            if not DAILY_PAPER_MODE and (sym in open_positions or sym in newly_opened): continue
            if DAILY_PAPER_MODE and any(k.startswith(f"PAPER:{sym}:") for k in log["open_entries"]): continue
            if bnsym(sym) not in info_exch and not DAILY_PAPER_MODE:
                continue
            if count_strat("DailyMACD") + len([t for t in new_trades if t[0]=="DailyMACD"]) >= MAX_DAILY_POSITIONS: break
            if len(open_positions) + len(new_trades) >= MAX_TOTAL_POSITIONS: break

            if VERBOSE: print(f"  [DM {idx}] {sym}...", flush=True)
            sigs_by_side = {"LONG": [], "SHORT": []}
            for tf_label, iv, prio, bpy in DAILY_MACD_TIMEFRAMES:
                try:
                    df_tf, _ = fetch_daily_tf(sym, tf_label)
                    if df_tf is None: continue
                    for s in detect_daily_macd_signals(df_tf, tf_label, bpy):
                        if s["action"].startswith("ENTRY_"):
                            s["_tf_prio"] = prio; s["_tf_label"] = tf_label
                            sigs_by_side[s["side"]].append(s)
                except Exception: continue

            for side in ("LONG", "SHORT"):
                if not sigs_by_side[side]: continue
                sigs_by_side[side].sort(key=lambda x: x["_tf_prio"], reverse=True)
                sig = sigs_by_side[side][0]
                tf_label = sig["_tf_label"]
                log_key = f"PAPER:{sym}:{side}" if DAILY_PAPER_MODE else f"{sym}:{side}"
                if log_key in log["open_entries"]: continue
                cd_key = f"DailyMACD:{sym}:{side}"
                bar_ts = sig.get("_bar_ts")
                if bar_ts and hours_since_last_entry(log, cd_key, bar_ts) < DAILY_COOLDOWN_HOURS: continue
                ok, reason = is_signal_ok(sig, FILTERS_DAILY)
                if not ok: continue

                try:
                    df_chk, _ = fetch_daily_tf(sym, tf_label)
                    if df_chk is None: continue
                    cur = float(df_chk["close"].iloc[-1])
                except Exception: continue
                if side == "LONG" and not (sig["sl"] < cur < sig["tp"]): continue
                if side == "SHORT" and not (sig["tp"] < cur < sig["sl"]): continue

                vol30 = sig.get("_vol30", 0.30)
                if pd.isna(vol30): vol30 = 0.30
                size_frac = compute_dynamic_size(vol30, DAILY_PROFILE)
                notional = min(balance_usd * size_frac * LEVERAGE, balance_usd * MAX_NOTIONAL_PCT * LEVERAGE)
                qty, qerr = compute_quantity(sym, notional, sig["entry"]) if not DAILY_PAPER_MODE else (0, "")

                if DAILY_PAPER_MODE:
                    sig["_paper"] = True
                    log["open_entries"][log_key] = {
                        "symbol": sym, "strategy": "DailyMACD", "side": side,
                        "entry": sig["entry"], "sl": sig["sl"], "tp": sig["tp"],
                        "qty": 0, "notional": notional, "tf": tf_label, "paper": True,
                    }
                    if bar_ts: record_entry_bar(log, cd_key, bar_ts)
                    new_trades.append(("DailyMACD", sym, sig, 0, notional))
                    print(f"  📝 [PAPER] DailyMACD {side} {sym} @ {fmt_price(sig['entry'])}")
                else:
                    if qty is None:
                        print(f"  [{sym}] DM skip qty: {qerr}"); continue
                    if DRY_RUN:
                        print(f"  [DRY] DailyMACD {side} {sym} @ {fmt_price(sig['entry'])} qty={qty}")
                        new_trades.append(("DailyMACD", sym, sig, qty, notional))
                        continue
                    set_leverage(sym, LEVERAGE)
                    order_side = "BUY" if side == "LONG" else "SELL"
                    resp = place_market_order(sym, order_side, qty)
                    if not resp: continue
                    close_side = "SELL" if side == "LONG" else "BUY"
                    place_stop_order(sym, close_side, sig["sl"])
                    place_tp_order(sym, close_side, sig["tp"])
                    sig["_paper"] = False
                    log["open_entries"][log_key] = {
                        "symbol": sym, "strategy": "DailyMACD", "side": side,
                        "entry": sig["entry"], "sl": sig["sl"], "tp": sig["tp"],
                        "qty": qty, "notional": notional, "tf": tf_label, "paper": False,
                    }
                    if bar_ts: record_entry_bar(log, cd_key, bar_ts)
                    new_trades.append(("DailyMACD", sym, sig, qty, notional))
                    newly_opened.add(sym)
                    print(f"  🟢 [DailyMACD {tf_label}] {side} {sym} @ {fmt_price(sig['entry'])} qty={qty}")

        # CBT
        for idx, sym in enumerate(CBT_SYMBOLS, 1):
            if sym in open_positions or sym in newly_opened: continue
            if f"CBT:{sym}" in log["open_entries"]: continue
            if bnsym(sym) not in info_exch: continue
            if count_strat("CBT") + len([t for t in new_trades if t[0]=="CBT"]) >= MAX_CBT_POSITIONS: break
            if len(open_positions) + len(new_trades) >= MAX_TOTAL_POSITIONS: break

            if VERBOSE: print(f"  [CBT {idx}] {sym}...", flush=True)
            k30 = get_klines(sym, "30m", limit=500)
            if not k30 or len(k30) < CBT_MIN_BARS:
                if VERBOSE: print(f"    only {len(k30) if k30 else 0}/{CBT_MIN_BARS} bars")
                continue
            df30 = klines_to_df(k30)
            try: sigs = detect_c_be_trail_signals(df30)
            except Exception as e:
                print(f"  [CBT {sym}] detect error: {e}"); continue
            if not sigs: continue
            sig = sigs[0]
            cd_key = f"CBT:{sym}"
            bar_ts = sig.get("_bar_ts")
            if bar_ts and hours_since_last_entry(log, cd_key, bar_ts) < CBT_COOLDOWN_HOURS: continue
            ok, reason = is_signal_ok(sig, FILTERS_CBT)
            if not ok: continue
            cur = _current_price(sym, "30m", None)
            if cur is None or not (sig["tp"] < cur < sig["sl"]): continue

            vol30 = sig.get("_vol30", 0.30)
            if pd.isna(vol30): vol30 = 0.30
            size_frac = compute_dynamic_size(vol30, CBT_PROFILE)
            notional = min(balance_usd * size_frac * LEVERAGE, balance_usd * MAX_NOTIONAL_PCT * LEVERAGE)
            qty, qerr = compute_quantity(sym, notional, sig["entry"])
            if qty is None:
                print(f"  [{sym}] CBT skip qty: {qerr}"); continue

            if DRY_RUN:
                print(f"  [DRY] CBT SHORT {sym} @ {fmt_price(sig['entry'])} qty={qty}")
                new_trades.append(("CBT", sym, sig, qty, notional))
                continue

            set_leverage(sym, LEVERAGE)
            resp = place_market_order(sym, "SELL", qty)
            if not resp: continue
            place_stop_order(sym, "BUY", sig["sl"])
            place_tp_order(sym, "BUY", sig["tp"])

            log["open_entries"][f"CBT:{sym}"] = {
                "symbol": sym, "strategy": "CBT", "side": "SHORT",
                "entry": sig["entry"], "sl": sig["sl"], "current_sl": sig["sl"],
                "tp": sig["tp"], "qty": qty, "notional": notional, "tf": "30m",
                "entry_bar_ts": bar_ts, "_bar_ts": bar_ts,
                "atr": sig.get("_atr"), "risk": sig.get("_risk"),
                "be_done": False, "trail_on": False, "paper": False,
            }
            if bar_ts: record_entry_bar(log, cd_key, bar_ts)
            new_trades.append(("CBT", sym, sig, qty, notional))
            newly_opened.add(sym)
            print(f"  🔴 [CBT 30m] SHORT {sym} @ {fmt_price(sig['entry'])} qty={qty}")

    save_log(log)

    # ============ TELEGRAM ============
    if new_trades or newly_closed:
        blocks = []
        if kill_on: blocks.append(f"🛑 KILL: {kill_reason}")
        for strat in ["Donchian", "DailyMACD", "CBT"]:
            trades = [t for t in new_trades if t[0] == strat]
            if trades:
                blocks.append(f"<b>🔔 {strat}</b>")
                for _, sym, sig, qty, notional in trades:
                    blocks.append(format_trade(sym, sig, qty, notional, balance_usd))
        if newly_closed:
            blocks.append(f"<b>🔒 Closed</b>")
            for t in newly_closed: blocks.append(format_closed(t))
        blocks.append(format_summary(log, "Donchian", "📊 DN"))
        blocks.append(format_summary(log, "DailyMACD", "📊 DM"))
        blocks.append(format_summary(log, "CBT", "📊 CBT"))
        if TG_CHAT:
            if send_telegram("\n\n".join(blocks)):
                print("  [telegram] sent ✓")

    print(f"\nResult: {len(new_trades)} new | {len(newly_closed)} closed"
          f"{' | KILL' if kill_on else ''}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    if INTERVAL_MIN <= 0:
        sys.exit(run_scan())
    print(f"Loop: every {INTERVAL_MIN} min")
    while True:
        try:
            run_scan()
        except KeyboardInterrupt:
            print("\nStopped"); sys.exit(0)
        except Exception as e:
            print(f"SCAN CRASH: {e}")
        try:
            time.sleep(INTERVAL_MIN * 60)
        except KeyboardInterrupt:
            print("\nStopped"); sys.exit(0)
