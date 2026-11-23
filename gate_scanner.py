#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gate.io Futures TestNet Scanner — ALL STRATEGIES
================================================
1. Original strategies (Supertrend / QQE / MACD / MA Cross ...)
2. VESPA_HMA (from previous strong backtests)
3. SNIPER_HMA (11 strongest coins from latest sweep)

هر استراتژی جداگانه سیگنال می‌دهد و در تلگرام کاملاً مشخص است.
"""

import os, sys, json, time, requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from gate_api import ApiClient, Configuration, FuturesApi
from gate_api.models import FuturesOrder, FuturesPriceTriggeredOrder, FuturesInitialOrder, FuturesPriceTrigger

API_KEY    = "b37ed23f61f92662e11ba8b247adfb3b"
API_SECRET = "39395a011e7d170e4b955c3060692ed5bfe3f380cd470717dbbc516b03a73fea"
GATE_HOST  = "https://api-testnet.gateapi.io/api/v4"

TG_TOKEN   = "8601458987:AAEvcxx2ClOTfQIqEp9Ok6TwcvKRaMfIeqo"
TG_CHAT_ID = "254500349"

def send_telegram(text):
    if not TG_TOKEN or not TG_CHAT_ID: return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10,
        )
    except Exception as e:
        print(f"  [TG] Error: {e}")

if "testnet" not in GATE_HOST.lower():
    print("❌ SAFETY: host is not testnet!"); sys.exit(99)

print("🧪 GATE.IO TESTNET — ALL STRATEGIES (Original + VESPA + SNIPER)")

# ============================================================
# ⚙️ PARAMETERS
# ============================================================
RISK_PCT       = 0.02
ATR_MULT_SL    = 2.5
ATR_MULT_TP    = 7.5
MAX_CONTRACTS  = 90_000
LEVERAGE       = 10
STATE_FILE     = "state.json"
COOLDOWN_FILE  = "cooldown.json"
COOLDOWN_HOURS = 4
MAX_MARGIN_PCT = 0.20

# ============================================================
# 1) ORIGINAL CONFIGS (Supertrend / QQE / MACD / MA Cross)
# ============================================================
ORIGINAL_CONFIGS = [
    {"symbol": "ADA/USDT", "timeframe": "1h",
     "entry_strategy": "Supertrend", "entry_params": {"period": 14, "mult": 4.0},
     "entry_filter_type": "VWMA", "entry_filter_period": 100,
     "exit_strategy": "Volume", "exit_params": {"vol_mult": 1.5, "ma_period": 30}},

    {"symbol": "OP/USDT", "timeframe": "1h",
     "entry_strategy": "Supertrend", "entry_params": {"period": 14, "mult": 5.0},
     "entry_filter_type": "TSI", "entry_filter_period": 25,
     "exit_strategy": "QQE", "exit_params": {"rsi_period": 14, "smooth": 7, "factor": 5.0},
     "exit_filter_type": "HMA", "exit_filter_period": 20},

    {"symbol": "COMP/USDT", "timeframe": "1h",
     "entry_strategy": "Supertrend", "entry_params": {"period": 14, "mult": 3.0},
     "entry_filter_type": "VWMA", "entry_filter_period": 100,
     "exit_strategy": "Supertrend", "exit_params": {"period": 14, "mult": 4.0}},

    {"symbol": "BCH/USDT", "timeframe": "1h",
     "entry_strategy": "QQE", "entry_params": {"rsi_period": 14, "smooth": 7, "factor": 5.0},
     "entry_filter_type": "VWMA", "entry_filter_period": 14,
     "exit_strategy": "Supertrend", "exit_params": {"period": 14, "mult": 3.0},
     "exit_filter_type": "TSI", "exit_filter_period": 25},

    {"symbol": "LINK/USDT", "timeframe": "1h",
     "entry_strategy": "DEMA_CROSS", "entry_params": {"fast": 8, "slow": 21},
     "exit_strategy": "TMA_CROSS", "exit_params": {"fast": 6, "slow": 19}},

    {"symbol": "SOL/USDT", "timeframe": "1h",
     "entry_strategy": "Supertrend", "entry_params": {"period": 14, "mult": 3.0},
     "entry_filter_type": "VWMA", "entry_filter_period": 50,
     "exit_strategy": "Supertrend", "exit_params": {"period": 20, "mult": 5.0}},

    {"symbol": "DOGE/USDT", "timeframe": "1h",
     "entry_strategy": "TMA_CROSS", "entry_params": {"fast": 2, "slow": 5},
     "entry_filter_type": "VWMA", "entry_filter_period": 7,
     "exit_strategy": "TMA_CROSS", "exit_params": {"fast": 8, "slow": 21}},

    {"symbol": "AVAX/USDT", "timeframe": "1h",
     "entry_strategy": "Supertrend", "entry_params": {"period": 14, "mult": 3.0},
     "entry_filter_type": "TSI", "entry_filter_period": 25,
     "exit_strategy": "Supertrend", "exit_params": {"period": 20, "mult": 5.0}},

    {"symbol": "NEAR/USDT", "timeframe": "30m",
     "entry_strategy": "Supertrend", "entry_params": {"period": 14, "mult": 4.0},
     "entry_filter_type": "VWMA", "entry_filter_period": 100,
     "exit_strategy": "Supertrend", "exit_params": {"period": 14, "mult": 3.0}},
]

# ============================================================
# 2) VESPA_HMA CONFIGS
# ============================================================
VESPA_CONFIGS = [
    {"symbol": "FET/USDT",   "timeframe": "4h", "entry_strategy": "VESPA",
     "entry_params": {"vlen": 16, "vsm": 8, "fast": 13, "slow": 36, "thr": 0.028},
     "exit_strategy": "VESPA", "exit_params": {"vlen": 16, "vsm": 8, "fast": 13, "slow": 36, "thr": 0.028}},

    {"symbol": "INJ/USDT",   "timeframe": "4h", "entry_strategy": "VESPA",
     "entry_params": {"vlen": 16, "vsm": 8, "fast": 20, "slow": 50, "thr": 0.028},
     "exit_strategy": "VESPA", "exit_params": {"vlen": 16, "vsm": 8, "fast": 20, "slow": 50, "thr": 0.028}},

    {"symbol": "AVAX/USDT",  "timeframe": "4h", "entry_strategy": "VESPA",
     "entry_params": {"vlen": 30, "vsm": 12, "fast": 13, "slow": 36, "thr": 0.028},
     "exit_strategy": "VESPA", "exit_params": {"vlen": 30, "vsm": 12, "fast": 13, "slow": 36, "thr": 0.028}},

    {"symbol": "ADA/USDT",   "timeframe": "4h", "entry_strategy": "VESPA",
     "entry_params": {"vlen": 16, "vsm": 8, "fast": 20, "slow": 50, "thr": 0.028},
     "exit_strategy": "VESPA", "exit_params": {"vlen": 16, "vsm": 8, "fast": 20, "slow": 50, "thr": 0.028}},

    {"symbol": "BCH/USDT",   "timeframe": "4h", "entry_strategy": "VESPA",
     "entry_params": {"vlen": 20, "vsm": 12, "fast": 18, "slow": 45, "thr": 0.028},
     "exit_strategy": "VESPA", "exit_params": {"vlen": 20, "vsm": 12, "fast": 18, "slow": 45, "thr": 0.028}},

    {"symbol": "ETH/USDT",   "timeframe": "4h", "entry_strategy": "VESPA",
     "entry_params": {"vlen": 40, "vsm": 12, "fast": 13, "slow": 36, "thr": 0.028},
     "exit_strategy": "VESPA", "exit_params": {"vlen": 40, "vsm": 12, "fast": 13, "slow": 36, "thr": 0.028}},
]

# ============================================================
# 3) SNIPER_HMA — ۱۱ ارز قوی
# ============================================================
SENS_MAP = {
    'VVVVS': {'mild_ob': 10, 'maj_ob': 25, 'mild_os': -10, 'maj_os': -25},
    'VVVS' : {'mild_ob': 15, 'maj_ob': 32, 'mild_os': -18, 'maj_os': -35},
    'VVS'  : {'mild_ob': 20, 'maj_ob': 40, 'mild_os': -28, 'maj_os': -45},
    'VS'   : {'mild_ob': 25, 'maj_ob': 48, 'mild_os': -38, 'maj_os': -50},
    'S'    : {'mild_ob': 30, 'maj_ob': 55, 'mild_os': -50, 'maj_os': -55},
    'NS'   : {'mild_ob': 42, 'maj_ob': 65, 'mild_os': -60, 'maj_os': -68},
}

SNIPER_CONFIGS = [
    {"symbol": "STX/USDT",  "timeframe": "4h", "entry_strategy": "SNIPER_HMA",
     "entry_params": {"hma_type": "HMA",  "hma_period": 80, "sens": "NS"},
     "exit_strategy": "SNIPER_HMA", "exit_params": {"hma_type": "HMA",  "hma_period": 80, "sens": "NS"}},

    {"symbol": "BCH/USDT",  "timeframe": "4h", "entry_strategy": "SNIPER_HMA",
     "entry_params": {"hma_type": "HMA",  "hma_period": 80, "sens": "NS"},
     "exit_strategy": "SNIPER_HMA", "exit_params": {"hma_type": "HMA",  "hma_period": 80, "sens": "NS"}},

    {"symbol": "COMP/USDT", "timeframe": "4h", "entry_strategy": "SNIPER_HMA",
     "entry_params": {"hma_type": "EHMA", "hma_period": 62, "sens": "S"},
     "exit_strategy": "SNIPER_HMA", "exit_params": {"hma_type": "EHMA", "hma_period": 62, "sens": "S"}},

    {"symbol": "LINK/USDT", "timeframe": "4h", "entry_strategy": "SNIPER_HMA",
     "entry_params": {"hma_type": "EHMA", "hma_period": 55, "sens": "S"},
     "exit_strategy": "SNIPER_HMA", "exit_params": {"hma_type": "EHMA", "hma_period": 55, "sens": "S"}},

    {"symbol": "SOL/USDT",  "timeframe": "4h", "entry_strategy": "SNIPER_HMA",
     "entry_params": {"hma_type": "EHMA", "hma_period": 50, "sens": "S"},
     "exit_strategy": "SNIPER_HMA", "exit_params": {"hma_type": "EHMA", "hma_period": 50, "sens": "S"}},

    {"symbol": "RUNE/USDT", "timeframe": "4h", "entry_strategy": "SNIPER_HMA",
     "entry_params": {"hma_type": "EHMA", "hma_period": 70, "sens": "S"},
     "exit_strategy": "SNIPER_HMA", "exit_params": {"hma_type": "EHMA", "hma_period": 70, "sens": "S"}},

    {"symbol": "SHIB/USDT", "timeframe": "4h", "entry_strategy": "SNIPER_HMA",
     "entry_params": {"hma_type": "HMA",  "hma_period": 80, "sens": "VS"},
     "exit_strategy": "SNIPER_HMA", "exit_params": {"hma_type": "HMA",  "hma_period": 80, "sens": "VS"}},

    {"symbol": "HBAR/USDT", "timeframe": "4h", "entry_strategy": "SNIPER_HMA",
     "entry_params": {"hma_type": "EHMA", "hma_period": 80, "sens": "VVVVS"},
     "exit_strategy": "SNIPER_HMA", "exit_params": {"hma_type": "EHMA", "hma_period": 80, "sens": "VVVVS"}},

    {"symbol": "NEAR/USDT", "timeframe": "4h", "entry_strategy": "SNIPER_HMA",
     "entry_params": {"hma_type": "EHMA", "hma_period": 75, "sens": "S"},
     "exit_strategy": "SNIPER_HMA", "exit_params": {"hma_type": "EHMA", "hma_period": 75, "sens": "S"}},

    {"symbol": "DOGE/USDT", "timeframe": "4h", "entry_strategy": "SNIPER_HMA",
     "entry_params": {"hma_type": "EHMA", "hma_period": 45, "sens": "NS"},
     "exit_strategy": "SNIPER_HMA", "exit_params": {"hma_type": "EHMA", "hma_period": 45, "sens": "NS"}},

    {"symbol": "AVAX/USDT", "timeframe": "4h", "entry_strategy": "SNIPER_HMA",
     "entry_params": {"hma_type": "EHMA", "hma_period": 80, "sens": "VS"},
     "exit_strategy": "SNIPER_HMA", "exit_params": {"hma_type": "EHMA", "hma_period": 80, "sens": "VS"}},
]

# همه کانفیگ‌ها با هم
BEST_CONFIGS = ORIGINAL_CONFIGS + VESPA_CONFIGS + SNIPER_CONFIGS

# ============================================================
# CLIENT & CACHES
# ============================================================
config = Configuration(key=API_KEY, secret=API_SECRET, host=GATE_HOST)
futures_api = FuturesApi(ApiClient(config))

_contracts_cache = {}
_specs_cache     = {}
_equity_cache    = {}
_available_cache = {}
_candles_cache   = {}

for f in (STATE_FILE, COOLDOWN_FILE):
    if not os.path.exists(f):
        with open(f, "w") as fp: json.dump({}, fp)

def load_json(fp):
    try:
        with open(fp) as f: return json.load(f)
    except: return {}

def save_json(fp, d):
    try:
        with open(fp, "w") as f: json.dump(d, f, indent=2)
    except Exception as e: print(f"  ⚠️ Save {fp}: {e}")

def get_contracts():
    if "usdt" not in _contracts_cache:
        try: _contracts_cache["usdt"] = futures_api.list_futures_contracts("usdt")
        except: _contracts_cache["usdt"] = []
    return _contracts_cache["usdt"]

def get_contract_specs(contract):
    if contract in _specs_cache: return _specs_cache[contract]
    for c in get_contracts():
        if c.name == contract:
            spec = {"quanto_multiplier": float(c.quanto_multiplier),
                    "order_size_min": int(c.order_size_min),
                    "order_size_max": int(c.order_size_max),
                    "order_price_round": float(c.order_price_round) if hasattr(c, "order_price_round") else 0.0001}
            _specs_cache[contract] = spec
            return spec
    return {"quanto_multiplier": 1.0, "order_size_min": 1, "order_size_max": MAX_CONTRACTS, "order_price_round": 0.0001}

def round_to_tick(p, t): return round(p / t) * t

# ============================================================
# INDICATORS
# ============================================================
def atr(df, p=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/p, adjust=False).mean()

def rsi(s, p=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1/p, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/p, adjust=False).mean()
    return 100 - 100/(1 + g/l.replace(0, np.nan))

def ema(s, p): return s.ewm(span=int(p), adjust=False).mean()
def sma(s, p): return s.rolling(int(p)).mean()

def wma(s, p):
    p = int(p)
    w = np.arange(1, p+1)
    return s.rolling(p).apply(lambda x: np.dot(x, w)/w.sum(), raw=True)

def hma(s, p):
    p = int(p)
    if p < 2: p = 2
    hl = max(p//2, 1)
    sl = max(1, int(np.sqrt(p)))
    return wma(2*wma(s, hl) - wma(s, p), sl)

def ehma(s, p):
    p = int(p)
    hl = max(p//2, 1)
    sl = max(1, int(np.sqrt(p)))
    return ema(2*ema(s, hl) - ema(s, p), sl)

def dema(s, p):
    e1 = ema(s, p)
    return 2*e1 - ema(e1, p)

def tma(s, p):
    p = int(p)
    h = (p + 1)//2 if p % 2 else p//2 + 1
    return s.rolling(h).mean().rolling(h).mean()

def vwma(close, volume, p):
    return (close*volume).rolling(p).sum() / volume.rolling(p).sum()

def supertrend(df, period=10, mult=3.0):
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    a = atr(df, period).values
    hl2 = (h+l)/2
    up, lo = hl2 + mult*a, hl2 - mult*a
    n = len(c)
    fu, fl = np.full(n, np.nan), np.full(n, np.nan)
    trend = np.ones(n, dtype=int)
    for i in range(int(period), n):
        if np.isnan(up[i]): continue
        if np.isnan(fu[i-1]):
            fu[i], fl[i] = up[i], lo[i]
            trend[i] = 1 if c[i] > fl[i] else -1
            continue
        fu[i] = up[i] if (up[i] < fu[i-1] or c[i-1] > fu[i-1]) else fu[i-1]
        fl[i] = lo[i] if (lo[i] > fl[i-1] or c[i-1] < fl[i-1]) else fl[i-1]
        trend[i] = (-1 if c[i] < fl[i] else 1) if trend[i-1]==1 else (1 if c[i] > fu[i] else -1)
    return pd.Series(trend, index=df.index)

def qqe(df, rsi_p=14, smooth=5, factor=4.236):
    c = df["close"]; r = rsi(c, rsi_p)
    rma = r.ewm(span=smooth, adjust=False).mean()
    atr_r = rma.diff().abs().ewm(alpha=1/(2*rsi_p-1), adjust=False).mean()
    dar = atr_r * factor
    n = len(c); trend = np.zeros(n, dtype=int); trend[0] = 1
    lb, sb = np.full(n, np.nan), np.full(n, np.nan)
    lb[0], sb[0] = rma.iloc[0]-dar.iloc[0], rma.iloc[0]+dar.iloc[0]
    rn, dn = rma.values, dar.values
    for i in range(1, n):
        nl, ns = rn[i]-dn[i], rn[i]+dn[i]
        lb[i] = max(lb[i-1], nl) if (rn[i-1] > lb[i-1] and rn[i] > lb[i-1]) else nl
        sb[i] = min(sb[i-1], ns) if (rn[i-1] < sb[i-1] and rn[i] < sb[i-1]) else ns
        if rn[i] > sb[i-1]: trend[i] = 1
        elif rn[i] < lb[i-1]: trend[i] = -1
        else: trend[i] = trend[i-1]
    return pd.Series(trend, index=df.index)

def tsi(s, long=25, short=13):
    m = s.diff(); am = m.abs()
    sm = m.ewm(span=long, adjust=False).mean().ewm(span=short, adjust=False).mean()
    sa = am.ewm(span=long, adjust=False).mean().ewm(span=short, adjust=False).mean()
    return 100 * (sm / sa)

# VESPA
def _wma_np(a, n):
    w = np.arange(n, 0, -1).astype(float)
    o = np.full(len(a), np.nan)
    if len(a) >= n:
        o[n-1:] = np.convolve(a, w, 'valid') / w.sum()
    return o

def hma_series(s, n):
    a = s.values.astype(float)
    h = max(int(n/2), 1)
    r = max(int(np.sqrt(n)), 1)
    d = 2*_wma_np(a, h) - _wma_np(a, n)
    o = np.full(len(a), np.nan)
    if len(a)-n+1 >= r:
        o[n+r-2:] = np.convolve(d[n-1:], np.arange(r,0,-1).astype(float), 'valid') / np.arange(r,0,-1).sum()
    return pd.Series(o, index=s.index)

def vespa_nv(df, L=20, S=12):
    o, c, h, l, v = df['open'], df['close'], df['high'], df['low'], df['volume'].fillna(1)
    g = o - c.shift(1)
    b = (c - o).abs()
    w = (h - l) - b
    up = c > o
    bull = w + np.where(up, b, 0) + g.clip(lower=0)
    bear = w + np.where(up, 0, b) + (-g).clip(lower=0)
    r = bull + bear
    bs = np.where(r > 0, bull/r, 0.5)
    dem = pd.Series(_wma_np(_wma_np((bs*v).values, L), S), index=df.index)
    sup = pd.Series(_wma_np(_wma_np(((1-bs)*v).values, L), S), index=df.index)
    return dem - sup

def io_pat(df):
    oo = ((df['high']>=df['high'].shift(1))&(df['low']<=df['low'].shift(1))&
          (df['high'].shift(1)>=df['high'].shift(2))&(df['low'].shift(1)<=df['low'].shift(2)))
    ii = ((df['high']<=df['high'].shift(1))&(df['low']>=df['low'].shift(1))&
          (df['high'].shift(1)<=df['high'].shift(2))&(df['low'].shift(1)>=df['low'].shift(2)))
    ioi= ((df['high']<=df['high'].shift(1))&(df['low']>=df['low'].shift(1))&
          (df['high'].shift(1)>=df['high'].shift(2))&(df['low'].shift(1)<=df['low'].shift(2)))
    return oo.fillna(False), ii.fillna(False), ioi.fillna(False)

# SNIPER
def sniper_signals(df, mild_ob, maj_ob, mild_os, maj_os):
    ap = (df['high'] + df['low'] + df['close']) / 3
    esa = ema(ap, 20)
    d   = ema((ap - esa).abs(), 20)
    ci  = (ap - esa) / (0.015 * d.replace(0, np.nan))
    wt1 = ema(ci, 30)
    wt2 = sma(wt1, 2)
    cross = ((wt1 > wt2) & (wt1.shift(1) <= wt2.shift(1))) | ((wt1 < wt2) & (wt1.shift(1) >= wt2.shift(1)))
    long1  = (wt1 < mild_os) & (wt1 > maj_os)
    short1 = (wt1 > mild_ob) & (wt1 < maj_ob)
    long2  = wt1 < maj_os
    short2 = wt1 > maj_ob
    lng = ((long1 & cross) | (long2 & cross)).fillna(False)
    sht = ((short1 & cross) | (short2 & cross)).fillna(False)
    both = lng & sht
    return (lng & ~both), (sht & ~both)

# ============================================================
# SIGNALS
# ============================================================
def get_ma_cross_signals(df, ma_type, fast, slow):
    close = df["close"]
    if ma_type == "WMA":   f, s = wma(close, fast), wma(close, slow)
    elif ma_type == "DEMA": f, s = dema(close, fast), dema(close, slow)
    elif ma_type == "TMA":  f, s = tma(close, fast), tma(close, slow)
    elif ma_type == "HMA":  f, s = hma(close, fast), hma(close, slow)
    else: return np.zeros(len(df), dtype=int)
    sig = np.zeros(len(df), dtype=int)
    sig[(f > s) & (f.shift(1) <= s.shift(1))] = 1
    sig[(f < s) & (f.shift(1) >= s.shift(1))] = -1
    return sig

def get_vespa_signals(df, params):
    vlen = params.get("vlen", 20)
    vsm  = params.get("vsm", 12)
    fast = params.get("fast", 16)
    slow = params.get("slow", 40)
    thr  = params.get("thr", 0.028)
    vw_period = params.get("vw_period", 100)

    NV = vespa_nv(df, vlen, vsm)
    HC_F = hma_series(df['close'], fast)
    HC_S = hma_series(df['close'], slow)
    OO, II, IOI = io_pat(df)
    VW = (df['close']*df['volume']).rolling(vw_period).sum() / df['volume'].rolling(vw_period).sum()

    try:
        htf = df.resample('1D').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
        NV_HTF = vespa_nv(htf, max(5,vlen//2), max(3,vsm//2)).reindex(df.index, method='ffill') if len(htf)>20 else pd.Series(0.0, index=df.index)
    except:
        NV_HTF = pd.Series(0.0, index=df.index)

    long_cond  = (NV > thr) & (HC_F > HC_S) & (II | IOI) & (df['close'] > VW) & (NV_HTF > thr*0.65)
    short_cond = (NV < -thr) & (HC_F < HC_S) & OO & (df['close'] < VW) & (NV_HTF < -thr*0.65)

    sig = np.zeros(len(df), dtype=int)
    sig[long_cond & ~long_cond.shift(1).fillna(False)] = 1
    sig[short_cond & ~short_cond.shift(1).fillna(False)] = -1
    return sig

def get_sniper_hma_signals(df, params):
    hma_type = params.get("hma_type", "HMA")
    hma_period = int(params.get("hma_period", 62))
    sens_name = params.get("sens", "S")
    sens = SENS_MAP.get(sens_name, SENS_MAP['S'])

    long_sig, short_sig = sniper_signals(df, **sens)
    hma_line = ehma(df['close'], hma_period) if hma_type == "EHMA" else hma(df['close'], hma_period)

    long_final  = long_sig  & (df['close'] > hma_line)
    short_final = short_sig & (df['close'] < hma_line)

    sig = np.zeros(len(df), dtype=int)
    sig[long_final  & ~long_final.shift(1).fillna(False)]  = 1
    sig[short_final & ~short_final.shift(1).fillna(False)] = -1
    return sig

def get_strategy_signals(df, strat_type, params):
    if strat_type == "VESPA":
        return get_vespa_signals(df, params)
    if strat_type == "SNIPER_HMA":
        return get_sniper_hma_signals(df, params)
    if strat_type in ("WMA_CROSS", "DEMA_CROSS", "TMA_CROSS", "HMA_CROSS"):
        return get_ma_cross_signals(df, strat_type.replace("_CROSS",""), params["fast"], params["slow"])

    close = df["close"]
    sig = np.zeros(len(df), dtype=int)
    if strat_type == "MACD":
        fast = close.ewm(span=params["fast"], adjust=False).mean()
        slow = close.ewm(span=params["slow"], adjust=False).mean()
        macd = fast - slow
        s = macd.ewm(span=params["signal"], adjust=False).mean()
        hist = macd - s
        st = supertrend(df, 10, 3.0)
        sig[(hist > 0) & (hist.shift(1) <= 0) & (st == 1)] = 1
        sig[(hist < 0) & (hist.shift(1) >= 0) & (st == -1)] = -1
    elif strat_type == "QQE":
        q = qqe(df, params["rsi_period"], params["smooth"], params["factor"])
        sig[(q == 1) & (q.shift(1) == -1)] = 1
        sig[(q == -1) & (q.shift(1) == 1)] = -1
    elif strat_type == "Supertrend":
        st = supertrend(df, params["period"], params["mult"])
        sig[(st == 1) & (st.shift(1) == -1)] = 1
        sig[(st == -1) & (st.shift(1) == 1)] = -1
    elif strat_type == "Volume":
        vma = df["volume"].rolling(params["ma_period"]).mean()
        vok = df["volume"] > params["vol_mult"] * vma
        sig[vok & (close > close.shift(1))] = 1
        sig[vok & (close < close.shift(1))] = -1
    return sig

def compute_filter_val(df, ftype, period):
    if not ftype or ftype == "NONE": return None
    close = df["close"]
    if ftype == "HMA":  return hma(close, period)
    if ftype == "DEMA": return dema(close, period)
    if ftype == "VWMA": return vwma(close, df["volume"], period)
    if ftype == "TSI":  return tsi(close)
    return None

def apply_filter(sig, close, fval, ftype):
    if fval is None or not ftype or ftype == "NONE": return sig
    ca = close.values if hasattr(close, "values") else np.asarray(close)
    fa = fval.values if hasattr(fval, "values") else np.asarray(fval)
    if ftype == "TSI":
        bull, bear = fa > 0, fa < 0
    else:
        bull, bear = ca > fa, ca < fa
    out = sig.copy()
    out[(sig == 1) & (~bull)] = 0
    out[(sig == -1) & (~bear)] = 0
    return out

def get_last_signal(df, strat, params, ftype="NONE", fperiod=0):
    raw = get_strategy_signals(df, strat, params)
    if ftype and ftype != "NONE":
        fval = compute_filter_val(df, ftype, fperiod)
        filtered = apply_filter(raw, df["close"], fval, ftype)
    else:
        filtered = raw
    return int(filtered[-1])

# ============================================================
# DATA & ACCOUNT + ORDERS + REPORTS (same as before)
# ============================================================
def fetch_candles(contract, tf, limit=300):
    key = (contract, tf)
    if key in _candles_cache: return _candles_cache[key]
    url = f"{GATE_HOST}/futures/usdt/candlesticks"
    try:
        r = requests.get(url, params={"contract": contract, "interval": tf, "limit": limit}, timeout=15)
        if r.status_code != 200: return None
        data = r.json()
        if not data or len(data) < 80: return None
        df = pd.DataFrame(data)
        df["t"] = pd.to_datetime(df["t"].astype(int), unit="s", utc=True)
        df = df.set_index("t").sort_index()
        for col in ["o","h","l","c","v"]: df[col] = df[col].astype(float)
        df = df.rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})
        _candles_cache[key] = df
        return df
    except: return None

def resolve_contract(symbol):
    base = symbol.split("/")[0]
    alt = f"{base}_USDT"
    for c in get_contracts():
        if c.name == alt: return alt
    return None

def get_position_info(contract):
    try:
        for p in futures_api.list_positions("usdt"):
            if p.contract == contract and int(p.size) != 0:
                return {"size": int(p.size), "entry_price": float(p.entry_price),
                        "mark_price": float(p.mark_price), "unrealised_pnl": float(p.unrealised_pnl),
                        "open_time": int(p.open_time) if hasattr(p,"open_time") and p.open_time else None}
    except: pass
    return None

def set_leverage(contract, lev):
    try: futures_api.update_position_leverage("usdt", contract, str(lev))
    except: pass

def place_market_order(contract, size):
    order = FuturesOrder(contract=contract, size=size, price="0", tif="ioc", reduce_only=False)
    try:
        futures_api.create_futures_order("usdt", order)
        print(f"  🧪 {'LONG' if size>0 else 'SHORT'} {contract} size={abs(size)}")
        return True
    except Exception as e:
        print(f"  ❌ {contract}: {e}")
        return False

def close_position_market(contract, size):
    order = FuturesOrder(contract=contract, size=0, price="0", tif="ioc", reduce_only=True, close=True)
    try:
        futures_api.create_futures_order("usdt", order)
        print(f"  🧪 CLOSE {contract}")
    except Exception as e: print(f"  ❌ Close {contract}: {e}")

def place_sl_tp(contract, side, sl_price, tp_price, entry_price, size):
    specs = get_contract_specs(contract)
    tick = specs["order_price_round"]
    sl_price = round_to_tick(sl_price, tick)
    tp_price = round_to_tick(tp_price, tick)
    sl_rule = 2 if side=="long" else 1
    tp_rule = 1 if side=="long" else 2
    order_type = "close-long-position" if side=="long" else "close-short-position"
    for kind, price, rule in [("SL", sl_price, sl_rule), ("TP", tp_price, tp_rule)]:
        order = FuturesPriceTriggeredOrder(
            initial=FuturesInitialOrder(contract=contract, size=0, price="0", tif="ioc", reduce_only=True, close=True),
            trigger=FuturesPriceTrigger(strategy_type=0, price_type=0, price=f"{price:.10f}", rule=rule, expiration=86400),
            order_type=order_type)
        try:
            futures_api.create_price_triggered_order("usdt", order)
            print(f"  🧪 {kind} {contract} @ {price:.6f}")
        except Exception as e: print(f"  ⚠️ {kind} {contract}: {e}")

def send_positions_report():
    try: positions = futures_api.list_positions("usdt")
    except: return
    active = [p for p in positions if int(p.size) != 0]
    if not active:
        send_telegram("📭 <b>No open positions</b>"); return
    now = datetime.now(timezone.utc).timestamp()
    total_pnl = 0
    lines = [f"📊 <b>Open Positions ({len(active)})</b>\n"]
    for p in active:
        size = int(p.size); side = "LONG" if size>0 else "SHORT"
        entry = float(p.entry_price); mark = float(p.mark_price); pnl = float(p.unrealised_pnl)
        total_pnl += pnl
        dur = "N/A"
        if hasattr(p,"open_time") and p.open_time:
            d = now - int(p.open_time)
            dur = f"{int(d//3600)}h {int((d%3600)//60)}m"
        pct = ((mark-entry)/entry*100) if side=="LONG" else ((entry-mark)/entry*100)
        lines.append(f"{'🟢' if side=='LONG' else '🔴'} <b>{p.contract}</b> {side} | {abs(size)}\n"
                     f"   ⏱ {dur} | Entry: {entry:.6f} | Mark: {mark:.6f}\n"
                     f"   PnL: {'✅' if pnl>=0 else '❌'} {pnl:+.4f} USDT ({pct:+.2f}%)\n")
    lines.append(f"\n💰 <b>Total: {total_pnl:+.4f} USDT</b>")
    send_telegram("\n".join(lines))

def send_trade_stats():
    try: closed = futures_api.list_position_close("usdt", limit=50)
    except: return
    if not closed: return
    total = len(closed)
    wins = sum(1 for p in closed if float(p.pnl)>0)
    total_pnl = sum(float(p.pnl) for p in closed)
    wr = (wins/total*100) if total>0 else 0
    send_telegram(f"📈 <b>Last {total} Closed:</b>\n✅ {wins}W | ❌ {total-wins}L\n🎯 WR: {wr:.1f}% | 💰 {total_pnl:+.4f} USDT\n🧪 <i>TESTNET</i>")

# ============================================================
# TRADING LOGIC
# ============================================================
def process_config(cfg):
    symbol = cfg["symbol"]
    tf = cfg["timeframe"]

    contract = resolve_contract(symbol)
    if contract is None:
        print(f"  ⚠️ Contract not found: {symbol}")
        return

    df = fetch_candles(contract, tf, limit=300)
    if df is None or len(df) < 100:
        print(f"  ⚠️ Not enough candles: {symbol} {tf}")
        return

    state = load_json(STATE_FILE)
    cooldown = load_json(COOLDOWN_FILE)
    pos_info = get_position_info(contract)
    current_pos = pos_info["size"] if pos_info else 0

    entry_sig = get_last_signal(df, cfg["entry_strategy"], cfg["entry_params"],
                                cfg.get("entry_filter_type","NONE"), cfg.get("entry_filter_period",0))
    exit_sig  = get_last_signal(df, cfg["exit_strategy"],  cfg["exit_params"],
                                cfg.get("exit_filter_type","NONE"), cfg.get("exit_filter_period",0))

    # تگ استراتژی برای تلگرام و state
    strat = cfg["entry_strategy"]
    if strat == "VESPA":
        strat_tag = "VESPA_HMA"
    elif strat == "SNIPER_HMA":
        strat_tag = "SNIPER_HMA"
    else:
        filt = cfg.get("entry_filter_type", "NONE")
        strat_tag = f"{strat}+{filt}" if filt != "NONE" else strat

    my_key = f"{symbol}|{tf}|{strat_tag}"

    if contract in cooldown and time.time() - cooldown[contract] < COOLDOWN_HOURS*3600:
        return

    if current_pos == 0:
        if contract in state:
            cooldown[contract] = time.time(); save_json(COOLDOWN_FILE, cooldown)
            del state[contract]; save_json(STATE_FILE, state)

        if entry_sig != 0:
            atr_val = atr(df, 14).iloc[-1]
            price = df["close"].iloc[-1]
            if atr_val <= 0 or price <= 0: return

            specs = get_contract_specs(contract)
            mult = specs["quanto_multiplier"]
            sl_dist = atr_val * ATR_MULT_SL
            tp_dist = atr_val * ATR_MULT_TP

            equity = _equity_cache.get("usdt", 0.0)
            if equity <= 0: return
            risk = equity * RISK_PCT
            raw_size = int(risk / (mult * sl_dist))
            size = max(specs["order_size_min"], min(raw_size, specs["order_size_max"], MAX_CONTRACTS))
            if size <= 0: return

            req_margin = (size * mult * price) / LEVERAGE
            max_margin = equity * MAX_MARGIN_PCT
            if req_margin > max_margin:
                size = int(size * (max_margin / req_margin))
                req_margin = (size * mult * price) / LEVERAGE

            available = _available_cache.get("usdt", 0.0)
            if req_margin > available:
                print(f"  ⏸️ {symbol}: need {req_margin:.2f}, have {available:.2f}")
                return

            set_leverage(contract, LEVERAGE)

            if entry_sig == 1:
                print(f"  📈 LONG {symbol} {tf} [{strat_tag}]")
                if place_market_order(contract, size):
                    state[contract] = my_key; save_json(STATE_FILE, state)
                    place_sl_tp(contract, "long", price-sl_dist, price+tp_dist, price, size)
                    send_telegram(
                        f"📈 <b>LONG</b> {symbol} {tf}\n"
                        f"🏷 <b>Strategy: {strat_tag}</b>\n"
                        f"💰 Entry: {price:.6f} | 📦 Size: {size}\n"
                        f"🛡️ SL: {price-sl_dist:.6f}\n"
                        f"🎯 TP: {price+tp_dist:.6f}\n"
                        f"🧪 <i>TESTNET</i>"
                    )
            elif entry_sig == -1:
                print(f"  📉 SHORT {symbol} {tf} [{strat_tag}]")
                if place_market_order(contract, -size):
                    state[contract] = my_key; save_json(STATE_FILE, state)
                    place_sl_tp(contract, "short", price+sl_dist, price-tp_dist, price, size)
                    send_telegram(
                        f"📉 <b>SHORT</b> {symbol} {tf}\n"
                        f"🏷 <b>Strategy: {strat_tag}</b>\n"
                        f"💰 Entry: {price:.6f} | 📦 Size: {size}\n"
                        f"🛡️ SL: {price+sl_dist:.6f}\n"
                        f"🎯 TP: {price-tp_dist:.6f}\n"
                        f"🧪 <i>TESTNET</i>"
                    )

    else:
        owner = state.get(contract)
        if owner is None:
            state[contract] = my_key; save_json(STATE_FILE, state); owner = my_key
        if owner != my_key: return

        should_exit = (current_pos > 0 and exit_sig == -1) or (current_pos < 0 and exit_sig == 1)
        if should_exit:
            print(f"  🔄 Exit {symbol} [{strat_tag}]")
            pnl = pos_info["unrealised_pnl"]
            ot = pos_info.get("open_time")
            dur = "N/A"
            if ot:
                d = datetime.now(timezone.utc).timestamp() - ot
                dur = f"{int(d//3600)}h {int((d%3600)//60)}m"

            close_position_market(contract, current_pos)
            del state[contract]; save_json(STATE_FILE, state)
            cooldown[contract] = time.time(); save_json(COOLDOWN_FILE, cooldown)

            side = "LONG" if current_pos > 0 else "SHORT"
            send_telegram(
                f"🚪 <b>CLOSED</b> {side} {symbol} {tf}\n"
                f"🏷 <b>Strategy: {strat_tag}</b>\n"
                f"⏱ {dur} | 📊 {'✅' if pnl>=0 else '❌'} {pnl:+.4f} USDT\n"
                f"🧪 <i>TESTNET</i>"
            )

# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 70)
    print(f"🧪 TESTNET — ALL STRATEGIES")
    print(f"⚙️ Original: {len(ORIGINAL_CONFIGS)} | VESPA: {len(VESPA_CONFIGS)} | SNIPER: {len(SNIPER_CONFIGS)}")
    print(f"⚙️ Total configs: {len(BEST_CONFIGS)}")
    print("=" * 70)

    try:
        acc = futures_api.list_futures_accounts("usdt")
        equity = float(acc.total)
        available = float(acc.available) if hasattr(acc, "available") else 0.0
        print(f"\n✅ Equity: {equity:.2f} | Available: {available:.2f}\n")
    except Exception as e:
        print(f"❌ {e}"); send_telegram(f"❌ Connection: {e}"); sys.exit(1)

    _equity_cache["usdt"] = equity
    _available_cache["usdt"] = available

    send_telegram(
        f"🧪 <b>All Strategies Scanner</b>\n"
        f"💰 ${equity:.2f} | Free: ${available:.2f}\n"
        f"📊 Original: {len(ORIGINAL_CONFIGS)} | VESPA: {len(VESPA_CONFIGS)} | SNIPER: {len(SNIPER_CONFIGS)}\n"
        f"🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"
    )

    send_positions_report()

    for cfg in BEST_CONFIGS:
        try:
            process_config(cfg)
        except Exception as e:
            print(f"  ❌ {cfg['symbol']}: {e}")

    send_positions_report()
    send_trade_stats()
    print(f"\n✅ Run complete — {len(BEST_CONFIGS)} configs checked")

if __name__ == "__main__":
    main()
