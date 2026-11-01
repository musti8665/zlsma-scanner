#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live Binance Futures Testnet Trader
====================================
- Reads best_configs.json
- Filters to only USDT-quoted symbols (Binance USD-M Futures)
- Polls closed candles, generates signals, places market orders
- Supports real Long/Short with exchange-side SL/TP
- Uses Binance Futures Testnet (testnet.binancefuture.com)
"""

import os
import json
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN

import pandas as pd
import numpy as np
from binance.client import Client
from binance.exceptions import BinanceAPIException

# ============================================================
# CONFIG
# ============================================================
API_KEY = os.environ.get("BINANCE_API_KEY", "qGEVAOupRbdzcUf3LCOFmpHVoBvncqO4R8lJQSHYNrEe8MthJ4OoFTRClJ3le5Ng")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "iRXxFB4hrhjVCVlM4fOnAEAz0tam1xR1zcWm8Db0vta46y8c3Pn6vDzP5RkS1Ixs")

CONFIG_FILE = "best_configs.json"

RISK_PCT = 0.02
LEVERAGE = 3
SL_ATR_MULT = 2.5
TP_ATR_MULT = 2.5

TF_TO_INTERVAL = {"5m": "5m", "15m": "15m", "30m": "30m"}
TF_TO_SECONDS = {"5m": 300, "15m": 900, "30m": 1800}

# فقط نمادهایی که در بایننس فیوچرز موجودند
SUPPORTED_QUOTE = "USDT"

# ============================================================
# BINANCE CLIENT (TESTNET)
# ============================================================
client = Client(API_KEY, API_SECRET, testnet=True)

def to_binance_symbol(symbol: str) -> str:
    return symbol.replace("/", "")

# ============================================================
# DATA FETCH
# ============================================================
def fetch_klines(symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
    """کندل‌های بسته‌شده را برمی‌گرداند (کندل آخر حذف می‌شود)."""
    raw = client.futures_klines(
        symbol=to_binance_symbol(symbol),
        interval=interval,
        limit=limit,
    )
    df = pd.DataFrame(raw, columns=[
        "t", "open", "high", "low", "close", "volume",
        "close_time", "quote_vol", "trades", "tb_base", "tb_quote", "ignore"
    ])
    df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    df = df.set_index("t").sort_index()
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    # حذف کندل آخر که هنوز بسته نشده
    return df.iloc[:-1]

# ============================================================
# INDICATORS (همان منطق)
# ============================================================
def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, np.nan))

def supertrend(df: pd.DataFrame, period: int = 10, mult: float = 3.0) -> pd.Series:
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    a = atr(df, period).values
    hl2 = (h + l) / 2
    up_u, up_l = hl2 + mult * a, hl2 - mult * a
    n = len(c)
    fu, fl = np.full(n, np.nan), np.full(n, np.nan)
    trend = np.ones(n, dtype=int)
    for i in range(int(period), n):
        if np.isnan(up_u[i]):
            continue
        if np.isnan(fu[i - 1]):
            fu[i], fl[i] = up_u[i], up_l[i]
            trend[i] = 1 if c[i] > fl[i] else -1
            continue
        fu[i] = up_u[i] if (up_u[i] < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = up_l[i] if (up_l[i] > fl[i - 1] or c[i - 1] < fl[i - 1]) else fl[i - 1]
        trend[i] = (-1 if c[i] < fl[i] else 1) if trend[i - 1] == 1 else (1 if c[i] > fu[i] else -1)
    return pd.Series(trend, index=df.index)

def qqe(df: pd.DataFrame, rsi_p: int = 14, smooth: int = 5, factor: float = 4.236) -> pd.Series:
    c = df["close"]
    r = rsi(c, rsi_p)
    rma = r.ewm(span=smooth, adjust=False).mean()
    rdiff = rma.diff().abs()
    atr_r = rdiff.ewm(alpha=1 / (2 * rsi_p - 1), adjust=False).mean()
    dar = atr_r * factor
    n = len(c)
    trend = np.zeros(n, dtype=int)
    trend[0] = 1
    lb, sb = np.full(n, np.nan), np.full(n, np.nan)
    lb[0], sb[0] = rma.iloc[0] - dar.iloc[0], rma.iloc[0] + dar.iloc[0]
    rn, dn_arr = rma.values, dar.values
    for i in range(1, n):
        nl, ns = rn[i] - dn_arr[i], rn[i] + dn_arr[i]
        lb[i] = max(lb[i - 1], nl) if (rn[i - 1] > lb[i - 1] and rn[i] > lb[i - 1]) else nl
        sb[i] = min(sb[i - 1], ns) if (rn[i - 1] < sb[i - 1] and rn[i] < sb[i - 1]) else ns
        if rn[i] > sb[i - 1]:
            trend[i] = 1
        elif rn[i] < lb[i - 1]:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]
    return pd.Series(trend, index=df.index)

def generate_signal(df: pd.DataFrame, strategy: str, params: dict) -> dict:
    if strategy == "MACD":
        close = df["close"]
        fast = close.ewm(span=params["fast"], adjust=False).mean()
        slow = close.ewm(span=params["slow"], adjust=False).mean()
        macd = fast - slow
        sig = macd.ewm(span=params["signal"], adjust=False).mean()
        hist = macd - sig
        st = supertrend(df, 10, 3.0)
        entry_long = (hist.iloc[-1] > 0) and (hist.iloc[-2] <= 0)
        entry_short = (hist.iloc[-1] < 0) and (hist.iloc[-2] >= 0)
        exit_long = (hist.iloc[-1] < 0) or (st.iloc[-1] == -1)
        exit_short = (hist.iloc[-1] > 0) or (st.iloc[-1] == 1)
    elif strategy == "QQE":
        q = qqe(df, params["rsi_period"], params["smooth"], params["factor"])
        entry_long = (q.iloc[-1] == 1) and (q.iloc[-2] == -1)
        entry_short = (q.iloc[-1] == -1) and (q.iloc[-2] == 1)
        exit_long = q.iloc[-1] == -1
        exit_short = q.iloc[-1] == 1
    elif strategy == "Volume":
        vma = df["volume"].rolling(params["ma_period"]).mean()
        vol_ok = df["volume"] > params["vol_mult"] * vma
        close = df["close"]
        entry_long = bool(vol_ok.iloc[-1] and close.iloc[-1] > close.iloc[-2])
        entry_short = bool(vol_ok.iloc[-1] and close.iloc[-1] < close.iloc[-2])
        exit_long = close.iloc[-1] < close.iloc[-2]
        exit_short = close.iloc[-1] > close.iloc[-2]
    elif strategy == "Supertrend":
        st = supertrend(df, params["period"], params["mult"])
        entry_long = (st.iloc[-1] == 1) and (st.iloc[-2] == -1)
        entry_short = (st.iloc[-1] == -1) and (st.iloc[-2] == 1)
        exit_long = st.iloc[-1] == -1
        exit_short = st.iloc[-1] == 1
    else:
        raise ValueError(strategy)
    return {
        "entry_long": bool(entry_long),
        "entry_short": bool(entry_short),
        "exit_long": bool(exit_long),
        "exit_short": bool(exit_short),
    }

# ============================================================
# ACCOUNT & ORDER HELPERS
# ============================================================
def get_usdt_balance() -> float:
    balances = client.futures_account_balance()
    for b in balances:
        if b["asset"] == "USDT":
            return float(b["availableBalance"])
    return 0.0

def get_symbol_info(symbol: str) -> dict:
    info = client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            return s
    raise ValueError(f"Symbol not found: {symbol}")

def get_qty_step(symbol: str) -> Decimal:
    info = get_symbol_info(symbol)
    for f in info["filters"]:
        if f["filterType"] == "LOT_SIZE":
            return Decimal(f["stepSize"])
    return Decimal("0.001")

def round_qty(qty: float, step: Decimal) -> Decimal:
    q = Decimal(str(qty))
    return (q / step).to_integral_value(rounding=ROUND_DOWN) * step

def get_last_price(symbol: str) -> float:
    t = client.futures_symbol_ticker(symbol=symbol)
    return float(t["price"])

def set_leverage(symbol: str, leverage: int = LEVERAGE):
    try:
        client.futures_change_leverage(symbol=symbol, leverage=leverage)
    except BinanceAPIException as e:
        print(f"  [LEVERAGE] {symbol}: {e.message}")

def get_open_position(symbol: str) -> dict | None:
    positions = client.futures_position_information(symbol=symbol)
    for p in positions:
        if float(p["positionAmt"]) != 0:
            return p
    return None

# ============================================================
# TRADE EXECUTION
# ============================================================
def open_position(symbol: str, side: str, balance: float, atr_df: pd.DataFrame):
    price = get_last_price(symbol)
    atr_val = atr(atr_df, 14).iloc[-1]
    if np.isnan(atr_val) or atr_val <= 0:
        print(f"  [SKIP] invalid ATR for {symbol}")
        return

    sl_dist = atr_val * SL_ATR_MULT
    tp_dist = sl_dist * TP_ATR_MULT
    risk_usdt = balance * RISK_PCT
    qty_raw = risk_usdt / sl_dist
    step = get_qty_step(symbol)
    qty = round_qty(qty_raw, step)

    if qty <= 0:
        print(f"  [SKIP] qty=0 for {symbol}")
        return

    set_leverage(symbol)

    order_side = "BUY" if side == "LONG" else "SELL"
    try:
        order = client.futures_create_order(
            symbol=symbol,
            side=order_side,
            type="MARKET",
            quantity=str(qty),
        )
        print(f"  ✅ {side} {symbol} | qty={qty} | price≈{price:.4f}")
    except BinanceAPIException as e:
        print(f"  [ORDER FAILED] {symbol} {side}: {e.message}")
        return

    # ثبت SL و TP به‌صورت سفارش شرطی
    if side == "LONG":
        sl_price = round(price - sl_dist, 4)
        tp_price = round(price + tp_dist, 4)
        close_side = "SELL"
    else:
        sl_price = round(price + sl_dist, 4)
        tp_price = round(price - tp_dist, 4)
        close_side = "BUY"

    for typ, stop in [("STOP_MARKET", sl_price), ("TAKE_PROFIT_MARKET", tp_price)]:
        try:
            client.futures_create_order(
                symbol=symbol,
                side=close_side,
                type=typ,
                stopPrice=stop,
                closePosition=True,
                workingType="MARK_PRICE",
            )
        except BinanceAPIException as e:
            print(f"     [{typ} WARN] {e.message}")

    print(f"     SL≈{sl_price} | TP≈{tp_price}")

def close_position(symbol: str):
    pos = get_open_position(symbol)
    if pos is None:
        return
    amt = float(pos["positionAmt"])
    if amt == 0:
        return
    close_side = "SELL" if amt > 0 else "BUY"
    try:
        client.futures_create_order(
            symbol=symbol,
            side=close_side,
            type="MARKET",
            quantity=str(abs(amt)),
            reduceOnly=True,
        )
        print(f"  🔒 Closed {symbol} | amt={abs(amt)}")
    except BinanceAPIException as e:
        print(f"  [CLOSE FAILED] {symbol}: {e.message}")

# ============================================================
# MAIN LOOP
# ============================================================
def main():
    # خواندن configs و فیلتر USDT
    with open(CONFIG_FILE) as f:
        raw_configs = json.load(f)["configs"]

    configs = []
    skipped = 0
    for c in raw_configs:
        base, quote = c["symbol"].split("/")
        if quote != SUPPORTED_QUOTE:
            skipped += 1
            continue
        # بررسی وجود نماد در بایننس فیوچرز
        try:
            get_symbol_info(to_binance_symbol(c["symbol"]))
            configs.append(c)
        except Exception:
            skipped += 1

    print(f"Loaded {len(configs)} tradable configs (skipped {skipped} non-USDT or unavailable).")
    print("Using Binance Futures Testnet.\n")
    for c in configs:
        print(f"  → {c['symbol']:10s} {c['timeframe']:4s} {c['strategy']:10s}")
    print()

    last_checked = {}

    while True:
        try:
            balance = get_usdt_balance()
            print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC] USDT: {balance:.2f}")

            for cfg in configs:
                sym_pair = cfg["symbol"]
                sym = to_binance_symbol(sym_pair)
                tf = cfg["timeframe"]
                strat = cfg["strategy"]
                params = eval(cfg["params"]) if isinstance(cfg["params"], str) else cfg["params"]

                interval_sec = TF_TO_SECONDS[tf]
                now = time.time()
                key = (sym, tf)

                if key in last_checked and now - last_checked[key] < interval_sec:
                    continue

                try:
                    df = fetch_klines(sym_pair, TF_TO_INTERVAL[tf])
                except BinanceAPIException as e:
                    print(f"  [DATA] {sym_pair} {tf}: {e.message}")
                    continue

                if len(df) < 60:
                    continue

                try:
                    sig = generate_signal(df, strat, params)
                except Exception as e:
                    print(f"  [SIGNAL] {sym_pair} {tf}: {e}")
                    continue

                pos = get_open_position(sym)
                if pos is not None:
                    amt = float(pos["positionAmt"])
                    if amt > 0 and sig["exit_long"]:
                        print(f"  🚪 Exit LONG: {sym_pair} {tf}")
                        close_position(sym)
                    elif amt < 0 and sig["exit_short"]:
                        print(f"  🚪 Exit SHORT: {sym_pair} {tf}")
                        close_position(sym)
                    last_checked[key] = now
                    continue

                if sig["entry_long"]:
                    print(f"  📈 LONG: {sym_pair} {tf} {strat}")
                    open_position(sym, "LONG", balance, df)
                elif sig["entry_short"]:
                    print(f"  📉 SHORT: {sym_pair} {tf} {strat}")
                    open_position(sym, "SHORT", balance, df)

                last_checked[key] = now

            time.sleep(10)

        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print(f"[LOOP ERROR] {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
