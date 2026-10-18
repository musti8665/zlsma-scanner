# ============================================================
# Ultimate Scanner: ZLSMA (via Kraken) + ICT (via yfinance)
# ============================================================
import os, json, time, requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings("ignore")
import yfinance as yf

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT  = os.environ.get("TG_CHAT", "")
STATE_FILE = "state.json"

ZLSMA_SYMBOLS = [
    "SHIBUSD","PEOPLEUSD","ZECUSD","ARKMUSD","TIAUSD",
    "ICPUSD","RUNEUSD","BOMEUSD","SEIUSD",
    "NOTUSD","WIFUSD","AVAXUSD","WLDUSD","MEMEUSD",
    "ARUSD","ADAUSD","NEARUSD","OPUSD","BONKUSD","CKBUSD",
    "HBARUSD", "ALGOUSD", "INJUSD", "FETUSD", "ONDOUSD"
]

ZLSMA_TF = {"5m":"5", "15m":"15", "1h":"60", "4h":"240"}
ZLSMA_LEN = 32
ZLSMA_OFF = 0
KRAKEN_BASE = "https://api.kraken.com/0/public/OHLC"

ICT_SYMBOLS = [
    "SHIB-USD","ICP-USD","SEI-USD","NOT-USD","WIF-USD","AVAX-USD",
    "WLD-USD","PEPE-USD","ADA-USD","NEAR-USD","OP-USD","BONK-USD",
    "SOL-USD","INJ-USD","FIL-USD","ARB-USD","GALA-USD","AAVE-USD",
    "STX-USD","THETA-USD","RUNE-USD","DOGE-USD","HBAR-USD",
    "HYPE32196-USD", "ALGO-USD", "FET-USD", "UNI-USD", "ONDO-USD"
]
ICT_TF_LIST = ["5m", "15m", "1h", "4h"] 
ICT_LOOKBACK_DAYS = 30
PIVOT_LENGTH = 10
MAX_ACTIVE_LEVELS = 5

FOLLOW_UP_TIMES = {
    "5m": 5 * 60, "15m": 15 * 60, "1h": 60 * 60, "4h": 4 * 60 * 60
}

def send_telegram(msg):
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram not configured"); return False
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={
            "chat_id": TG_CHAT, "text": msg,
            "parse_mode": "HTML", "disable_web_page_preview": True,
        }, timeout=15)
        if not r.ok: print("TG error:", r.text[:200])
        return r.ok
    except Exception as e:
        print("TG exception:", e); return False

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f: return json.load(f)
        except Exception: return {}
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def fetch_kraken_klines(symbol, interval, limit=200):
    try:
        params = {"pair": symbol, "interval": interval}
        r = requests.get(KRAKEN_BASE, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("error"):
            print(f"Kraken Error {symbol} {interval}: {data['error']}")
            return None
        result = data.get("result", {})
        pair_keys = [k for k in result.keys() if k != "last"]
        if not pair_keys: return None
        klines = result[pair_keys[0]]
        if not klines: return None
        df = pd.DataFrame(klines, columns=[
            "time","open","high","low","close","vwap","volume","count"
        ])
        for col in ["open","high","low","close","volume"]:
            df[col] = df[col].astype(float)
        if len(df) > 1:
            df = df.iloc[:-1]
        return df
    except Exception as e:
        print(f"Kraken fetch error {symbol} {interval}: {e}")
        return None

def linreg(series, length, offset=0):
    s = series.shift(offset)
    x = pd.Series(range(length), dtype=float)
    x_mean = x.mean()
    denom = ((x - x_mean) ** 2).sum()
    out = pd.Series(index=series.index, dtype=float)
    vals = s.values
    for i in range(length - 1, len(vals)):
        y = vals[i - length + 1: i + 1]
        if pd.isna(y).any(): continue
        y_mean = y.mean()
        slope = ((x - x_mean) * (y - y_mean)).sum() / denom
        intercept = y_mean - slope * x_mean
        out.iloc[i] = intercept + slope * (length - 1)
    return out

def zlsma(series, length=ZLSMA_LEN, offset=ZLSMA_OFF):
    lsma  = linreg(series, length, offset)
    lsma2 = linreg(lsma,   length, offset)
    return lsma + (lsma - lsma2)

def chandelier_exit(df, period=22, multiplier=3.0):
    atr = (df['high'] - df['low']).rolling(period).mean()
    highest_high = df['high'].rolling(period).max()
    lowest_low = df['low'].rolling(period).min()
    long_stop = highest_high - (atr * multiplier)
    short_stop = lowest_low + (atr * multiplier)
    ce_dir = 1
    for i in range(period, len(df)):
        if df['close'].iloc[i] > short_stop.iloc[i-1]:
            ce_dir = 1
        elif df['close'].iloc[i] < long_stop.iloc[i-1]:
            ce_dir = -1
    return ce_dir

def check_volume(df):
    if len(df) < 20: return False
    avg_vol = df['volume'].rolling(20).mean().iloc[-1]
    last_vol = df['volume'].iloc[-1]
    return last_vol > avg_vol * 1.2

def check_fvg(df):
    if len(df) < 3: return False
    if df['low'].iloc[-1] > df['high'].iloc[-3]: return True
    if df['high'].iloc[-1] < df['low'].iloc[-3]: return True
    return False

def scan_zlsma():
    rows = []
    tf_keys = list(ZLSMA_TF.keys())
    for sym in ZLSMA_SYMBOLS:
        row = {"Symbol": sym.replace("USD", "")[:9]}
        for tf_name, tf_code in ZLSMA_TF.items():
            df = fetch_kraken_klines(sym, tf_code, limit=200)
            
            if df is not None and len(df) >= ZLSMA_LEN:
                z = zlsma(df["close"])
                row[tf_name] = "UP" if z.iloc[-1] > z.iloc[-2] else "DOWN"
                
                if tf_name in ["1h", "4h"]:
                    ce = "🟢" if chandelier_exit(df) == 1 else "🔴"
                    vol = "📊" if check_volume(df) else "➖"
                    fvg = "⚡" if check_fvg(df) else "➖"
                    row[f"CE_{tf_name}"] = ce
                    row[f"Vol_{tf_name}"] = vol
                    row[f"FVG_{tf_name}"] = fvg
            else:
                row[tf_name] = "ERR"
                if tf_name in ["1h", "4h"]:
                    row[f"CE_{tf_name}"] = "⚪"
                    row[f"Vol_{tf_name}"] = "⚪"
                    row[f"FVG_{tf_name}"] = "⚪"
            time.sleep(1.0)
        rows.append(row)
        
    df = pd.DataFrame(rows)
    df["Score"] = (df[tf_keys] == "UP").sum(axis=1)
    df = df.sort_values("Score", ascending=False).reset_index(drop=True)
    return df

# ==========================================
# 🟢 محاسبه دقیق دامیننس تتر در هر تایم‌فریم
# ==========================================
def get_usdt_dominance_trend():
    tfs = {"5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
    results = {}
    
    for tf, interval in tfs.items():
        try:
            days = 2 if tf in ["5m", "15m"] else 7 if tf == "1h" else 30
            df = yf.download("USDT-USD", period=f"{days}d", interval=interval, progress=False)
            
            if df.empty or len(df) < 5:
                results[tf] = {"trend": "➡️ (خنثی)", "bias": "neutral", "emoji": "➡️"}
                continue

            z = zlsma(df["Close"], length=14)
            last_z = z.iloc[-1]
            prev_z = z.iloc[-2]
            
            if last_z > prev_z:
                trend = "📈 (صعودی - ریسک‌گریز)"
                bias = "bearish"
                emoji = "📈"
            else:
                trend = "📉 (نزولی - ریسک‌پذیر)"
                bias = "bullish"
                emoji = "📉"
                
            results[tf] = {"trend": trend, "bias": bias, "emoji": emoji}
        except Exception as e:
            print(f"USDT.D {tf} err: {e}")
            results[tf] = {"trend": "➡️ (خنثی)", "bias": "neutral", "emoji": "➡️"}
            
    try:
        r = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
        data = r.json()["data"]
        r2 = requests.get("https://api.coingecko.com/api/v3/coins/tether", timeout=10)
        usdt_data = r2.json()
        current_dom = (usdt_data["market_data"]["market_cap"]["usd"] / data["total_market_cap"]["usd"]) * 100
    except:
        current_dom = None
        
    return results, round(current_dom, 2) if current_dom else None

def find_pivots(df, length=PIVOT_LENGTH):
    highs = df["high"].values; lows = df["low"].values
    n = len(df); ph, pl = [], []
    for i in range(length, n - length):
        if highs[i] == np.max(highs[i-length:i+length+1]):
            ph.append({"index": i, "price": highs[i]})
        if lows[i] == np.min(lows[i-length:i+length+1]):
            pl.append({"index": i, "price": lows[i]})
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
        if (signal_type == "LONG"  and df["low"].iloc[-i]  < level) or \
           (signal_type == "SHORT" and df["high"].iloc[-i] > level):
            bars_since = i; break
    time_score = min(10, max(1, int(bars_since / 3)))
    final = int(round(move_score * 0.65 + time_score * 0.35))
    final = max(1, min(10, final))
    if final <= 3:   return final, "1", "🟢"
    elif final <= 6: return final, "2", "🟡"
    else:            return final, "3", "🔴"

def detect_sweeps(df, ph, pl):
    signals = []
    active_bsl = [p for p in ph if p["index"] < len(df)-2][-MAX_ACTIVE_LEVELS:]
    active_ssl = [p for p in pl if p["index"] < len(df)-2][-MAX_ACTIVE_LEVELS:]
    ch = df["high"].iloc[-1]; cl = df["low"].iloc[-1]; cc = df["close"].iloc[-1]
    for lvl in reversed(active_ssl):
        if cl < lvl["price"] and cc > lvl["price"]:
            score, label, emoji = calculate_stage(df, lvl["price"], "LONG")
            signals.append({"type":"LONG","level":lvl["price"],"price":cc,
                            "score":score,"stage":label,"emoji":emoji}); break
    for lvl in reversed(active_bsl):
        if ch > lvl["price"] and cc < lvl["price"]:
            score, label, emoji = calculate_stage(df, lvl["price"], "SHORT")
            signals.append({"type":"SHORT","level":lvl["price"],"price":cc,
                            "score":score,"stage":label,"emoji":emoji}); break
    return signals

def scan_ict(usdt_tfs):
    results = []
    for symbol in ICT_SYMBOLS:
        for tf in ICT_TF_LIST:
            try:
                days = 30 if tf == "4h" else 7
                df = yf.download(symbol, period=f"{days}d", interval=tf, progress=False)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
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
                
                valid_signals = []
                for sig in signals:
                    if sig["type"] == "LONG" and "نزدیک مقاومت" in market_structure: continue
                    if sig["type"] == "SHORT" and "نزدیک حمایت" in market_structure: continue
                    valid_signals.append(sig)
                
                if len(valid_signals) > 1:
                    valid_signals = [max(valid_signals, key=lambda x: x["score"])]
                
                if not valid_signals: continue

                for sig in valid_signals:
                    bias = usdt_tfs[tf]["bias"] if tf in usdt_tfs else "neutral"
                    trend_emoji = usdt_tfs[tf]["emoji"] if tf in usdt_tfs else "➡️"
                    if bias == "bullish": rel = "موافق روند" if sig["type"]=="LONG"  else "خلاف روند"
                    elif bias == "bearish": rel = "موافق روند" if sig["type"]=="SHORT" else "خلاف روند"
                    else: rel = "خنثی"
                    
                    results.append({"Symbol": clean, "TF": tf, "Signal": sig["type"],
                                    "Score": sig["score"], "Stage": sig["stage"],
                                    "Market Structure": market_structure,
                                    "USDT.D Relation": rel, "USDT.D Trend": trend_emoji,
                                    "Entry Price": sig["price"], "Timestamp": time.time()})
            except Exception as e:
                print(f"ICT {symbol} {tf} err: {str(e)[:80]}")
    return pd.DataFrame(results) if results else pd.DataFrame()

def build_message(zdf, usdt_tfs, current_dom, ict_df, follow_ups):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    msg = f"<b>📊 اسکنر ترکیبی</b> — {now}\n\n"
    
    msg += f"<b>USDT.D ({current_dom}%):</b>\n"
    for tf, data in usdt_tfs.items():
        msg += f"• {tf}: {data['trend']}\n"
    msg += "\n"

    if not zdf.empty:
        msg += "📈 <b>ZLSMA Trend (4 TFs + Ind.)</b>\n"
        msg += "<pre>\n"
        msg += f"{'Sym':<7}|{'5m':<2}|{'15m':<2}|{'1h':<2}|{'4h':<2}|{'CE':<6}|{'Vol':<5}|{'FVG':<5}|Scr\n"
        msg += "-" * 55 + "\n"
        
        def get_icon(val):
            if val == "UP": return "🟢"
            if val == "DOWN": return "🔴"
            return "⚪"

        for _, r in zdf.iterrows():
            sym_short = r['Symbol'][:7]
            t5 = get_icon(r.get('5m'))
            t15 = get_icon(r.get('15m'))
            t1h = get_icon(r.get('1h'))
            t4h = get_icon(r.get('4h'))
            
            ce_1h = r.get('CE_1h', '⚪')
            ce_4h = r.get('CE_4h', '⚪')
            vol_1h = r.get('Vol_1h', '⚪')
            vol_4h = r.get('Vol_4h', '⚪')
            fvg_1h = r.get('FVG_1h', '⚪')
            fvg_4h = r.get('FVG_4h', '⚪')
            
            ce_str = f"{ce_1h}{ce_4h}"
            vol_str = f"{vol_1h}{vol_4h}"
            fvg_str = f"{fvg_1h}{fvg_4h}"
            
            score = int(r['Score'])
            msg += f"{sym_short:<7}|{t5} |{t15} |{t1h} |{t4h} |{ce_str:<6}|{vol_str:<5}|{fvg_str:<5}|{score}/4\n"
            
        msg += "</pre>\n\n"
        msg += "راهنما: CE=Chandelier | Vol=حجم | FVG=شکاف ارزش (ستون‌ها: اول 1h، دوم 4h)\n\n"

    if follow_ups:
        msg += "🔎 <b>نتیجه سیگنال‌های قبلی:</b>\n"
        for f in follow_ups:
            status = "✅ سود" if f["profit"] > 0 else "❌ ضرر"
            if f["TF"] == "5m": time_str = "۵ دقیقه"
            elif f["TF"] == "15m": time_str = "۱۵ دقیقه"
            elif f["TF"] == "1h": time_str = "۱ ساعت"
            else: time_str = "۴ ساعت"
            msg += (f"• <code>{f['Symbol']}</code> | {f['Signal']} | TF: {f['TF']} "
                    f"| پس از {time_str}: {f['profit']:+.2f}% {status}\n")
        msg += "\n"

    if not ict_df.empty:
        msg += f"💧 <b>ICT Liquidity Sweep</b> ({len(ict_df)} سیگنال)\n\n"
        for _, r in ict_df.iterrows():
            emo = "🟢" if r["Signal"] == "LONG" else "🔴"
            
            risk_warning = ""
            if "وسط" in r['Market Structure']:
                risk_warning += " ⚠️(وسط)"
            if "خلاف روند" in r['USDT.D Relation']:
                risk_warning += " ⚠️(خلاف روند)"
            
            msg += (f"{emo} <code>{r['Symbol']}</code> | TF: {r['TF']} | {r['Signal']} "
                    f"| ⭐{r['Score']} | مرحله: {r['Stage']}{risk_warning}\n"
                    f"   📍 ساختار: {r['Market Structure']}\n"
                    f"   🌐 USDT.D: {r['USDT.D Trend']} ({r['USDT.D Relation']})\n")
        msg += "\n"

    if zdf.empty and ict_df.empty and not follow_ups:
        return None

    msg += "⚠️ این پیام صرفاً تحلیلی است، نه سیگنال خرید/فروش."
    return msg

def main():
    print(f"Run: {datetime.utcnow():%Y-%m-%d %H:%M:%S} UTC\n")

    usdt_tfs, current_dom = get_usdt_dominance_trend()

    print("=" * 70); print("ZLSMA Ultimate Scanner (via Kraken)"); print("=" * 70)
    zdf = scan_zlsma()
    print(zdf.to_string(index=False))

    print("\n" + "=" * 70); print("ICT Liquidity Sweep"); print("=" * 70)
    ict_df = scan_ict(usdt_tfs)
    if not ict_df.empty: print(ict_df.to_string(index=False))

    state = load_state()
    follow_ups = []
    now_ts = time.time()
    keys_to_remove = []

    for key, data in state.items():
        if key.startswith("ict_") and isinstance(data, dict) and "timestamp" in data:
            tf = data["TF"]
            if tf in FOLLOW_UP_TIMES:
                elapsed = now_ts - data["timestamp"]
                if elapsed >= FOLLOW_UP_TIMES[tf]:
                    current_df = yf_data(data["Symbol"] + "-USD", tf, 7)
                    if current_df is not None and len(current_df) > 0:
                        current_price = current_df["close"].iloc[-1]
                        entry_price = data["Entry Price"]
                        if data["Signal"] == "LONG":
                            profit = (current_price - entry_price) / entry_price * 100
                        else:
                            profit = (entry_price - current_price) / entry_price * 100
                        follow_ups.append({
                            "Symbol": data["Symbol"], "TF": tf, 
                            "Signal": data["Signal"], "profit": profit
                        })
                    keys_to_remove.append(key)

    for k in keys_to_remove:
        del state[k]

    new_ict = []
    # 🟢 تغییر زمان ضد اسپم به ۱۵ دقیقه
    FIFTEEN_MIN = 15 * 60

    for _, r in ict_df.iterrows() if not ict_df.empty else []:
        sym = r['Symbol']; tf = r['TF']; sig_type = r['Signal'] 
        key = f"ict_{sym}_{tf}"
        
        is_new = False
        if key in state and isinstance(state[key], dict):
            last_type = state[key].get("Signal")
            last_ts = state[key].get("timestamp", 0)
            if sig_type != last_type or (now_ts - last_ts) >= FIFTEEN_MIN:
                is_new = True
        else:
            is_new = True
            
        if is_new:
            new_ict.append(r)
            state[key] = {
                "Symbol": sym, "TF": tf, "Signal": sig_type,
                "Entry Price": r["Entry Price"], "timestamp": now_ts
            }

    ict_new_df = pd.DataFrame(new_ict) if new_ict else pd.DataFrame()
    save_state(state)

    msg = build_message(zdf, usdt_tfs, current_dom, ict_new_df, follow_ups)
    if msg:
        print("\nTelegram sent:", send_telegram(msg))
    else:
        print("\nNo alerts to send.")

if __name__ == "__main__":
    main()
