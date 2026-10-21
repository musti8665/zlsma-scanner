#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Multi-Timeframe Multi-Strategy Scanner: ParkF + ACMTS
Scans 25 symbols across multiple timeframes (5m, 15m, 30m, 1h, 4h)
"""

import os
import sys
import json
import csv
import warnings
import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import pandas as pd
import numpy as np

warnings.filterwarnings('ignore')

# ============================================================
# CONFIG
# ============================================================
TIMEFRAMES = ['5m', '15m', '30m', '1h', '4h']
LIMIT = 500
CHECK_BARS = 1

TG_TOKEN = os.getenv('TELEGRAM_TOKEN', '').strip()
TG_CHAT  = os.getenv('TELEGRAM_CHAT_ID', '').strip()

EXCHANGES = ['kucoin', 'okx', 'binance', 'bybit']

# ============================================================
# لیست ۲۵ نماد شما (تبدیل شده به فرمت ccxt)
# ============================================================
SYMBOLS = [
    "SHIB/USDT", "ICP/USDT", "SEI/USDT", "NOT/USDT", "WIF/USDT",
    "AVAX/USDT", "WLD/USDT", "ADA/USDT", "NEAR/USDT", "OP/USDT",
    "BONK/USDT", "SOL/USDT", "INJ/USDT", "FIL/USDT", "ARB/USDT",
    "GALA/USDT", "AAVE/USDT", "THETA/USDT", "RUNE/USDT", "DOGE/USDT",
    "HBAR/USDT", "HYPE/USDT", "ALGO/USDT", "FET/USDT", "ONDO/USDT",
]

# ============================================================
# پارامترهای تایید شده (بدون MACD-SR)
# ============================================================
PARKF_PARAMS = {
    'length': 50, 'sigma': 1.0, 'sl_atr_mult': 2.0, 'max_bars': 50,
}
ACMTS_PARAMS = {
    'dc_4h_period': 15, 'width_lookback': 100,
    'trend_threshold': 1.8, 'range_threshold': 0.6,
    'dc_1h_period': 20, 'bb_1h_period': 20, 'bb_1h_std': 2.0,
    'sl_atr_mult': 3.5, 'tp_rr_trend': 2.0, 'max_bars': 15,
}

# ============================================================
# Cache markets
# ============================================================
_MARKETS_CACHE = {}

def get_exchange(ex_name):
    if ex_name not in _MARKETS_CACHE:
        ex = getattr(ccxt, ex_name)()
        ex.load_markets()
        _MARKETS_CACHE[ex_name] = ex
    return _MARKETS_CACHE[ex_name]

def fetch_ohlcv(symbol, timeframe, limit=500):
    for ex_name in EXCHANGES:
        try:
            ex = get_exchange(ex_name)
            if symbol not in ex.markets:
                continue
            data = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            if not data or len(data) < 100:
                continue
            df = pd.DataFrame(data, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
            df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
            return df.drop_duplicates('ts').set_index('ts').sort_index()
        except Exception:
            continue
    return None

def compute_atr(df, length=14):
    high, low, close = df['high'].values, df['low'].values, df['close'].values
    tr = pd.Series(np.maximum(
        high - low,
        np.maximum(np.abs(high - np.roll(close, 1)),
                   np.abs(low - np.roll(close, 1)))
    ))
    tr.iloc[0] = high[0] - low[0]
    return tr.ewm(alpha=1/length, adjust=False).mean().values

# ============================================================
# استراتژی ۱: ParkF
# ============================================================
def detect_parkf(df, params, check_bars=1):
    n = len(df)
    length = params['length']
    if n < length + 5: return None
    close, high, low = df['close'].values, df['high'].values, df['low'].values
    atr = compute_atr(df)
    shape = (n - length + 1, length)
    strides = (close.strides[0], close.strides[0])
    y_win = np.lib.stride_tricks.as_strided(close, shape=shape, strides=strides)
    x = np.arange(length, dtype=float)
    xm = x.mean()
    xv = ((x - xm) ** 2).sum()
    ym = y_win.mean(axis=1, keepdims=True)
    slopes = ((x - xm) * (y_win - ym)).sum(axis=1) / xv
    ints = ym.flatten() - slopes * xm
    tv = ints[:, None] + slopes[:, None] * x
    resid = y_win - tv
    stds = np.sqrt((resid ** 2).mean(axis=1))
    base = np.full(n, np.nan)
    std_arr = np.full(n, np.nan)
    base[length - 1:] = ints
    std_arr[length - 1:] = stds
    width = std_arr / np.where(base > 0, base, np.nan)
    w_med = np.nanmedian(width)
    upper = base + params['sigma'] * std_arr
    lower = base - params['sigma'] * std_arr
    end_bar = n - 1
    start_bar = max(length, end_bar - check_bars + 1)
    for t in range(end_bar, start_bar - 1, -1):
        if np.isnan(base[t]) or np.isnan(lower[t]): continue
        if np.isnan(width[t]) or width[t] > w_med: continue
        p, hi, lo, a = close[t], high[t], low[t], atr[t]
        if lo <= lower[t] and p > lower[t]:
            entry, sl, tp = p, p - params['sl_atr_mult'] * a, base[t]
            if 0 < (entry - sl) / entry < 0.15 and tp > entry:
                return {'strategy': 'ParkF', 'side': 'LONG', 'regime': 'RANGE', 'entry_price': round(entry, 8), 'sl_price': round(sl, 8), 'tp_price': round(tp, 8), 'sl_pct': round((entry - sl) / entry * 100, 2), 'tp_pct': round((tp - entry) / entry * 100, 2), 'bars_ago': n - 1 - t, 'reason': 'Touch lower band (tight channel)'}
        if hi >= upper[t] and p < upper[t]:
            entry, sl, tp = p, p + params['sl_atr_mult'] * a, base[t]
            if 0 < (sl - entry) / entry < 0.15 and tp < entry:
                return {'strategy': 'ParkF', 'side': 'SHORT', 'regime': 'RANGE', 'entry_price': round(entry, 8), 'sl_price': round(sl, 8), 'tp_price': round(tp, 8), 'sl_pct': round((sl - entry) / entry * 100, 2), 'tp_pct': round((entry - tp) / entry * 100, 2), 'bars_ago': n - 1 - t, 'reason': 'Touch upper band (tight channel)'}
    return None

# ============================================================
# استراتژی ۲: ACMTS
# ============================================================
def get_higher_tf(tf):
    mapping = {'5m': '1h', '15m': '4h', '30m': '4h', '1h': '4h', '4h': '1d'}
    return mapping.get(tf, '4h')

def compute_current_regime(df_htf, params):
    if len(df_htf) < params['dc_4h_period'] + params['width_lookback']: return 0
    h4, l4 = df_htf['high'].values, df_htf['low'].values
    dc_h4 = pd.Series(h4).rolling(params['dc_4h_period']).max().values
    dc_l4 = pd.Series(l4).rolling(params['dc_4h_period']).min().values
    mid4 = (dc_h4 + dc_l4) / 2
    width4 = (dc_h4 - dc_l4) / np.where(mid4 > 0, mid4, 1)
    w_med = pd.Series(width4).rolling(params['width_lookback']).median().values
    ratio = width4 / np.where(w_med > 0, w_med, 1)
    if len(ratio) == 0 or np.isnan(ratio[-1]): return 0
    if ratio[-1] > params['trend_threshold']: return 1
    if ratio[-1] < params['range_threshold']: return -1
    return 0

def detect_acmts(df_1h, df_htf, params, check_bars=1):
    if df_htf is None or len(df_htf) < 50: return None
    regime = compute_current_regime(df_htf, params)
    if regime == 0: return None
    n = len(df_1h)
    if n < params['dc_1h_period'] + 10: return None
    close, high, low = df_1h['close'].values, df_1h['high'].values, df_1h['low'].values
    atr = compute_atr(df_1h)
    end_bar = n - 1
    start_bar = max(params['dc_1h_period'], end_bar - check_bars + 1)
    if regime == 1:
        dc_h1 = pd.Series(high).rolling(params['dc_1h_period']).max().shift(1).values
        dc_l1 = pd.Series(low).rolling(params['dc_1h_period']).min().shift(1).values
        for t in range(end_bar, start_bar - 1, -1):
            if np.isnan(dc_h1[t]) or np.isnan(atr[t]): continue
            if close[t-1] <= dc_h1[t] and close[t] > dc_h1[t]:
                entry, sl, tp = close[t], close[t] - params['sl_atr_mult'] * atr[t], close[t] + params['tp_rr_trend'] * (close[t] - (close[t] - params['sl_atr_mult'] * atr[t]))
                return {'strategy': 'ACMTS', 'side': 'LONG', 'regime': 'TREND', 'entry_price': round(entry, 8), 'sl_price': round(sl, 8), 'tp_price': round(tp, 8), 'sl_pct': round((entry - sl) / entry * 100, 2), 'tp_pct': round((tp - entry) / entry * 100, 2), 'bars_ago': n - 1 - t, 'reason': 'Breakout above Donchian (trend)'}
            if close[t-1] >= dc_l1[t] and close[t] < dc_l1[t]:
                entry, sl, tp = close[t], close[t] + params['sl_atr_mult'] * atr[t], close[t] - params['tp_rr_trend'] * ((close[t] + params['sl_atr_mult'] * atr[t]) - close[t])
                return {'strategy': 'ACMTS', 'side': 'SHORT', 'regime': 'TREND', 'entry_price': round(entry, 8), 'sl_price': round(sl, 8), 'tp_price': round(tp, 8), 'sl_pct': round((sl - entry) / entry * 100, 2), 'tp_pct': round((entry - tp) / entry * 100, 2), 'bars_ago': n - 1 - t, 'reason': 'Breakdown below Donchian (trend)'}
    elif regime == -1:
        bb_mid = pd.Series(close).rolling(params['bb_1h_period']).mean().values
        bb_std = pd.Series(close).rolling(params['bb_1h_period']).std().values
        bb_u, bb_l = bb_mid + params['bb_1h_std'] * bb_std, bb_mid - params['bb_1h_std'] * bb_std
        for t in range(end_bar, start_bar - 1, -1):
            if np.isnan(bb_l[t]) or np.isnan(atr[t]): continue
            if low[t] <= bb_l[t] and close[t] > bb_l[t]:
                entry, sl, tp = close[t], close[t] - params['sl_atr_mult'] * atr[t], bb_mid[t]
                if tp > entry and entry - sl > 0:
                    return {'strategy': 'ACMTS', 'side': 'LONG', 'regime': 'RANGE', 'entry_price': round(entry, 8), 'sl_price': round(sl, 8), 'tp_price': round(tp, 8), 'sl_pct': round((entry - sl) / entry * 100, 2), 'tp_pct': round((tp - entry) / entry * 100, 2), 'bars_ago': n - 1 - t, 'reason': 'Bounce from lower BB (range)'}
            if high[t] >= bb_u[t] and close[t] < bb_u[t]:
                entry, sl, tp = close[t], close[t] + params['sl_atr_mult'] * atr[t], bb_mid[t]
                if tp < entry and sl - entry > 0:
                    return {'strategy': 'ACMTS', 'side': 'SHORT', 'regime': 'RANGE', 'entry_price': round(entry, 8), 'sl_price': round(sl, 8), 'tp_price': round(tp, 8), 'sl_pct': round((sl - entry) / entry * 100, 2), 'tp_pct': round((entry - tp) / entry * 100, 2), 'bars_ago': n - 1 - t, 'reason': 'Rejection from upper BB (range)'}
    return None

# ============================================================
# Telegram
# ============================================================
def send_telegram(text):
    if not TG_TOKEN or not TG_CHAT: return False
    try:
        import urllib.request, urllib.parse
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        if len(text) <= 4000:
            data = urllib.parse.urlencode({'chat_id': TG_CHAT, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': 'true'}).encode()
            urllib.request.urlopen(url, data, timeout=15)
            return True
        chunks, current = [], ""
        for line in text.split('\n'):
            if len(current) + len(line) + 1 <= 4000: current += line + '\n'
            else: chunks.append(current); current = line + '\n'
        if current: chunks.append(current)
        for chunk in chunks:
            data = urllib.parse.urlencode({'chat_id': TG_CHAT, 'text': chunk, 'parse_mode': 'HTML', 'disable_web_page_preview': 'true'}).encode()
            urllib.request.urlopen(url, data, timeout=15)
            time.sleep(1)
        return True
    except Exception as e:
        print(f"[!] Telegram: {e}")
        return False

# ============================================================
# MAIN
# ============================================================
def main():
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    print("=" * 85)
    print(f"🎯 Multi-TF Multi-Strategy Scanner | {now}")
    print(f"Timeframes: {', '.join(TIMEFRAMES)}")
    print(f"Strategies: ParkF + ACMTS")
    print(f"Symbols: {len(SYMBOLS)}")
    print("=" * 85)

    all_signals = []
    
    for tf in TIMEFRAMES:
        print(f"\n--- Scanning {tf} ---")
        htf = get_higher_tf(tf)
        
        for i, symbol in enumerate(SYMBOLS, 1):
            tag = f"[{tf}] [{i:02d}/{len(SYMBOLS)}] {symbol:14s}"
            
            df_1h = fetch_ohlcv(symbol, tf, LIMIT)
            if df_1h is None:
                print(f"{tag} ✗ data")
                continue
            
            df_htf = fetch_ohlcv(symbol, htf, LIMIT) if htf else None
            found = False

            # 1. ParkF
            try:
                sig = detect_parkf(df_1h, PARKF_PARAMS, CHECK_BARS)
                if sig:
                    sig['symbol'] = symbol; sig['timeframe'] = tf
                    all_signals.append(sig); found = True
                    print(f"{tag} {'🟢' if sig['side'] == 'LONG' else '🔴'} ParkF {sig['side']:5s} | Entry={sig['entry_price']:.6g}")
            except Exception: pass

            # 2. ACMTS
            if not found and df_htf is not None:
                try:
                    sig = detect_acmts(df_1h, df_htf, ACMTS_PARAMS, CHECK_BARS)
                    if sig:
                        sig['symbol'] = symbol; sig['timeframe'] = tf
                        all_signals.append(sig); found = True
                        print(f"{tag} {'🟢' if sig['side'] == 'LONG' else '🔴'} ACMTS[{sig['regime']}] {sig['side']:5s} | Entry={sig['entry_price']:.6g}")
                except Exception: pass

            if not found:
                print(f"{tag} ·")

    # ============================================================
    # Telegram Output
    # ============================================================
    if all_signals and TG_TOKEN and TG_CHAT:
        lines = [f"🎯 <b>سیگنال‌های چند تایم‌فریمی</b> — {now}", ""]
        
        for tf in TIMEFRAMES:
            sigs_tf = [s for s in all_signals if s.get('timeframe') == tf]
            if not sigs_tf: continue
            
            lines.append(f"<b>🕒 تایم‌فریم: {tf}</b>")
            for s in sigs_tf:
                emoji = '🟢' if s['side'] == 'LONG' else '🔴'
                regime_str = f" [{s.get('regime', '')}]" if s.get('regime') else ""
                lines.append(
                    f"{emoji} <b>{s['symbol']}</b> | {s['strategy']}{regime_str} {s['side']}\n"
                    f"   ورود: <code>{s['entry_price']:.6g}</code> | SL: {s['sl_pct']:.2f}% | TP: {s['tp_pct']:.2f}%"
                )
            lines.append("")
        
        lines.append("⚠️ <i>این پیام صرفاً تحلیل است، نه سیگنال خرید/فروش.</i>")
        
        if send_telegram("\n".join(lines)):
            print("\n✅ Telegram sent")
    else:
        print("\nNo alerts to send.")

if __name__ == '__main__':
    main()
