#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gate.io Futures TestNet Scanner — 18 OPTIMIZED CONFIGS (v6→v13)
================================================================
All configs verified through 6 rounds of parameter tuning (v6→v13):
  - WF 4/4 (positive in all 4 walk-forward windows)
  - wf_min > 6.7% (worst window also profitable)
  - Mean wf_mean: 17.4%
  - 4 MA types, 2 timeframes, 18 unique symbols

Each signal shows: strategy, params, WF stats, source version.
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

if "testnet" not in GATE_HOST.lower():
    print("❌ SAFETY: host is not testnet!"); sys.exit(99)

def send_telegram(text):
    if not TG_TOKEN or not TG_CHAT_ID: return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=10,
        )
    except Exception as e:
        print(f"  [TG] Error: {e}")

print("🧪 GATE.IO TESTNET — 18 OPTIMIZED CONFIGS")

# ============================================================
# PARAMETERS
# ============================================================
RISK_PCT       = 0.01   # نصف نرمال (18 config همزمان)
ATR_MULT_SL    = 2.5
ATR_MULT_TP    = 7.5
MAX_CONTRACTS  = 90_000
LEVERAGE       = 10
STATE_FILE     = "state.json"
COOLDOWN_FILE  = "cooldown.json"
COOLDOWN_HOURS = 4
MAX_MARGIN_PCT = 0.20

# ============================================================
# 18 OPTIMIZED CONFIGS (sorted by wf_min, safety-first)
# ============================================================
OPTIMIZED_CONFIGS = [
    # TIER 1 — wf_min > 12% (6 configs, پایهٔ اصلی)
    {"symbol": "ZEC/USDT", "timeframe": "1d", "ma_type": "LSMA",
     "params": {"fast": 9, "slow": 79},
     "wf_min": 17.4, "wf_mean": 25.0, "score": 43.2, "src": "v6", "n_trades": 32, "pf": 5.13},

    {"symbol": "SOL/USDT", "timeframe": "1d", "ma_type": "LSMA",
     "params": {"fast": 20, "slow": 59},
     "wf_min": 15.2, "wf_mean": 18.7, "score": 36.5, "src": "v9", "n_trades": 32, "pf": 3.02},

    {"symbol": "TAO/USDT", "timeframe": "4h", "ma_type": "WMA",
     "params": {"fast": 36, "slow": 116},
     "wf_min": 13.9, "wf_mean": 17.4, "score": 26.2, "src": "v10", "n_trades": 35, "pf": 1.95},

    {"symbol": "TIA/USDT", "timeframe": "4h", "ma_type": "LSMA",
     "params": {"fast": 43, "slow": 106},
     "wf_min": 13.5, "wf_mean": 16.2, "score": 30.5, "src": "v9", "n_trades": 84, "pf": 1.73},

    {"symbol": "XRP/USDT", "timeframe": "4h", "ma_type": "WMA",
     "params": {"fast": 49, "slow": 58},
     "wf_min": 13.4, "wf_mean": 18.3, "score": 36.6, "src": "v9", "n_trades": 42, "pf": 1.91},

    {"symbol": "ETH/USDT", "timeframe": "1d", "ma_type": "HMA",
     "params": {"fast": 27, "slow": 130},
     "wf_min": 11.4, "wf_mean": 22.0, "score": 43.0, "src": "v9", "n_trades": 23, "pf": 6.83},

    # TIER 2 — wf_min 9-12% (4 configs)
    {"symbol": "CVC/USDT", "timeframe": "4h", "ma_type": "DMA",
     "params": {"fast": 14, "fast_shift": 15, "slow": 50, "slow_shift": 5},
     "wf_min": 11.2, "wf_mean": 20.9, "score": 47.4, "src": "v11", "n_trades": 75, "pf": 1.72},

    {"symbol": "SUI/USDT", "timeframe": "4h", "ma_type": "LSMA",
     "params": {"fast": 50, "slow": 179},
     "wf_min": 10.8, "wf_mean": 14.7, "score": 27.6, "src": "v9", "n_trades": 34, "pf": 1.86},

    {"symbol": "BTC/USDT", "timeframe": "1d", "ma_type": "LSMA",
     "params": {"fast": 39, "slow": 80},
     "wf_min": 9.9, "wf_mean": 18.4, "score": 26.7, "src": "v9", "n_trades": 24, "pf": 3.05},

    {"symbol": "KSM/USDT", "timeframe": "4h", "ma_type": "DMA",
     "params": {"fast": 13, "fast_shift": 12, "slow": 52, "slow_shift": 5},
     "wf_min": 9.8, "wf_mean": 19.2, "score": 38.4, "src": "v9", "n_trades": 52, "pf": 2.23},

    # TIER 3 — wf_min 8-9% (4 configs)
    {"symbol": "SNX/USDT", "timeframe": "4h", "ma_type": "DMA",
     "params": {"fast": 22, "fast_shift": 15, "slow": 50, "slow_shift": 10},
     "wf_min": 9.0, "wf_mean": 14.5, "score": 27.9, "src": "v9", "n_trades": 59, "pf": 1.72},

    {"symbol": "DOGE/USDT", "timeframe": "1d", "ma_type": "HMA",
     "params": {"fast": 24, "slow": 129},
     "wf_min": 9.0, "wf_mean": 16.9, "score": 21.6, "src": "v9", "n_trades": 24, "pf": 2.36},

    {"symbol": "KNC/USDT", "timeframe": "4h", "ma_type": "DMA",
     "params": {"fast": 13, "fast_shift": 12, "slow": 221, "slow_shift": 25},
     "wf_min": 8.5, "wf_mean": 13.3, "score": 15.1, "src": "v11", "n_trades": 19, "pf": 2.55},

    {"symbol": "BLUR/USDT", "timeframe": "4h", "ma_type": "WMA",
     "params": {"fast": 49, "slow": 119},
     "wf_min": 8.3, "wf_mean": 15.6, "score": 26.6, "src": "v9", "n_trades": 22, "pf": 2.76},

    # TIER 4 — wf_min 6-8% (4 configs جدید)
    {"symbol": "PENGU/USDT", "timeframe": "4h", "ma_type": "LSMA",
     "params": {"fast": 49, "slow": 139},
     "wf_min": 7.8, "wf_mean": 15.9, "score": 26.8, "src": "v12", "n_trades": 31, "pf": 2.32},

    {"symbol": "GRT/USDT", "timeframe": "1d", "ma_type": "LSMA",
     "params": {"fast": 20, "slow": 59},
     "wf_min": 7.3, "wf_mean": 13.8, "score": 27.3, "src": "v13", "n_trades": 32, "pf": 3.17},

    {"symbol": "ID/USDT", "timeframe": "4h", "ma_type": "DMA",
     "params": {"fast": 18, "fast_shift": 11, "slow": 50, "slow_shift": 11},
     "wf_min": 7.2, "wf_mean": 12.4, "score": 18.4, "src": "v12", "n_trades": 48, "pf": 1.66},

    {"symbol": "PIXEL/USDT", "timeframe": "4h", "ma_type": "WMA",
     "params": {"fast": 47, "slow": 205},
     "wf_min": 6.7, "wf_mean": 15.5, "score": 24.5, "src": "v9", "n_trades": 15, "pf": 4.86},
]

# Print startup summary
print("\n" + "="*140)
print(f"  📋 LOADED {len(OPTIMIZED_CONFIGS)} OPTIMIZED CONFIGS")
print("="*140)
print(f"  {'#':>2} {'Symbol':<12} {'TF':<4} {'MA':<6} {'wf_min':>7} {'wf_mean':>8} {'score':>6} {'src':<4}")
print(f"  {'-'*2} {'-'*12} {'-'*4} {'-'*6} {'-'*7} {'-'*8} {'-'*6} {'-'*4}")
for i, c in enumerate(OPTIMIZED_CONFIGS, 1):
    print(f"  {i:>2} {c['symbol']:<12} {c['timeframe']:<4} {c['ma_type']:<6} "
          f"{c['wf_min']:>6.1f}% {c['wf_mean']:>7.1f}% {c['score']:>6.1f} {c['src']:<4}")
print("="*140)
print(f"  Mean wf_min: {np.mean([c['wf_min'] for c in OPTIMIZED_CONFIGS]):.1f}%  |  "
      f"Mean wf_mean: {np.mean([c['wf_mean'] for c in OPTIMIZED_CONFIGS]):.1f}%")
print("="*140 + "\n")

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
    return {"quanto_multiplier": 1.0, "order_size_min": 1,
            "order_size_max": MAX_CONTRACTS, "order_price_round": 0.0001}

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

def dema(s, p):
    e1 = ema(s, p)
    return 2*e1 - ema(e1, p)

def tma(s, p):
    p = int(p)
    h = (p + 1)//2 if p % 2 else p//2 + 1
    return s.rolling(h).mean().rolling(h).mean()

def lsma(s, p):
    """Least Squares Moving Average."""
    p = int(p)
    if p < 2: p = 2
    n = len(s)
    y = s.values.astype(float)
    idx = np.arange(n, dtype=float)
    ky = idx * y
    ky_roll = pd.Series(ky).rolling(p, min_periods=p).sum().values
    y_roll = pd.Series(y).rolling(p, min_periods=p).sum().values
    start = idx - (p - 1)
    weighted = ky_roll - start * y_roll
    xsum = p * (p - 1) / 2
    denom = p**2 * (p**2 - 1) / 12
    slope = (p * weighted - xsum * y_roll) / denom
    out = y_roll / p + slope * (p - 1) / 2
    return pd.Series(out, index=s.index)

def dma_ma(s, p, shift=5):
    """Displaced Moving Average."""
    return s.rolling(int(p)).mean().shift(int(shift))

# ============================================================
# SIGNALS
# ============================================================
def compute_ma(close, ma_type, params):
    """Compute fast and slow MA for a given MA type."""
    fast = params["fast"]
    slow = params["slow"]
    if ma_type == "WMA":
        return wma(close, fast), wma(close, slow)
    elif ma_type == "HMA":
        return hma(close, fast), hma(close, slow)
    elif ma_type == "DEMA":
        return dema(close, fast), dema(close, slow)
    elif ma_type == "TMA":
        return tma(close, fast), tma(close, slow)
    elif ma_type == "LSMA":
        return lsma(close, fast), lsma(close, slow)
    elif ma_type == "DMA":
        fs = params.get("fast_shift", 5)
        ss = params.get("slow_shift", 10)
        return dma_ma(close, fast, fs), dma_ma(close, slow, ss)
    else:
        return None, None

def get_ma_cross_signals(df, ma_type, params):
    """Returns +1 at golden cross, -1 at death cross, 0 elsewhere."""
    close = df["close"]
    f, s = compute_ma(close, ma_type, params)
    if f is None:
        return np.zeros(len(df), dtype=int)
    sig = np.zeros(len(df), dtype=int)
    sig[(f > s) & (f.shift(1) <= s.shift(1))] = 1
    sig[(f < s) & (f.shift(1) >= s.shift(1))] = -1
    return sig

def get_last_signal(df, ma_type, params):
    raw = get_ma_cross_signals(df, ma_type, params)
    return int(raw[-1])

def describe_signal(df, ma_type, params):
    """Human-readable explanation of last bar's MA state."""
    close = df["close"]
    f, s = compute_ma(close, ma_type, params)
    if f is None or len(f) < 2:
        return "N/A"
    f_now, s_now = float(f.iloc[-1]), float(s.iloc[-1])
    f_prev, s_prev = float(f.iloc[-2]), float(s.iloc[-2])
    if f_prev <= s_prev and f_now > s_now:
        return "🟢 GOLDEN CROSS (fast crossed ABOVE slow)"
    elif f_prev >= s_prev and f_now < s_now:
        return "🔴 DEATH CROSS (fast crossed BELOW slow)"
    elif f_now > s_now:
        return f"⚪ fast({f_now:.4f}) > slow({s_now:.4f}) — no cross yet"
    else:
        return f"⚪ fast({f_now:.4f}) < slow({s_now:.4f}) — no cross yet"

# ============================================================
# DATA FETCH
# ============================================================
def fetch_candles(contract, tf, limit=300):
    key = (contract, tf)
    if key in _candles_cache: return _candles_cache[key]
    url = f"{GATE_HOST}/futures/usdt/candlesticks"
    # Gate uses 1d, 4h, 1h, 30m, etc — ok
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
        return True
    except Exception as e:
        print(f"  ❌ Order {contract}: {e}")
        return False

def close_position_market(contract, size):
    order = FuturesOrder(contract=contract, size=0, price="0", tif="ioc", reduce_only=True, close=True)
    try:
        futures_api.create_futures_order("usdt", order)
        return True
    except Exception as e:
        print(f"  ❌ Close {contract}: {e}")
        return False

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
            print(f"    ✓ {kind} @ {price:.6f}")
        except Exception as e: print(f"    ⚠️ {kind}: {e}")

# ============================================================
# REPORTS
# ============================================================
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
    send_telegram(f"📈 <b>Last {total} Closed:</b>\n"
                  f"✅ {wins}W | ❌ {total-wins}L\n"
                  f"🎯 WR: {wr:.1f}% | 💰 {total_pnl:+.4f} USDT\n"
                  f"🧪 <i>TESTNET</i>")

# ============================================================
# TRADING LOGIC
# ============================================================
def process_config(cfg):
    symbol = cfg["symbol"]
    tf = cfg["timeframe"]
    ma_type = cfg["ma_type"]
    params = cfg["params"]

    contract = resolve_contract(symbol)
    if contract is None:
        return  # silently skip (expected for some symbols on testnet)

    df = fetch_candles(contract, tf, limit=300)
    if df is None or len(df) < 100:
        return

    state = load_json(STATE_FILE)
    cooldown = load_json(COOLDOWN_FILE)
    pos_info = get_position_info(contract)
    current_pos = pos_info["size"] if pos_info else 0

    # Compute signal + description
    entry_sig = get_last_signal(df, ma_type, params)
    sig_desc = describe_signal(df, ma_type, params)

    # Strategy tag (clear ID)
    strat_tag = f"{ma_type}_CROSS"
    param_str = " ".join(f"{k}={v}" for k, v in params.items())
    my_key = f"{symbol}|{tf}|{strat_tag}"

    # Cooldown check
    if contract in cooldown and time.time() - cooldown[contract] < COOLDOWN_HOURS*3600:
        return

    # ---- ENTRY ----
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
            if equity <= 0:
                # Fetch equity
                try:
                    accts = futures_api.list_futures_accounts("usdt")
                    equity = float(accts.total)
                    _equity_cache["usdt"] = equity
                    _available_cache["usdt"] = float(accts.available)
                except: return
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

            direction = "LONG" if entry_sig == 1 else "SHORT"
            side_word = "LONG" if entry_sig == 1 else "SHORT"
            emoji = "📈" if entry_sig == 1 else "📉"

            print(f"\n  {emoji} {side_word} {symbol} {tf} [{strat_tag}]")
            print(f"     Signal: {sig_desc}")
            print(f"     Params: {param_str}")
            print(f"     WF: min={cfg['wf_min']:+.1f}%, mean={cfg['wf_mean']:+.1f}%, src={cfg['src']}")
            print(f"     Entry: {price:.6f}, SL: {sl_dist:.6f}, TP: {tp_dist:.6f}, Size: {size}")

            signed_size = size if entry_sig == 1 else -size
            if place_market_order(contract, signed_size):
                state[contract] = my_key; save_json(STATE_FILE, state)
                if entry_sig == 1:
                    place_sl_tp(contract, "long", price-sl_dist, price+tp_dist, price, size)
                else:
                    place_sl_tp(contract, "short", price+sl_dist, price-tp_dist, price, size)

                # DETAILED TELEGRAM
                sl_price = price - sl_dist if entry_sig == 1 else price + sl_dist
                tp_price = price + tp_dist if entry_sig == 1 else price - tp_dist
                send_telegram(
                    f"{emoji} <b>{side_word}</b> {symbol} {tf}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🏷 <b>Strategy:</b> {strat_tag}\n"
                    f"📐 <b>Params:</b> <code>{param_str}</code>\n"
                    f"📊 <b>Backtest:</b> WF_min=<b>{cfg['wf_min']:+.1f}%</b> | "
                    f"WF_mean={cfg['wf_mean']:+.1f}% | Score={cfg['score']:.1f}\n"
                    f"📦 <b>Source:</b> {cfg['src']} | n={cfg['n_trades']} trades | PF={cfg['pf']:.2f}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🔍 <b>Signal:</b> {sig_desc}\n"
                    f"💰 <b>Entry:</b> {price:.6f}\n"
                    f"🛡 <b>SL:</b> {sl_price:.6f} ({-ATR_MULT_SL*atr_val/price*100:+.2f}%)\n"
                    f"🎯 <b>TP:</b> {tp_price:.6f} ({ATR_MULT_TP*atr_val/price*100:+.2f}%)\n"
                    f"📦 <b>Size:</b> {size} contracts\n"
                    f"💵 <b>Risk:</b> {RISK_PCT*100:.1f}% of equity\n"
                    f"🧪 <i>TESTNET</i>"
                )

    # ---- EXIT ----
    else:
        owner = state.get(contract)
        if owner is None:
            state[contract] = my_key; save_json(STATE_FILE, state); owner = my_key
        if owner != my_key: return

        # Exit: opposite signal
        should_exit = (current_pos > 0 and entry_sig == -1) or (current_pos < 0 and entry_sig == 1)
        if should_exit:
            pnl = pos_info["unrealised_pnl"]
            ot = pos_info.get("open_time")
            dur = "N/A"
            if ot:
                d = datetime.now(timezone.utc).timestamp() - ot
                dur = f"{int(d//3600)}h {int((d%3600)//60)}m"

            print(f"\n  🔄 EXIT {symbol} [{strat_tag}] | PnL: {pnl:+.4f}")
            close_position_market(contract, current_pos)
            del state[contract]; save_json(STATE_FILE, state)
            cooldown[contract] = time.time(); save_json(COOLDOWN_FILE, cooldown)

            side = "LONG" if current_pos > 0 else "SHORT"
            send_telegram(
                f"🚪 <b>CLOSED</b> {side} {symbol} {tf}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🏷 <b>Strategy:</b> {strat_tag}\n"
                f"📐 <b>Params:</b> <code>{param_str}</code>\n"
                f"📦 <b>Source:</b> {cfg['src']}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🔍 <b>Exit signal:</b> {sig_desc}\n"
                f"⏱ <b>Duration:</b> {dur}\n"
                f"📊 <b>PnL:</b> {'✅' if pnl>=0 else '❌'} <b>{pnl:+.4f} USDT</b>\n"
                f"🧪 <i>TESTNET</i>"
            )

# ============================================================
# MAIN
# ============================================================
def main():
    # Fetch equity once at startup
    try:
        accts = futures_api.list_futures_accounts("usdt")
        _equity_cache["usdt"] = float(accts.total)
        _available_cache["usdt"] = float(accts.available)
        print(f"💰 Equity: {_equity_cache['usdt']:.2f} USDT | Available: {_available_cache['usdt']:.2f} USDT\n")
    except Exception as e:
        print(f"⚠️ Cannot fetch equity: {e}")

    # Startup message
    send_telegram(
        f"🚀 <b>Scanner Started — 18 Configs</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 Equity: {_equity_cache.get('usdt', 0):.2f} USDT\n"
        f"🎯 Risk per trade: {RISK_PCT*100:.1f}%\n"
        f"🔧 Leverage: {LEVERAGE}x\n"
        f"📊 Portfolio: 18 configs (all WF 4/4)\n"
        f"🧪 <i>TESTNET</i>"
    )

    cycle = 0
    while True:
        cycle += 1
        print(f"\n{'='*80}")
        print(f"  🔄 CYCLE {cycle} — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"{'='*80}")

        # Refresh equity every 10 cycles
        if cycle % 10 == 1:
            try:
                accts = futures_api.list_futures_accounts("usdt")
                _equity_cache["usdt"] = float(accts.total)
                _available_cache["usdt"] = float(accts.available)
            except: pass

        for cfg in OPTIMIZED_CONFIGS:
            try:
                process_config(cfg)
            except Exception as e:
                print(f"  ⚠️ {cfg['symbol']} error: {e}")

        # Reports every cycle
        try:
            send_positions_report()
            if cycle % 5 == 0:
                send_trade_stats()
        except Exception as e:
            print(f"  ⚠️ Report error: {e}")

        # Sleep between cycles (5 minutes)
        print(f"\n  💤 Sleeping 5 min...")
        time.sleep(300)

if __name__ == "__main__":
    main()
