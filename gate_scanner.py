 #!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gate.io Futures TestNet Scanner — CROSS-STRATEGY GOLDEN 15
==========================================================
- 15 configs from 27,071-backtest Cross-Strategy analysis
- Entry: signal from entry_strategy
- Exit:  opposite signal from exit_strategy (cross-strategy) + SL/TP
- Leverage 10x | Cooldown 4h | Risk 2% | Max Margin 20%
- TP = 7.5 ATR | SL = 2.5 ATR (1:3 R:R)
"""

import os, sys, json, time, requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from gate_api import ApiClient, Configuration, FuturesApi
from gate_api.models import FuturesOrder, FuturesPriceTriggeredOrder, FuturesInitialOrder, FuturesPriceTrigger

# ============================================================
# 🧪 TESTNET KEYS
# ============================================================
API_KEY    = "b37ed23f61f92662e11ba8b247adfb3b"
API_SECRET = "39395a011e7d170e4b955c3060692ed5bfe3f380cd470717dbbc516b03a73fea"
GATE_HOST  = "https://api-testnet.gateapi.io/api/v4"

# ============================================================
# 📱 TELEGRAM
# ============================================================
TG_TOKEN   = "8601458987:AAEvcxx2ClOTfQIqEp9Ok6TwcvKRaMfIeqo"
TG_CHAT_ID = "254500349"

def send_telegram(text: str):
    if not TG_TOKEN or not TG_CHAT_ID: return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10,
        )
    except Exception as e:
        print(f"  [TG] Error: {e}")

# ============================================================
# 🛡️ SAFETY: only run on testnet
# ============================================================
if "testnet" not in GATE_HOST.lower():
    print("❌ SAFETY: host is not testnet!"); sys.exit(99)

print("🧪 GATE.IO TESTNET — CROSS-STRATEGY GOLDEN 15")

# ============================================================
# ⚙️ PARAMETERS
# ============================================================
RISK_PCT       = 0.02      # 2% risk per trade
ATR_MULT_SL    = 2.5       # SL = 2.5 ATR
ATR_MULT_TP    = 7.5       # TP = 7.5 ATR (1:3 R:R)
MAX_CONTRACTS  = 90_000
LEVERAGE       = 10
STATE_FILE     = "state.json"
COOLDOWN_FILE  = "cooldown.json"
COOLDOWN_HOURS = 4
MAX_MARGIN_PCT = 0.20      # max 20% equity per trade

# ============================================================
# ✅ GOLDEN 15 — Cross-Strategy Portfolio
# ============================================================
BEST_CONFIGS = [
    # 1. POL 1h: QQE_I → MACD_S
    {"symbol": "POL/USDT", "timeframe": "1h",
     "entry_strategy": "QQE",        "entry_params": {"rsi_period": 14, "smooth": 7, "factor": 5.0},   "entry_filter": "HMA_20",
     "exit_strategy":  "MACD",       "exit_params":  {"fast": 8, "slow": 17, "signal": 5},             "exit_filter":  "NONE"},

    # 2. OP 1h: ST_I → QQE_I (بالاترین شارپ)
    {"symbol": "OP/USDT", "timeframe": "1h",
     "entry_strategy": "Supertrend", "entry_params": {"period": 14, "mult": 5.0},                       "entry_filter": "TSI_SENS",
     "exit_strategy":  "QQE",        "exit_params":  {"rsi_period": 14, "smooth": 7, "factor": 5.0},    "exit_filter":  "HMA_20"},

    # 3. WLD 1h: ST_I → MACD_I
    {"symbol": "WLD/USDT", "timeframe": "1h",
     "entry_strategy": "Supertrend", "entry_params": {"period": 14, "mult": 5.0},                       "entry_filter": "TSI_SENS",
     "exit_strategy":  "MACD",       "exit_params":  {"fast": 20, "slow": 40, "signal": 12},            "exit_filter":  "NONE"},

    # 4. ETH 30m: QQE_I → ST_S
    {"symbol": "ETH/USDT", "timeframe": "30m",
     "entry_strategy": "QQE",        "entry_params": {"rsi_period": 14, "smooth": 7, "factor": 5.0},    "entry_filter": "HMA_20",
     "exit_strategy":  "Supertrend", "exit_params":  {"period": 14, "mult": 3.0},                       "exit_filter":  "TSI_SENS"},

    # 5. ADA 1h: ST_N → VOL_N (کم‌ریسک‌ترین DD)
    {"symbol": "ADA/USDT", "timeframe": "1h",
     "entry_strategy": "Supertrend", "entry_params": {"period": 14, "mult": 4.0},                       "entry_filter": "TSI_SENS",
     "exit_strategy":  "Volume",     "exit_params":  {"vol_mult": 1.2, "ma_period": 20},                "exit_filter":  "HMA_20"},

    # 6. SAND 30m: MACD_N → ST_S
    {"symbol": "SAND/USDT", "timeframe": "30m",
     "entry_strategy": "MACD",       "entry_params": {"fast": 12, "slow": 26, "signal": 9},             "entry_filter": "NONE",
     "exit_strategy":  "Supertrend", "exit_params":  {"period": 14, "mult": 3.0},                       "exit_filter":  "TSI_SENS"},

    # 7. FIL 30m: QQE_N → MACD_I
    {"symbol": "FIL/USDT", "timeframe": "30m",
     "entry_strategy": "QQE",        "entry_params": {"rsi_period": 14, "smooth": 5, "factor": 4.236},  "entry_filter": "HMA_20",
     "exit_strategy":  "MACD",       "exit_params":  {"fast": 20, "slow": 40, "signal": 12},            "exit_filter":  "NONE"},

    # 8. NEAR 30m: ST_N → VOL_N
    {"symbol": "NEAR/USDT", "timeframe": "30m",
     "entry_strategy": "Supertrend", "entry_params": {"period": 14, "mult": 4.0},                       "entry_filter": "TSI_SENS",
     "exit_strategy":  "Volume",     "exit_params":  {"vol_mult": 1.2, "ma_period": 20},                "exit_filter":  "HMA_20"},

    # 9. GRT 1h: VOL_I → MACD_N
    {"symbol": "GRT/USDT", "timeframe": "1h",
     "entry_strategy": "Volume",     "entry_params": {"vol_mult": 1.5, "ma_period": 30},                "entry_filter": "HMA_20",
     "exit_strategy":  "MACD",       "exit_params":  {"fast": 12, "slow": 26, "signal": 9},             "exit_filter":  "NONE"},

    # 10. BONK 30m: ST_S → ST_I
    {"symbol": "BONK/USDT", "timeframe": "30m",
     "entry_strategy": "Supertrend", "entry_params": {"period": 14, "mult": 3.0},                       "entry_filter": "TSI_SENS",
     "exit_strategy":  "Supertrend", "exit_params":  {"period": 14, "mult": 5.0},                       "exit_filter":  "TSI_SENS"},

    # 11. BCH 1h: QQE_I → ST_S
    {"symbol": "BCH/USDT", "timeframe": "1h",
     "entry_strategy": "QQE",        "entry_params": {"rsi_period": 14, "smooth": 7, "factor": 5.0},    "entry_filter": "HMA_20",
     "exit_strategy":  "Supertrend", "exit_params":  {"period": 14, "mult": 3.0},                       "exit_filter":  "TSI_SENS"},

    # 12. TIA 30m: ST_N → VOL_I
    {"symbol": "TIA/USDT", "timeframe": "30m",
     "entry_strategy": "Supertrend", "entry_params": {"period": 14, "mult": 4.0},                       "entry_filter": "TSI_SENS",
     "exit_strategy":  "Volume",     "exit_params":  {"vol_mult": 1.5, "ma_period": 30},                "exit_filter":  "HMA_20"},

    # 13. FET 1h: ST_N → VOL_N
    {"symbol": "FET/USDT", "timeframe": "1h",
     "entry_strategy": "Supertrend", "entry_params": {"period": 14, "mult": 4.0},                       "entry_filter": "TSI_SENS",
     "exit_strategy":  "Volume",     "exit_params":  {"vol_mult": 1.2, "ma_period": 20},                "exit_filter":  "HMA_20"},

    # 14. XRP 30m: QQE_S → ST_I
    {"symbol": "XRP/USDT", "timeframe": "30m",
     "entry_strategy": "QQE",        "entry_params": {"rsi_period": 14, "smooth": 3, "factor": 3.0},    "entry_filter": "HMA_20",
     "exit_strategy":  "Supertrend", "exit_params":  {"period": 14, "mult": 5.0},                       "exit_filter":  "TSI_SENS"},

    # 15. FLOKI 1h: ST_S → QQE_N
    {"symbol": "FLOKI/USDT", "timeframe": "1h",
     "entry_strategy": "Supertrend", "entry_params": {"period": 14, "mult": 3.0},                       "entry_filter": "TSI_SENS",
     "exit_strategy":  "QQE",        "exit_params":  {"rsi_period": 14, "smooth": 5, "factor": 4.236},  "exit_filter":  "HMA_20"},
]

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
    except Exception: return {}

def save_json(fp, data):
    try:
        with open(fp, "w") as f: json.dump(data, f, indent=2)
    except Exception as e: print(f"  ⚠️ Save {fp}: {e}")

def get_contracts():
    if "usdt" not in _contracts_cache:
        try: _contracts_cache["usdt"] = futures_api.list_futures_contracts("usdt")
        except Exception: _contracts_cache["usdt"] = []
    return _contracts_cache["usdt"]

def get_contract_specs(contract):
    if contract in _specs_cache: return _specs_cache[contract]
    for c in get_contracts():
        if c.name == contract:
            spec = {
                "quanto_multiplier": float(c.quanto_multiplier),
                "order_size_min": int(c.order_size_min),
                "order_size_max": int(c.order_size_max),
                "order_price_round": float(c.order_price_round) if hasattr(c, "order_price_round") else 0.0001,
            }
            _specs_cache[contract] = spec
            return spec
    return {"quanto_multiplier": 1.0, "order_size_min": 1,
            "order_size_max": MAX_CONTRACTS, "order_price_round": 0.0001}

def round_to_tick(price, tick): return round(price / tick) * tick

# ============================================================
# INDICATORS
# ============================================================
def atr(df, period=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()

def rsi(series, period=14):
    d = series.diff()
    g = d.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))

def supertrend(df, period=10, mult=3.0):
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    a = atr(df, period).values
    hl2 = (h + l) / 2
    up, lo = hl2 + mult * a, hl2 - mult * a
    n = len(c)
    fu, fl = np.full(n, np.nan), np.full(n, np.nan)
    trend = np.ones(n, dtype=int)
    for i in range(int(period), n):
        if np.isnan(up[i]): continue
        if np.isnan(fu[i - 1]):
            fu[i], fl[i] = up[i], lo[i]
            trend[i] = 1 if c[i] > fl[i] else -1
            continue
        fu[i] = up[i] if (up[i] < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lo[i] if (lo[i] > fl[i - 1] or c[i - 1] < fl[i - 1]) else fl[i - 1]
        trend[i] = (-1 if c[i] < fl[i] else 1) if trend[i - 1] == 1 else (1 if c[i] > fu[i] else -1)
    return pd.Series(trend, index=df.index)

def qqe(df, rsi_p=14, smooth=5, factor=4.236):
    c = df["close"]
    r = rsi(c, rsi_p)
    rma = r.ewm(span=smooth, adjust=False).mean()
    atr_r = rma.diff().abs().ewm(alpha=1 / (2 * rsi_p - 1), adjust=False).mean()
    dar = atr_r * factor
    n = len(c)
    trend = np.zeros(n, dtype=int); trend[0] = 1
    lb, sb = np.full(n, np.nan), np.full(n, np.nan)
    lb[0], sb[0] = rma.iloc[0] - dar.iloc[0], rma.iloc[0] + dar.iloc[0]
    rn, dn = rma.values, dar.values
    for i in range(1, n):
        nl, ns = rn[i] - dn[i], rn[i] + dn[i]
        lb[i] = max(lb[i - 1], nl) if (rn[i - 1] > lb[i - 1] and rn[i] > lb[i - 1]) else nl
        sb[i] = min(sb[i - 1], ns) if (rn[i - 1] < sb[i - 1] and rn[i] < sb[i - 1]) else ns
        if rn[i] > sb[i - 1]:   trend[i] = 1
        elif rn[i] < lb[i - 1]: trend[i] = -1
        else:                   trend[i] = trend[i - 1]
    return pd.Series(trend, index=df.index)

def wma(s, p):
    w = np.arange(1, p + 1)
    return s.rolling(p).apply(lambda x: np.dot(x, w) / w.sum(), raw=True)

def hma(s, p):
    hl = int(p / 2); sl = int(np.sqrt(p))
    return wma(2 * wma(s, hl) - wma(s, p), sl)

def tsi(s, long=25, short=13):
    m = s.diff(); am = m.abs()
    sm = m.ewm(span=long, adjust=False).mean().ewm(span=short, adjust=False).mean()
    sa = am.ewm(span=long, adjust=False).mean().ewm(span=short, adjust=False).mean()
    return 100 * (sm / sa)

# ============================================================
# SIGNALS
# ============================================================
def get_raw_signals(df, strat, params):
    close = df["close"]
    sig = np.zeros(len(df), dtype=int)

    if strat == "MACD":
        fast = close.ewm(span=params["fast"], adjust=False).mean()
        slow = close.ewm(span=params["slow"], adjust=False).mean()
        macd = fast - slow
        s = macd.ewm(span=params["signal"], adjust=False).mean()
        hist = macd - s
        st = supertrend(df, 10, 3.0)
        sig[(hist > 0) & (hist.shift(1) <= 0) & (st == 1)] = 1
        sig[(hist < 0) & (hist.shift(1) >= 0) & (st == -1)] = -1

    elif strat == "QQE":
        q = qqe(df, params["rsi_period"], params["smooth"], params["factor"])
        sig[(q == 1) & (q.shift(1) == -1)] = 1
        sig[(q == -1) & (q.shift(1) == 1)] = -1

    elif strat == "Supertrend":
        st = supertrend(df, params["period"], params["mult"])
        sig[(st == 1) & (st.shift(1) == -1)] = 1
        sig[(st == -1) & (st.shift(1) == 1)] = -1

    elif strat == "Volume":
        vma = df["volume"].rolling(params["ma_period"]).mean()
        vok = df["volume"] > params["vol_mult"] * vma
        sig[vok & (close > close.shift(1))] = 1
        sig[vok & (close < close.shift(1))] = -1

    return sig

def apply_filter(df, sig, filt):
    if filt == "NONE": return sig
    close = df["close"]
    bull = np.zeros(len(df), dtype=bool); bear = np.zeros(len(df), dtype=bool)

    if filt == "HMA_20":
        h = hma(close, 20)
        bull, bear = (close > h).values, (close < h).values
    elif filt == "TSI_SENS":
        t = tsi(close)
        bull, bear = (t > 0).values, (t < 0).values

    out = sig.copy()
    out[(sig == 1) & (~bull)] = 0
    out[(sig == -1) & (~bear)] = 0
    return out

def get_last_signal(df, strat, params, filt):
    raw = get_raw_signals(df, strat, params)
    filtered = apply_filter(df, raw, filt)
    return int(filtered[-1])

# ============================================================
# DATA & ACCOUNT
# ============================================================
def fetch_candles(contract, tf, limit=200):
    key = (contract, tf)
    if key in _candles_cache: return _candles_cache[key]
    url = f"{GATE_HOST}/futures/usdt/candlesticks"
    try:
        r = requests.get(url, params={"contract": contract, "interval": tf, "limit": limit}, timeout=15)
        if r.status_code != 200: return None
        data = r.json()
        if not data or len(data) < 100: return None
        df = pd.DataFrame(data)
        df["t"] = pd.to_datetime(df["t"].astype(int), unit="s", utc=True)
        df = df.set_index("t").sort_index()
        for col in ["o", "h", "l", "c", "v"]: df[col] = df[col].astype(float)
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        _candles_cache[key] = df
        return df
    except Exception: return None

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
                        "open_time": int(p.open_time) if hasattr(p, "open_time") and p.open_time else None}
    except Exception: pass
    return None

def set_leverage(contract, lev):
    try: futures_api.update_position_leverage("usdt", contract, str(lev))
    except Exception: pass

# ============================================================
# ORDERS
# ============================================================
def place_market_order(contract, size):
    order = FuturesOrder(contract=contract, size=size, price="0", tif="ioc", reduce_only=False)
    try:
        futures_api.create_futures_order("usdt", order)
        print(f"  🧪 {'LONG' if size > 0 else 'SHORT'} {contract} size={abs(size)}")
        return True
    except Exception as e:
        msg = str(e)
        if "MARKET_PRICE_TOO_DEVIATED" in msg:
            print(f"  ⏸️ {contract}: slippage")
        elif "INSUFFICIENT_AVAILABLE" in msg:
            print(f"  ⏸️ {contract}: no margin")
        else:
            print(f"  ❌ {contract}: {e}")
        return False

def close_position_market(contract, current_size):
    order = FuturesOrder(contract=contract, size=0, price="0", tif="ioc", reduce_only=True, close=True)
    try:
        futures_api.create_futures_order("usdt", order)
        print(f"  🧪 CLOSE {contract}")
    except Exception as e:
        print(f"  ❌ Close {contract}: {e}")

def place_sl_tp(contract, side, sl_price, tp_price, entry_price, size):
    specs = get_contract_specs(contract)
    tick = specs["order_price_round"]
    sl_price = round_to_tick(sl_price, tick)
    tp_price = round_to_tick(tp_price, tick)
    sl_rule = 2 if side == "long" else 1
    tp_rule = 1 if side == "long" else 2
    order_type = "close-long-position" if side == "long" else "close-short-position"

    for kind, price, rule in [("SL", sl_price, sl_rule), ("TP", tp_price, tp_rule)]:
        order = FuturesPriceTriggeredOrder(
            initial=FuturesInitialOrder(contract=contract, size=0, price="0", tif="ioc", reduce_only=True, close=True),
            trigger=FuturesPriceTrigger(strategy_type=0, price_type=0, price=f"{price:.10f}", rule=rule, expiration=86400),
            order_type=order_type,
        )
        try:
            futures_api.create_price_triggered_order("usdt", order)
            print(f"  🧪 {kind} {contract} @ {price:.6f}")
        except Exception as e:
            print(f"  ⚠️ {kind} {contract}: {e}")

# ============================================================
# REPORTS
# ============================================================
def send_positions_report():
    try: positions = futures_api.list_positions("usdt")
    except Exception: return
    active = [p for p in positions if int(p.size) != 0]
    if not active:
        send_telegram("📭 <b>No open positions</b>")
        return
    now = datetime.now(timezone.utc).timestamp()
    total_pnl = 0.0
    lines = [f"📊 <b>Open Positions ({len(active)})</b>\n"]
    for p in active:
        size = int(p.size); side = "LONG" if size > 0 else "SHORT"
        entry = float(p.entry_price); mark = float(p.mark_price); pnl = float(p.unrealised_pnl)
        total_pnl += pnl
        dur = "N/A"
        if hasattr(p, "open_time") and p.open_time:
            d = now - int(p.open_time)
            dur = f"{int(d // 3600)}h {int((d % 3600) // 60)}m"
        pct = ((mark - entry) / entry * 100) if side == "LONG" else ((entry - mark) / entry * 100)
        lines.append(
            f"{'🟢' if side=='LONG' else '🔴'} <b>{p.contract}</b> {side} | {abs(size)}\n"
            f"   ⏱ {dur} | Entry: {entry:.6f} | Mark: {mark:.6f}\n"
            f"   PnL: {'✅' if pnl>=0 else '❌'} {pnl:+.4f} USDT ({pct:+.2f}%)\n"
        )
    lines.append(f"\n💰 <b>Total: {total_pnl:+.4f} USDT</b>")
    send_telegram("\n".join(lines))

def send_trade_stats():
    try: closed = futures_api.list_position_close("usdt", limit=50)
    except Exception: return
    if not closed: return
    total = len(closed)
    wins = sum(1 for p in closed if float(p.pnl) > 0)
    total_pnl = sum(float(p.pnl) for p in closed)
    wr = (wins / total * 100) if total > 0 else 0
    send_telegram(
        f"📈 <b>Last {total} Closed:</b>\n"
        f"✅ {wins}W | ❌ {total-wins}L\n"
        f"🎯 WR: {wr:.1f}% | 💰 {total_pnl:+.4f} USDT\n"
        f"🧪 <i>TESTNET</i>"
    )

# ============================================================
# TRADING LOGIC — CROSS-STRATEGY
# ============================================================
def process_config(cfg):
    symbol = cfg["symbol"]
    tf = cfg["timeframe"]
    entry_strat = cfg["entry_strategy"]; entry_params = cfg["entry_params"]; entry_filt = cfg["entry_filter"]
    exit_strat  = cfg["exit_strategy"];  exit_params  = cfg["exit_params"];  exit_filt  = cfg["exit_filter"]

    contract = resolve_contract(symbol)
    if contract is None: return

    df = fetch_candles(contract, tf, limit=200)
    if df is None or len(df) < 100: return

    state = load_json(STATE_FILE)
    cooldown = load_json(COOLDOWN_FILE)
    pos_info = get_position_info(contract)
    current_pos = pos_info["size"] if pos_info else 0

    entry_sig = get_last_signal(df, entry_strat, entry_params, entry_filt)
    exit_sig  = get_last_signal(df, exit_strat,  exit_params,  exit_filt)

    my_key = f"{symbol}|{tf}|{entry_strat}->{exit_strat}"

    # Cooldown check
    if contract in cooldown and time.time() - cooldown[contract] < COOLDOWN_HOURS * 3600:
        return

    if current_pos == 0:
        # No open position → look for entry
        if contract in state:
            cooldown[contract] = time.time()
            save_json(COOLDOWN_FILE, cooldown)
            del state[contract]
            save_json(STATE_FILE, state)

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

            # Margin limit check
            req_margin = (size * mult * price) / LEVERAGE
            max_margin = equity * MAX_MARGIN_PCT
            if req_margin > max_margin:
                size = int(size * (max_margin / req_margin))
                req_margin = (size * mult * price) / LEVERAGE

            available = _available_cache.get("usdt", 0.0)
            if req_margin > available:
                print(f"  ⏸️ {symbol}: need {req_margin:.2f}, only {available:.2f}")
                return

            set_leverage(contract, LEVERAGE)

            if entry_sig == 1:
                print(f"  📈 LONG {symbol} {tf} [{entry_strat}→{exit_strat}]")
                if place_market_order(contract, size):
                    state[contract] = my_key
                    save_json(STATE_FILE, state)
                    place_sl_tp(contract, "long", price - sl_dist, price + tp_dist, price, size)
                    send_telegram(
                        f"📈 <b>LONG</b> {symbol} {tf}\n"
                        f"🎯 {entry_strat} → {exit_strat}\n"
                        f"💰 {price:.6f} | 📦 {size}\n"
                        f"🛡️ {price - sl_dist:.6f} | 🎯 {price + tp_dist:.6f}\n"
                        f"🧪 <i>TESTNET</i>"
                    )
            elif entry_sig == -1:
                print(f"  📉 SHORT {symbol} {tf} [{entry_strat}→{exit_strat}]")
                if place_market_order(contract, -size):
                    state[contract] = my_key
                    save_json(STATE_FILE, state)
                    place_sl_tp(contract, "short", price + sl_dist, price - tp_dist, price, size)
                    send_telegram(
                        f"📉 <b>SHORT</b> {symbol} {tf}\n"
                        f"🎯 {entry_strat} → {exit_strat}\n"
                        f"💰 {price:.6f} | 📦 {size}\n"
                        f"🛡️ {price + sl_dist:.6f} | 🎯 {price - tp_dist:.6f}\n"
                        f"🧪 <i>TESTNET</i>"
                    )

    else:
        # Position open → check cross-strategy exit
        owner = state.get(contract)
        if owner is None:
            state[contract] = my_key
            save_json(STATE_FILE, state)
            owner = my_key
        if owner != my_key: return

        # ✅ CROSS-STRATEGY EXIT: opposite signal from EXIT strategy
        should_exit = (current_pos > 0 and exit_sig == -1) or (current_pos < 0 and exit_sig == 1)

        if should_exit:
            print(f"  🔄 Reverse {symbol} [exit_strat={exit_strat}]")
            pnl = pos_info["unrealised_pnl"]
            open_time = pos_info["open_time"]
            dur = "N/A"
            if open_time:
                d = datetime.now(timezone.utc).timestamp() - open_time
                dur = f"{int(d // 3600)}h {int((d % 3600) // 60)}m"

            close_position_market(contract, current_pos)
            del state[contract]
            save_json(STATE_FILE, state)
            cooldown[contract] = time.time()
            save_json(COOLDOWN_FILE, cooldown)

            side = "LONG" if current_pos > 0 else "SHORT"
            send_telegram(
                f"🚪 <b>CLOSED</b> {side} {symbol} {tf}\n"
                f"🎯 {entry_strat} → {exit_strat}\n"
                f"⏱ {dur} | 📊 {'✅' if pnl>=0 else '❌'} {pnl:+.4f} USDT\n"
                f"🧪 <i>TESTNET</i>"
            )

# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 70)
    print(f"🧪 TESTNET — CROSS-STRATEGY GOLDEN 15")
    print(f"🌐 {GATE_HOST}")
    print(f"⚙️ Leverage: {LEVERAGE}x | Configs: {len(BEST_CONFIGS)}")
    print("=" * 70)

    try:
        acc = futures_api.list_futures_accounts("usdt")
        equity = float(acc.total)
        available = float(acc.available) if hasattr(acc, "available") else 0.0
        print(f"\n✅ Equity: {equity:.2f} | Available: {available:.2f}\n")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        send_telegram(f"❌ Connection: {e}")
        sys.exit(1)

    _equity_cache["usdt"] = equity
    _available_cache["usdt"] = available

    send_telegram(
        f"🧪 <b>Cross-Strategy Golden 15</b>\n"
        f"💰 ${equity:.2f} | Free: ${available:.2f}\n"
        f"⚙️ {LEVERAGE}x | 📊 {len(BEST_CONFIGS)} configs\n"
        f"🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"
    )

    send_positions_report()

    print(f"🚀 Processing {len(BEST_CONFIGS)} configs...\n")
    for cfg in BEST_CONFIGS:
        try:
            process_config(cfg)
        except Exception as e:
            print(f"  ❌ {cfg['symbol']}: {e}")

    send_positions_report()
    send_trade_stats()
    print(f"\n✅ Run complete — TESTNET")

if __name__ == "__main__":
    main()
