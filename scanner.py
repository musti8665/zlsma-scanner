# ============================================================
# Ultimate Scanner: ICT + Multi-Strategy (ChoCh + ParkF + ACMTS)
# ============================================================
import os, json, time, requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
import warnings
warnings.filterwarnings("ignore")
import yfinance as yf
import ccxt

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT  = os.environ.get("TG_CHAT", "")
STATE_FILE = "state.json"
LOG_FILE = 'paper_trades_log.json'

# ---------- تنظیمات اسکنر ICT ----------
ICT_SYMBOLS = [
    "SHIB-USD","ICP-USD","SEI-USD","NOT-USD","WIF-USD","AVAX-USD",
    "WLD-USD","ADA-USD","NEAR-USD","OP-USD","BONK-USD",
    "SOL-USD","INJ-USD","FIL-USD","ARB-USD","GALA-USD","AAVE-USD",
    "THETA-USD","RUNE-USD","DOGE-USD","HBAR-USD",
    "HYPE32196-USD", "ALGO-USD", "FET-USD", "ONDO-USD"
]
ICT_TF_LIST = ["5m", "15m", "1h", "4h"] 
ICT_LOOKBACK_DAYS = 30
PIVOT_LENGTH = 10
MAX_ACTIVE_LEVELS = 5

ANTI_SPAM_TIMES = {"5m": 10 * 60, "15m": 30 * 60, "1h": 60 * 60, "4h": 2 * 60 * 60}
FOLLOW_UP_TIMES = {"5m": 5 * 60, "15m": 15 * 60, "1h": 60 * 60, "4h": 4 * 60 * 60}

# ---------- تنظیمات استراتژی‌های سه‌گانه ----------
TIMEFRAME = '1h'
LIMIT = 500
CHECK_BARS = 1

EXCHANGES = ['binance', 'bybit', 'kucoin', 'okx']
MS_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
    "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "LINK/USDT", "DOT/USDT",
    "NEAR/USDT", "INJ/USDT", "MATIC/USDT", "ARB/USDT", "OP/USDT",
    "ATOM/USDT", "LTC/USDT", "ETC/USDT", "XLM/USDT", "ALGO/USDT",
]

CHOCH_PARAMS = {'swing_length': 5, 'lookback': 60, 'sl_rr': 1.5, 'max_bars': 20, 'risk_pct': 0.02}
PARKF_PARAMS = {'length': 50, 'sigma': 1.0, 'width_filter': 'tight', 'sl_atr_mult': 2.0, 'max_bars': 50, 'risk_pct': 0.02}
ACMTS_PARAMS = {'dc_4h_period': 15, 'width_lookback': 100, 'trend_threshold': 1.8, 'range_threshold': 0.6, 'dc_1h_period': 20, 'bb_1h_period': 20, 'bb_1h_std': 2.0, 'sl_atr_mult': 3.5, 'tp_rr_trend': 2.0, 'max_bars': 15, 'risk_pct': 0.02}

# ==========================================
# 🟢 Paper Trading Logger
# ==========================================
def log_paper_trades(new_signals):
    print("\n" + "=" * 75)
    print(f"📝 Paper Trading Scan — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 75)

    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, 'r') as f:
                log = json.load(f)
            print(f"📂 لاگ موجود: {len(log['trades'])} معامله ثبت‌شده")
        except:
            log = {'start_date': str(datetime.utcnow().date()), 'trades': []}
    else:
        log = {'start_date': str(datetime.utcnow().date()), 'trades': []}
        print("📂 لاگ جدید ایجاد شد")

    # ۱. بستن معاملات باز
    closed_count = 0
    for trade in log['trades']:
        if trade['status'] != 'OPEN': continue
        
        tf = trade['tf']
        tf_seconds = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}.get(tf, 900)
        entry_time = datetime.fromisoformat(trade['entry_time'])
        elapsed = (datetime.utcnow() - entry_time).total_seconds()
        
        if elapsed >= tf_seconds:
            try:
                # استفاده از yfinance برای دریافت قیمت فعلی
                symbol_yf = trade['symbol'].replace("/USDT", "-USD")
                df = yf.download(symbol_yf, period="1d", interval=tf, progress=False)
                if not df.empty:
                    current_price = df['Close'].iloc[-1]
                    entry_price = trade['entry_price']
                    
                    if trade['side'] == 'LONG': pnl = (current_price - entry_price) / entry_price * 100
                    else: pnl = (entry_price - current_price) / entry_price * 100
                        
                    trade['exit_price'] = round(current_price, 8)
                    trade['pnl_pct'] = round(pnl, 3)
                    trade['status'] = 'CLOSED'
                    closed_count += 1
                    print(f"  🔒 {trade['symbol']:12s} | {trade['side']:5s} | PnL: {pnl:+6.2f}% | CLOSED")
            except Exception as e:
                print(f"  ✗ Error closing {trade['symbol']}: {e}")

    # ۲. ثبت سیگنال‌های جدید
    added_count = 0
    for sig in new_signals:
        key = f"{sig['Symbol']}_{sig['TF']}_{sig['Signal']}"
        if any(t.get('key') == key for t in log['trades']): continue

        trade = {
            'key': key, 'symbol': sig['Symbol'], 'side': sig['Signal'], 'tf': sig['TF'],
            'entry_time': str(datetime.utcnow()), 'entry_price': sig['Entry Price'],
            'exit_price': None, 'pnl_pct': 0.0, 'status': 'OPEN', 'reason': sig.get('Strategy', 'Live Signal'),
            'logged_at': str(datetime.utcnow()),
        }
        log['trades'].append(trade)
        added_count += 1
        print(f"  {'🟢' if sig['Signal'] == 'LONG' else '🔴'} {sig['Symbol']:12s} | {sig['Signal']:5s} | TF: {sig['TF']} | {sig.get('Strategy', '')}")

    # ۳. ذخیره
    if added_count > 0 or closed_count > 0:
        with open(LOG_FILE, 'w') as f: json.dump(log, f, indent=2, default=str)
        print(f"\n✅ {added_count} سیگنال جدید | {closed_count} معامله بسته شد")

    # ۴. گزارش
    print("\n📊 گزارش تجمعی Paper Trading:")
    if log['trades']:
        df_log = pd.DataFrame(log['trades'])
        closed = df_log[df_log['status'] == 'CLOSED']
        open_t = df_log[df_log['status'] == 'OPEN']
        print(f"  کل: {len(df_log)} | باز: {len(open_t)} | بسته: {len(closed)}")
        if len(closed) >= 5:
            wins = closed[closed['pnl_pct'] > 0]
            losses = closed[closed['pnl_pct'] <= 0]
            pf = wins['pnl_pct'].sum() / abs(losses['pnl_pct'].sum()) if len(losses) else 999
            wr = len(wins)/len(closed)*100
            print(f"  WR: {wr:.1f}% | PF: {pf:.2f} | بازده: {closed['pnl_pct'].sum():+.2f}%")
    print("=" * 75 + "\n")

# ==========================================
# 🟢 استراتژی‌های سه‌گانه (ChoCh, ParkF, ACMTS)
# ==========================================
def fetch_ohlcv_ccxt(symbol, timeframe, limit=500):
    for ex_name in EXCHANGES:
        try:
            ex = getattr(ccxt, ex_name)()
            ex.load_markets()
            if symbol not in ex.markets: continue
            data = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            if not data or len(data) < 100: continue
            df = pd.DataFrame(data, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
            df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
            return df.drop_duplicates('ts').set_index('ts').sort_index()
        except Exception: continue
    return None

def compute_atr(df, length=14):
    high, low, close = df['high'].values, df['low'].values, df['close'].values
    tr = pd.Series(np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1)))))
    tr.iloc[0] = high[0] - low[0]
    return tr.ewm(alpha=1/length, adjust=False).mean().values

def detect_choch(df, params, check_bars=1):
    n = len(df)
    if n < params['swing_length'] * 2 + 5: return None
    close, high, low = df['close'].values, df['high'].values, df['low'].values
    atr = compute_atr(df)
    swing, lookback = params['swing_length'], params['lookback']
    sh_list, sl_list = [], []
    for i in range(swing, n - swing):
        if high[i] == high[i-swing:i+swing+1].max(): sh_list.append({'idx': i, 'price': float(high[i]), 'conf': i + swing})
        if low[i] == low[i-swing:i+swing+1].min(): sl_list.append({'idx': i, 'price': float(low[i]), 'conf': i + swing})
    end_bar, start_bar = n - 1, max(swing * 2, n - check_bars)
    for t in range(end_bar, start_bar - 1, -1):
        conf_h = [s for s in sh_list if s['conf'] <= t]
        conf_l = [s for s in sl_list if s['conf'] <= t]
        if len(conf_h) < 2 or len(conf_l) < 2: continue
        lh, ph, ll, pl = conf_h[-1], conf_h[-2], conf_l[-1], conf_l[-2]
        close_t = close[t]
        if (ll['price'] < pl['price'] and lh['price'] < ph['price'] and close_t > lh['price'] and lh['idx'] > t - lookback):
            entry, sl_price = close_t, ll['price']
            sl_dist = entry - sl_price
            if 0 < sl_dist / entry < 0.15:
                return {'strategy': 'ChoCh', 'side': 'LONG', 'entry_price': round(entry, 8), 'sl_price': round(sl_price, 8), 'tp_price': round(entry + params['sl_rr'] * sl_dist, 8), 'reason': 'LL+LH structure, break above LH'}
        if (lh['price'] > ph['price'] and ll['price'] > pl['price'] and close_t < ll['price'] and ll['idx'] > t - lookback):
            entry, sl_price = close_t, lh['price']
            sl_dist = sl_price - entry
            if 0 < sl_dist / entry < 0.15:
                return {'strategy': 'ChoCh', 'side': 'SHORT', 'entry_price': round(entry, 8), 'sl_price': round(sl_price, 8), 'tp_price': round(entry - params['sl_rr'] * sl_dist, 8), 'reason': 'HH+HL structure, break below HL'}
    return None

def compute_parkf_bands(df, length, sigma):
    close = df['close'].values
    n = len(close)
    if n < length + 5: return None
    shape = (n - length + 1, length)
    strides = (close.strides[0], close.strides[0])
    y_win = np.lib.stride_tricks.as_strided(close, shape=shape, strides=strides)
    x = np.arange(length, dtype=float)
    x_mean, x_var = x.mean(), ((x - x.mean()) ** 2).sum()
    y_mean = y_win.mean(axis=1, keepdims=True)
    slopes = ((x - x_mean) * (y_win - y_mean)).sum(axis=1) / x_var
    intercepts = y_mean.flatten() - slopes * x_mean
    trend_vals = intercepts[:, None] + slopes[:, None] * x
    residuals = y_win - trend_vals
    stds = np.sqrt((residuals ** 2).mean(axis=1))
    base, std_arr = np.full(n, np.nan), np.full(n, np.nan)
    base[length - 1:], std_arr[length - 1:] = intercepts, stds
    width_ratio = std_arr / np.where(base > 0, base, np.nan)
    return {'base': base, 'std': std_arr, 'upper': base + sigma * std_arr, 'lower': base - sigma * std_arr, 'width': width_ratio, 'width_med': np.nanmedian(width_ratio)}

def detect_parkf(df, params, check_bars=1):
    n = len(df)
    if n < params['length'] + 5: return None
    close, high, low = df['close'].values, df['high'].values, df['low'].values
    atr = compute_atr(df)
    bands = compute_parkf_bands(df, params['length'], params['sigma'])
    if bands is None: return None
    base, upper, lower, width, width_med = bands['base'], bands['upper'], bands['lower'], bands['width'], bands['width_med']
    end_bar, start_bar = n - 1, max(params['length'], n - check_bars)
    for t in range(end_bar, start_bar - 1, -1):
        if np.isnan(base[t]) or np.isnan(lower[t]): continue
        if np.isnan(width[t]) or width[t] > width_med: continue
        p, hi, lo, a = close[t], high[t], low[t], atr[t]
        if lo <= lower[t] and p > lower[t]:
            entry, sl_price = p, entry - params['sl_atr_mult'] * a
            sl_dist = entry - sl_price
            if 0 < sl_dist / entry < 0.15:
                return {'strategy': 'ParkF', 'side': 'LONG', 'entry_price': round(entry, 8), 'sl_price': round(sl_price, 8), 'tp_price': round(base[t], 8), 'reason': 'Touch lower band in tight channel'}
        if hi >= upper[t] and p < upper[t]:
            entry, sl_price = p, entry + params['sl_atr_mult'] * a
            sl_dist = sl_price - entry
            if 0 < sl_dist / entry < 0.15:
                return {'strategy': 'ParkF', 'side': 'SHORT', 'entry_price': round(entry, 8), 'sl_price': round(sl_price, 8), 'tp_price': round(base[t], 8), 'reason': 'Touch upper band in tight channel'}
    return None

def compute_current_regime(df_4h, params):
    if len(df_4h) < params['dc_4h_period'] + params['width_lookback']: return 0
    h4, l4 = df_4h['high'].values, df_4h['low'].values
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

def detect_acmts(df_1h, df_4h, params, check_bars=1):
    if df_4h is None or len(df_4h) < 50: return None
    n = len(df_1h)
    if n < params['dc_1h_period'] + 10: return None
    regime = compute_current_regime(df_4h, params)
    if regime == 0: return None
    close, high, low = df_1h['close'].values, df_1h['high'].values, df_1h['low'].values
    atr = compute_atr(df_1h)
    end_bar, start_bar = n - 1, max(params['dc_1h_period'], n - check_bars)
    if regime == 1:
        dc_h1 = pd.Series(high).rolling(params['dc_1h_period']).max().shift(1).values
        dc_l1 = pd.Series(low).rolling(params['dc_1h_period']).min().shift(1).values
        for t in range(end_bar, start_bar - 1, -1):
            if np.isnan(dc_h1[t]) or np.isnan(atr[t]): continue
            if close[t - 1] <= dc_h1[t] and close[t] > dc_h1[t]:
                entry, sl_price = close[t], close[t] - params['sl_atr_mult'] * atr[t]
                return {'strategy': 'ACMTS', 'side': 'LONG', 'regime': 'TREND', 'entry_price': round(entry, 8), 'sl_price': round(sl_price, 8), 'tp_price': round(entry + params['tp_rr_trend'] * (entry - sl_price), 8), 'reason': 'Breakout above Donchian'}
            if close[t - 1] >= dc_l1[t] and close[t] < dc_l1[t]:
                entry, sl_price = close[t], close[t] + params['sl_atr_mult'] * atr[t]
                return {'strategy': 'ACMTS', 'side': 'SHORT', 'regime': 'TREND', 'entry_price': round(entry, 8), 'sl_price': round(sl_price, 8), 'tp_price': round(entry - params['tp_rr_trend'] * (sl_price - entry), 8), 'reason': 'Breakdown below Donchian'}
    elif regime == -1:
        bb_mid = pd.Series(close).rolling(params['bb_1h_period']).mean().values
        bb_std = pd.Series(close).rolling(params['bb_1h_period']).std().values
        bb_u, bb_l = bb_mid + params['bb_1h_std'] * bb_std, bb_mid - params['bb_1h_std'] * bb_std
        for t in range(end_bar, start_bar - 1, -1):
            if np.isnan(bb_l[t]) or np.isnan(atr[t]): continue
            if low[t] <= bb_l[t] and close[t] > bb_l[t]:
                entry, sl_price = close[t], close[t] - params['sl_atr_mult'] * atr[t]
                return {'strategy': 'ACMTS', 'side': 'LONG', 'regime': 'RANGE', 'entry_price': round(entry, 8), 'sl_price': round(sl_price, 8), 'tp_price': round(bb_mid[t], 8), 'reason': 'Reversion from lower BB'}
            if high[t] >= bb_u[t] and close[t] < bb_u[t]:
                entry, sl_price = close[t], close[t] + params['sl_atr_mult'] * atr[t]
                return {'strategy': 'ACMTS', 'side': 'SHORT', 'regime': 'RANGE', 'entry_price': round(entry, 8), 'sl_price': round(sl_price, 8), 'tp_price': round(bb_mid[t], 8), 'reason': 'Rejection from upper BB'}
    return None

def scan_multi_strategy():
    results = []
    for i, symbol in enumerate(MS_SYMBOLS, 1):
        df_1h = fetch_ohlcv_ccxt(symbol, TIMEFRAME, LIMIT)
        if df_1h is None: continue
        df_4h = fetch_ohlcv_ccxt(symbol, '4h', 300)
        for strategy_name, detector, args in [('ChoCh', detect_choch, (df_1h, CHOCH_PARAMS, CHECK_BARS)), ('ParkF', detect_parkf, (df_1h, PARKF_PARAMS, CHECK_BARS)), ('ACMTS', detect_acmts, (df_1h, df_4h, ACMTS_PARAMS, CHECK_BARS))]:
            try:
                signal = detector(*args)
                if signal:
                    signal['Symbol'] = symbol.replace("/", "")
                    signal['TF'] = TIMEFRAME
                    signal['Signal'] = signal['side']
                    signal['Entry Price'] = signal['entry_price']
                    results.append(signal)
                    break
            except Exception: continue
    return results

# ==========================================
# 🟢 ICT Scanner (بدون تغییر)
# ==========================================
def get_usdt_dominance_trend():
    tfs = {"5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h"}
    results = {}
    current_dom = None
    try:
        r = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
        data = r.json()["data"]
        r2 = requests.get("https://api.coingecko.com/api/v3/coins/tether", timeout=10)
        usdt_data = r2.json()
        current_dom = round((usdt_data["market_data"]["market_cap"]["usd"] / data["total_market_cap"]["usd"]) * 100, 2)
    except: pass
    for tf, interval in tfs.items():
        try:
            days = 2 if tf in ["5m", "15m"] else 7 if tf == "1h" else 30
            df = yf.download("USDT-USD", period=f"{days}d", interval=interval, progress=False)
            if df.empty or len(df) < 20:
                results[tf] = {"trend": "No Data", "bias": "neutral", "emoji": "➡️"}
                continue
            df = df[["Open","High","Low","Close"]].dropna()
            df.columns = ["open","high","low","close"]
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['rsi'] = 100 - (100 / (1 + rs))
            df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
            df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
            df['bb_mid'] = df['close'].rolling(20).mean()
            df['bb_std'] = df['close'].rolling(20).std()
            df['bb_upper'] = df['bb_mid'] + (df['bb_std'] * 2)
            df['bb_lower'] = df['bb_mid'] - (df['bb_std'] * 2)
            last = df.iloc[-1]
            rsi, ema20, ema50, close, bb_upper, bb_lower = last['rsi'], last['ema20'], last['ema50'], last['close'], last['bb_upper'], last['bb_lower']
            analysis, bias, emoji = "", "neutral", "➡️"
            if rsi > 70: rsi_state = "نزدیک مقاومت"
            elif rsi < 30: rsi_state = "نزدیک حمایت"
            elif rsi > 50: rsi_state = "بالای وسط"
            else: rsi_state = "زیر وسط"
            ema_state = "صعودی" if ema20 > ema50 else "نزولی"
            if close > bb_upper: bb_state = "مقاومت قوی"
            elif close < bb_lower: bb_state = "حمایت قوی"
            else: bb_state = "نوسان عادی"
            if rsi < 30 and ema20 > ema50: analysis, bias, emoji = f"🟢 صعودی قوی | RSI: {rsi:.1f} | {ema_state} | {bb_state}", "bullish", "📉"
            elif rsi < 50 and ema20 > ema50: analysis, bias, emoji = f"🟡 صعودی خفیف | RSI: {rsi:.1f} | {ema_state} | {bb_state}", "bullish", "📉"
            elif rsi > 70 and ema20 < ema50: analysis, bias, emoji = f"🔴 نزولی قوی | RSI: {rsi:.1f} | {ema_state} | {bb_state}", "bearish", "📈"
            elif rsi > 50 and ema20 < ema50: analysis, bias, emoji = f"🟠 نزولی خفیف | RSI: {rsi:.1f} | {ema_state} | {bb_state}", "bearish", "📈"
            else: analysis, bias, emoji = f"⚪ خنثی | RSI: {rsi:.1f} | {ema_state} | {bb_state}", "neutral", "➡️"
            results[tf] = {"trend": analysis, "bias": bias, "emoji": emoji}
        except Exception: results[tf] = {"trend": "خطا در تحلیل", "bias": "neutral", "emoji": "➡️"}
    return results, current_dom

def calculate_cci(df, length):
    tp = (df['high'] + df['low'] + df['close']) / 3
    sma = tp.rolling(length).mean()
    mad = tp.rolling(length).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - sma) / (0.015 * mad)

def get_cci_display(df):
    cci5, cci13, cci52 = calculate_cci(df, 5).iloc[-1], calculate_cci(df, 13).iloc[-1], calculate_cci(df, 52).iloc[-1]
    if pd.isna(cci5) or pd.isna(cci13) or pd.isna(cci52): return "داده کافی نیست"
    if cci5 > 0 and cci13 > 0 and cci52 > 0: status = "🟢 همه بالای صفر (Long)"
    elif cci5 < 0 and cci13 < 0 and cci52 < 0: status = "🔴 همه زیر صفر (Short)"
    else: status = "⚪ مختلط (خنثی)"
    return f"{status} (5:{cci5:.0f}, 13:{cci13:.0f}, 52:{cci52:.0f})"

def get_ttm_display(df):
    if len(df) < 30: return "داده کافی نیست"
    c0, c1, c2, c3 = df['close'].iloc[-1], df['close'].iloc[-2], df['close'].iloc[-3], df['close'].iloc[-4]
    trigger_sell = (c1 < c0) and (c2 < c1 or c3 < c1)
    trigger_buy = (c1 > c0) and (c2 > c1 or c3 > c1)
    ema12, ema26 = df['close'].ewm(span=12, adjust=False).mean(), df['close'].ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - signal_line
    macd_bullish = macd_line.iloc[-1] > signal_line.iloc[-1] and macd_hist.iloc[-1] > 0
    macd_bearish = macd_line.iloc[-1] < signal_line.iloc[-1] and macd_hist.iloc[-1] < 0
    avg_vol, last_vol = df['volume'].rolling(20).mean().iloc[-1], df['volume'].iloc[-1]
    high_volume = last_vol > avg_vol * 1.2
    if trigger_buy:
        if macd_bullish and high_volume: return "🟢🟢 Strong Buy"
        elif macd_bullish or high_volume: return "🟢 Buy"
        else: return "🟡 Weak Buy"
    elif trigger_sell:
        if macd_bearish and high_volume: return "🔴🔴 Strong Sell"
        elif macd_bearish or high_volume: return "🔴 Sell"
        else: return "🟠 Weak Sell"
    else:
        ema20 = df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        if c0 > ema20 and macd_bullish: return "🟢 Uptrend"
        elif c0 < ema20 and macd_bearish: return "🔴 Downtrend"
        elif c0 > ema20: return "🟡 Mild Uptrend"
        else: return "🟠 Mild Downtrend"

def get_ao_display(df):
    if len(df) < 34: return "داده کافی نیست"
    median_price = (df['high'] + df['low']) / 2
    ao = median_price.rolling(5).mean() - median_price.rolling(34).mean()
    last_ao, prev_ao = ao.iloc[-1], ao.iloc[-2]
    if last_ao > 0 and last_ao > prev_ao: return "🟢 Bullish (صعودی)"
    elif last_ao < 0 and last_ao < prev_ao: return "🔴 Bearish (نزولی)"
    elif last_ao > 0: return "🟡 Weak Bullish (صعودی ضعیف)"
    else: return "🟠 Weak Bearish (نزولی ضعیف)"

def get_boll_macd_signal(df):
    if len(df) < 30: return "داده کافی نیست"
    bb_center, bb_std = df['close'].rolling(20).mean(), df['close'].rolling(20).std()
    bb_upper, bb_lower = bb_center + (bb_std * 2), bb_center - (bb_std * 2)
    ema12, ema26 = df['close'].ewm(span=12, adjust=False).mean(), df['close'].ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    high, low, close = df['high'], df['low'], df['close']
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(20).mean()
    last_close, last_low, last_high, last_center, last_atr = close.iloc[-1], low.iloc[-1], high.iloc[-1], bb_center.iloc[-1], atr.iloc[-1]
    bTouched_long = last_low < (last_center - last_atr)
    bTouched_short = last_high > (last_center + last_atr)
    macd_crossing_up = macd_line.iloc[-1] > macd_line.iloc[-2] and macd_line.iloc[-1] < signal_line.iloc[-1] and abs(macd_line.iloc[-1] - signal_line.iloc[-1]) < abs(macd_line.iloc[-2] - signal_line.iloc[-2])
    macd_crossing_down = macd_line.iloc[-1] < macd_line.iloc[-2] and macd_line.iloc[-1] > signal_line.iloc[-1] and abs(macd_line.iloc[-1] - signal_line.iloc[-1]) < abs(macd_line.iloc[-2] - signal_line.iloc[-2])
    if last_close > bb_upper.iloc[-1]: bb_status = "بالای باند بالا (Overbought)"
    elif last_close < bb_lower.iloc[-1]: bb_status = "زیر باند پایین (Oversold)"
    elif last_close > last_center: bb_status = "بالای باند وسط"
    else: bb_status = "زیر باند وسط"
    if bTouched_long and macd_crossing_up: return f"🟢 STRONG BUY (شرایط کامل) | {bb_status}"
    elif bTouched_short and macd_crossing_down: return f"🔴 STRONG SELL (شرایط کامل) | {bb_status}"
    elif bTouched_long: return f"🟡 Buy Zone (منتظر تایید MACD) | {bb_status}"
    elif bTouched_short: return f"🟠 Sell Zone (منتظر تایید MACD) | {bb_status}"
    else: return f"⚪ {bb_status}"

def get_macd_sr(df):
    if len(df) < 35: return "داده کافی نیست"
    ha_high, ha_low = df['high'], df['low']
    ema12, ema26 = df['close'].ewm(span=12, adjust=False).mean(), df['close'].ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    lookback = 5
    lookback_high, lookback_low = ha_high.rolling(lookback).max().iloc[-1], ha_low.rolling(lookback).min().iloc[-1]
    avg = (lookback_high + lookback_low) / 2
    current_price, prev_price = df['close'].iloc[-1], df['close'].iloc[-2]
    recent_cross_up = (prev_price < avg and current_price > avg) or (df['close'].iloc[-3] < avg and prev_price > avg)
    recent_cross_down = (prev_price > avg and current_price < avg) or (df['close'].iloc[-3] > avg and prev_price < avg)
    near_resistance = abs(current_price - lookback_high) / current_price < 0.002
    near_support = abs(current_price - lookback_low) / current_price < 0.002
    if recent_cross_up and (current_price > lookback_high or near_resistance): return "🟢 Strong Long (Cross Up + Near/Break Resistance)"
    elif recent_cross_down and (current_price < lookback_low or near_support): return "🔴 Strong Short (Cross Down + Near/Break Support)"
    elif recent_cross_up: return "🟡 Weak Long (Cross Up)"
    elif recent_cross_down: return "🟠 Weak Short (Cross Down)"
    else:
        if current_price > lookback_high: return "⚪ Above Channel (Breakout)"
        elif current_price < lookback_low: return "⚪ Below Channel (Breakdown)"
        else: return "⚪ Inside Channel"

def find_pivots(df, length=PIVOT_LENGTH):
    highs, lows = df["high"].values, df["low"].values
    n = len(df); ph, pl = [], []
    for i in range(length, n - length):
        if highs[i] == np.max(highs[i-length:i+length+1]): ph.append({"index": i, "price": highs[i]})
        if lows[i] == np.min(lows[i-length:i+length+1]): pl.append({"index": i, "price": lows[i]})
    return ph, pl

def analyze_market_structure(df, ph, pl, current_price):
    resistances = [p["price"] for p in ph if p["price"] > current_price]
    supports = [p["price"] for p in pl if p["price"] < current_price]
    nearest_res = min(resistances) if resistances else None
    nearest_sup = max(supports) if supports else None
    if nearest_res and nearest_sup:
        dist_res = (nearest_res - current_price) / current_price * 100
        dist_sup = (current_price - nearest_sup) / current_price * 100
        if dist_res < dist_sup * 0.5: return f"نزدیک مقاومت (فاصله {dist_res:.2f}%)"
        elif dist_sup < dist_res * 0.5: return f"نزدیک حمایت (فاصله {dist_sup:.2f}%)"
        else: return "بین حمایت و مقاومت (وسط)"
    elif nearest_res: return "بالای حمایت (مقاومت نزدیک نیست)"
    elif nearest_sup: return "زیر مقاومت (حمایت نزدیک نیست)"
    else: return "خارج از محدوده (شکست کامل)"

def calculate_stage(df, level, signal_type):
    current_price = df["close"].iloc[-1]
    atr = (df["high"] - df["low"]).rolling(14).mean().iloc[-1]
    if atr == 0 or np.isnan(atr): atr = abs(df["close"].iloc[-1] - df["close"].iloc[-20]) / 20
    distance = (current_price - level) if signal_type == "LONG" else (level - current_price)
    move_score = min(10, max(1, int((distance / atr) * 3.5)))
    bars_since = 0
    for i in range(1, min(40, len(df))):
        if (signal_type == "LONG" and df["low"].iloc[-i] < level) or (signal_type == "SHORT" and df["high"].iloc[-i] > level):
            bars_since = i; break
    time_score = min(10, max(1, int(bars_since / 3)))
    final = int(round(move_score * 0.65 + time_score * 0.35))
    final = max(1, min(10, final))
    if final <= 3: return final, "1", "🟢"
    elif final <= 6: return final, "2", "🟡"
    else: return final, "3", "🔴"

def detect_sweeps(df, ph, pl):
    signals = []
    active_bsl = [p for p in ph if p["index"] < len(df)-2][-MAX_ACTIVE_LEVELS:]
    active_ssl = [p for p in pl if p["index"] < len(df)-2][-MAX_ACTIVE_LEVELS:]
    ch, cl, cc = df["high"].iloc[-1], df["low"].iloc[-1], df["close"].iloc[-1]
    for lvl in reversed(active_ssl):
        if cl < lvl["price"] and cc > lvl["price"]:
            score, label, emoji = calculate_stage(df, lvl["price"], "LONG")
            signals.append({"type":"LONG","level":lvl["price"],"price":cc,"score":score,"stage":label,"emoji":emoji}); break
    for lvl in reversed(active_bsl):
        if ch > lvl["price"] and cc < lvl["price"]:
            score, label, emoji = calculate_stage(df, lvl["price"], "SHORT")
            signals.append({"type":"SHORT","level":lvl["price"],"price":cc,"score":score,"stage":label,"emoji":emoji}); break
    return signals

def scan_ict(usdt_tfs):
    results = []
    for symbol in ICT_SYMBOLS:
        for tf in ICT_TF_LIST:
            try:
                days = 30 if tf == "4h" else 7
                df = yf.download(symbol, period=f"{days}d", interval=tf, progress=False)
                if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
                if df.empty or len(df) < 60: continue
                df = df[["Open","High","Low","Close","Volume"]].dropna()
                df.columns = ["open","high","low","close","volume"]
                df = df.iloc[:-1]
                ph, pl = find_pivots(df)
                signals = detect_sweeps(df, ph, pl)
                if not signals: continue
                clean = symbol.replace("-USD", "/USDT").replace("32196", "")
                current_price = df["close"].iloc[-1]
                market_structure = analyze_market_structure(df, ph, pl, current_price)
                cci_display, ttm_display, ao_display = get_cci_display(df), get_ttm_display(df), get_ao_display(df)
                bm_signal, sr_signal = get_boll_macd_signal(df), get_macd_sr(df)
                if len(signals) > 1: signals = [max(signals, key=lambda x: x["score"])]
                for sig in signals:
                    bias = usdt_tfs[tf]["bias"] if tf in usdt_tfs else "neutral"
                    trend_emoji = usdt_tfs[tf]["emoji"] if tf in usdt_tfs else "➡️"
                    if sig["type"] == "LONG":
                        if bias == "bearish": rel = "موافق روند"
                        elif bias == "bullish": rel = "خلاف روند"
                        else: rel = "خنثی"
                    elif sig["type"] == "SHORT":
                        if bias == "bullish": rel = "موافق روند"
                        elif bias == "bearish": rel = "خلاف روند"
                        else: rel = "خنثی"
                    else: rel = "خنثی"
                    results.append({"Symbol": clean, "TF": tf, "Signal": sig["type"], "Strategy": "ICT", "Score": sig["score"], "Stage": sig["stage"], "Market Structure": market_structure, "USDT.D Relation": rel, "USDT.D Trend": trend_emoji, "CCI": cci_display, "TTM": ttm_display, "AO": ao_display, "BM": bm_signal, "SR": sr_signal, "Entry Price": sig["price"], "Timestamp": time.time()})
            except Exception: continue
    return pd.DataFrame(results) if results else pd.DataFrame()

# ==========================================
# 🟢 ارسال پیام و main
# ==========================================
def send_telegram(msg):
    if not TG_TOKEN or not TG_CHAT: return False
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    if len(msg) <= 4000:
        try:
            r = requests.post(url, data={"chat_id": TG_CHAT, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=15)
            return r.ok
        except Exception: return False
    else:
        chunks, current_chunk = [], ""
        for line in msg.split('\n'):
            if len(current_chunk) + len(line) + 1 <= 4000: current_chunk += line + '\n'
            else: chunks.append(current_chunk); current_chunk = line + '\n'
        if current_chunk: chunks.append(current_chunk)
        success = True
        for chunk in chunks:
            try:
                r = requests.post(url, data={"chat_id": TG_CHAT, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=15)
                if not r.ok: success = False
                time.sleep(1)
            except Exception: success = False
        return success

def main():
    print(f"Run: {datetime.utcnow():%Y-%m-%d %H:%M:%S} UTC\n")
    usdt_tfs, current_dom = get_usdt_dominance_trend()
    
    # 1. ICT Scanner
    print("=" * 70); print("ICT Liquidity Sweep Scanner"); print("=" * 70)
    ict_df = scan_ict(usdt_tfs)
    if not ict_df.empty: print(ict_df.to_string(index=False))
    
    # 2. Multi-Strategy Scanner
    print("\n" + "=" * 70); print("Multi-Strategy Scanner (ChoCh + ParkF + ACMTS)"); print("=" * 70)
    ms_signals = scan_multi_strategy()
    for sig in ms_signals:
        print(f"  {sig['strategy']}: {sig['Symbol']} | {sig['Signal']} | Entry: {sig['Entry Price']:.6f}")
    
    # 3. Combine and log
    all_new_signals = []
    if not ict_df.empty: all_new_signals.extend(ict_df.to_dict('records'))
    all_new_signals.extend(ms_signals)
    
    log_paper_trades(all_new_signals)
    
    # 4. Build Telegram Message
    msg = f"<b>📊 اسکنر ترکیبی</b> — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n\n"
    msg += f"<b>📉 تحلیل دامیننس تتر:</b>\n"
    for tf, data in usdt_tfs.items(): msg += f"• {tf}: {data['trend']}\n"
    msg += "\n"
    
    if not ict_df.empty:
        msg += f"<b>💧 ICT Liquidity Sweep</b> ({len(ict_df)}):\n"
        for _, r in ict_df.iterrows():
            emo = "🟢" if r["Signal"] == "LONG" else "🔴"
            msg += f"{emo} <code>{r['Symbol']}</code> | TF: {r['TF']} | {r['Signal']} | ⭐{r['Score']}\n"
            msg += f"   🌐 {r['USDT.D Trend']} | 📊 {r['CCI']}\n"
            msg += f"   ⚡ {r['TTM']} | 📈 {r['AO']}\n"
        msg += "\n"
        
    if ms_signals:
        msg += f"<b>🎯 Multi-Strategy Signals</b> ({len(ms_signals)}):\n"
        for sig in ms_signals:
            emo = "🟢" if sig['side'] == 'LONG' else "🔴"
            msg += f"{emo} <code>{sig['Symbol']}</code> | {sig['strategy']} | {sig['side']}\n"
            msg += f"   Entry: {sig['entry_price']:.6f} | SL: {sig['sl_price']:.6f}\n"
        msg += "\n"

    if not ict_df.empty or ms_signals:
        send_telegram(msg)
    else:
        print("\nNo alerts to send.")

if __name__ == "__main__":
    main()
