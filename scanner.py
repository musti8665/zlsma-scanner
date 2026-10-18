# ============================================================
# Combined Scanner: ZLSMA + ICT + Market Structure (via yfinance)
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

# 🟢 ZLSMA Symbols (yfinance format)
ZLSMA_SYMBOLS = [
    "SHIB-USD","PEOPLE-USD","ZEC-USD","ARKM-USD","MYX-USD","TIA-USD",
    "ICP-USD","RUNE-USD","DOGS-USD","BOME-USD","SEI-USD",
    "NOT-USD","APT-USD","WIF-USD","AVAX-USD","WLD-USD","MEME-USD","PEPE-USD",
    "AR-USD","ADA-USD","NEAR-USD","OP-USD","SUI-USD","BONK-USD","CKB-USD",
    "HYPE32196-USD", "HBAR-USD", "ALGO-USD", "INJ-USD", "FET-USD", "UNI-USD", "ONDO-USD"
]

ZLSMA_TF = {"3m":"3m", "5m":"5m", "15m":"15m", "1h":"1h", "4h":"4h"}
ZLSMA_LEN = 32
ZLSMA_OFF = 0

# 🟢 ICT Symbols (yfinance format)
ICT_SYMBOLS = [
    "SHIB-USD","ICP-USD","SEI-USD","NOT-USD","WIF-USD","AVAX-USD",
    "WLD-USD","PEPE-USD","ADA-USD","NEAR-USD","OP-USD","BONK-USD",
    "SOL-USD","INJ-USD","FIL-USD","ARB-USD","GALA-USD","AAVE-USD",
    "STX-USD","THETA-USD","RUNE-USD","DOGE-USD","HBAR-USD",
    "HYPE32196-USD", "ALGO-USD", "FET-USD", "UNI-USD", "ONDO-USD"
]
ICT_TF_LIST = ["15m", "1h", "4h"] 
ICT_LOOKBACK_DAYS = 30
PIVOT_LENGTH = 10
MAX_ACTIVE_LEVELS = 5

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

def yf_data(symbol, interval, days):
    end = datetime.now(); start = end - timedelta(days=days)
    try:
        df = yf.download(symbol, start=start, end=end, interval=interval, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df.empty or len(df) < 60: 
            return None
        df = df[["Open","High","Low","Close"]].dropna()
        df.columns = ["open","high","low","close"]
        return df.iloc[:-1] 
    except Exception as e:
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

def trend_of(series):
    if len(series) < 2 or pd.isna(series.iloc[-1]) or pd.isna(series.iloc[-2]):
        return "N/A"
    return "UP" if series.iloc[-1] > series.iloc[-2] else "DOWN"

def scan_zlsma():
    rows = []
    tf_keys = list(ZLSMA_TF.keys())
    for sym in ZLSMA_SYMBOLS:
        row = {"Symbol": sym.replace("-USD", "").replace("32196", "")[:9]}
        for tf_name, tf in ZLSMA_TF.items():
            days = 30 if tf == "4h" else 7
            df = yf_data(sym, tf, days)
            
            if df is not None and len(df) >= ZLSMA_LEN:
                z = zlsma(df["close"])
                row[tf_name] = trend_of(z)
            else:
                row[tf_name] = "ERR"
            time.sleep(0.1)
        rows.append(row)
        
    df = pd.DataFrame(rows)
    df["Score"] = (df[tf_keys] == "UP").sum(axis=1)
    df = df.sort_values("Score", ascending=False).reset_index(drop=True)
    return df

def get_usdt_dominance_trend():
    tfs = {"15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
    results = {}
    try:
        r = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
        data = r.json()["data"]
        r2 = requests.get("https://api.coingecko.com/api/v3/coins/tether", timeout=10)
        usdt_data = r2.json()
        current_dom = (usdt_data["market_data"]["market_cap"]["usd"] / data["total_market_cap"]["usd"]) * 100
        usdt_chg_24h = usdt_data["market_data"]["market_cap_change_percentage_24h"]
        market_chg_24h = data["market_cap_change_percentage_24h_usd"]
        
        if usdt_chg_24h > market_chg_24h + 0.1:
            bias = "bearish"; trend = "📈 (صعودی - ریسک‌گریز)"
        elif usdt_chg_24h < market_chg_24h - 0.1:
            bias = "bullish"; trend = "📉 (نزولی - ریسک‌پذیر)"
        else:
            bias = "neutral"; trend = "➡️ (خنثی)"
            
        for tf in tfs:
            results[tf] = {"trend": trend, "bias": bias, "emoji": trend}
        return results, round(current_dom, 2)
    except Exception as e:
        for tf in tfs:
            results[tf] = {"trend": "❓", "bias": "neutral", "emoji": "❓"}
        return results, None

def find_pivots(df, length=PIVOT_LENGTH):
    highs = df["high"].values; lows = df["low"].values
    n = len(df); ph, pl = [], []
    for i in range(length, n - length):
        if highs[i] == np.max(highs[i-length:i+length+1]):
            ph.append({"index": i, "price": highs[i]})
        if lows[i] == np.min(lows[i-length:i+length+1]):
            pl.append({"index": i, "price": lows[i]})
    return ph, pl

# 🟢 تابع جدید برای تحلیل ساختار بازار (حمایت/مقاومت)
def analyze_market_structure(df, ph, pl, current_price):
    resistances = [p["price"] for p in ph if p["price"] > current_price]
    supports = [p["price"] for p in pl if p["price"] < current_price]
    
    nearest_res = min(resistances) if resistances else None
    nearest_sup = max(supports) if supports else None
    
    if nearest_res and nearest_sup:
        dist_res = (nearest_res - current_price) / current_price * 100
        dist_sup = (current_price - nearest_sup) / current_price * 100
        
        if dist_res < dist_sup * 0.5:
            return f"نزدیک مقاومت (فاصله {dist_res:.2f}%)"
        elif dist_sup < dist_res * 0.5:
            return f"نزدیک حمایت (فاصله {dist_sup:.2f}%)"
        else:
            return "بین حمایت و مقاومت (وسط)"
            
    elif nearest_res:
        return "بالای حمایت (مقاومت نزدیک نیست)"
    elif nearest_sup:
        return "زیر مقاومت (حمایت نزدیک نیست)"
    else:
        return "خارج از محدوده (شکست کامل)"

def calculate_stage(df, level, signal_type):
    current_price = df["close"].iloc[-1]
    atr = (df["high"] - df["low"]).rolling(14).mean().iloc[-1]
    if atr == 0 or np.isnan(atr):
        atr = abs(df["close"].iloc[-1] - df["close"].iloc[-20]) / 20
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
                df = yf_data(symbol, tf, days)
                if df is None: continue
                ph, pl = find_pivots(df)
                signals = detect_sweeps(df, ph, pl)
                clean = symbol.replace("-USD", "/USDT").replace("32196", "")
                
                # 🟢 تحلیل ساختار بازار برای این ارز و تایم فریم
                current_price = df["close"].iloc[-1]
                market_structure = analyze_market_structure(df, ph, pl, current_price)

                for sig in signals:
                    bias = usdt_tfs[tf]["bias"] if tf in usdt_tfs else "neutral"
                    trend_emoji = usdt_tfs[tf]["emoji"] if tf in usdt_tfs else "❓"
                    
                    if bias == "bullish":
                        rel = "موافق روند" if sig["type"]=="LONG"  else "خلاف روند"
                    elif bias == "bearish":
                        rel = "موافق روند" if sig["type"]=="SHORT" else "خلاف روند"
                    else:
                        rel = "خنثی"
                    
                    results.append({"Symbol": clean, "TF": tf, "Signal": sig["type"],
                                    "Score": sig["score"], "Stage": sig["stage"],
                                    "Market Structure": market_structure,
                                    "USDT.D Relation": rel,
                                    "USDT.D Trend": trend_emoji,
                                    "Level": round(sig["level"], 8),
                                    "Price": round(sig["price"], 8)})
            except Exception as e:
                print(f"ICT {symbol} {tf} err: {str(e)[:80]}")
    return pd.DataFrame(results) if results else pd.DataFrame()

def build_message(zdf, usdt_tfs, current_dom, ict_df):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    msg = f"<b>📊 اسکنر ترکیبی</b> — {now}\n\n"
    
    msg += f"<b>USDT.D ({current_dom}%):</b>\n"
    for tf, data in usdt_tfs.items():
        msg += f"• {tf}: {data['trend']}\n"
    msg += "\n"

    if not zdf.empty:
        msg += "📈 <b>ZLSMA Trend (وضعیت ارزها)</b>\n"
        msg += "<pre>\n"
        msg += f"{'Symbol':<9} | {'3m':<2} | {'5m':<2} | {'15m':<3} | {'1h':<2} | {'4h':<2} | Score\n"
        msg += "-" * 45 + "\n"
        
        def get_icon(val):
            if val == "UP": return "🟢"
            if val == "DOWN": return "🔴"
            return "⚪"

        for _, r in zdf.iterrows():
            sym_short = r['Symbol'][:9]
            t3 = get_icon(r.get('3m'))
            t5 = get_icon(r.get('5m'))
            t15 = get_icon(r.get('15m'))
            t1h = get_icon(r.get('1h'))
            t4h = get_icon(r.get('4h'))
            score = int(r['Score'])
            msg += f"{sym_short:<9} | {t3}  | {t5}  | {t15}  | {t1h}  | {t4h}  | {score}/5\n"
            
        msg += "</pre>\n\n"

    if not ict_df.empty:
        msg += f"💧 <b>ICT Liquidity Sweep</b> ({len(ict_df)} سیگنال)\n\n"
        for _, r in ict_df.iterrows():
            emo = "🟢" if r["Signal"] == "LONG" else "🔴"
            # 🟢 اضافه شدن ساختار بازار و توضیح دامیننس تتر
            msg += (f"{emo} <code>{r['Symbol']}</code> | TF: {r['TF']} | {r['Signal']} "
                    f"| ⭐{r['Score']} | مرحله: {r['Stage']}\n"
                    f"   📍 ساختار: {r['Market Structure']}\n"
                    f"   🌐 USDT.D: {r['USDT.D Trend']} ({r['USDT.D Relation']})\n")
        msg += "\n"

    if zdf.empty and ict_df.empty:
        return None

    msg += "⚠️ این پیام صرفاً تحلیلی است، نه سیگنال خرید/فروش."
    return msg

def main():
    print(f"Run: {datetime.utcnow():%Y-%m-%d %H:%M:%S} UTC\n")

    usdt_tfs, current_dom = get_usdt_dominance_trend()

    print("=" * 70); print("ZLSMA Scanner (via yfinance)"); print("=" * 70)
    zdf = scan_zlsma()
    print(zdf.to_string(index=False))

    print("\n" + "=" * 70); print("ICT Liquidity Sweep"); print("=" * 70)
    ict_df = scan_ict(usdt_tfs)
    if not ict_df.empty: print(ict_df.to_string(index=False))

    state = load_state()
    new_ict = []
    now_ts = time.time()
    
    HALF_HOUR = 30 * 60

    for _, r in ict_df.iterrows() if not ict_df.empty else []:
        sym = r['Symbol']
        tf = r['TF']
        sig_type = r['Signal'] 
        key = f"ict_{sym}_{tf}"
        current_sig = f"{sig_type}_{now_ts}"
        
        if key in state:
            last_sig_str = state[key]
            last_type = last_sig_str.split("_")[0] 
            last_ts = float(last_sig_str.split("_")[1])
            
            if sig_type != last_type or (now_ts - last_ts) >= HALF_HOUR:
                new_ict.append(r)
                state[key] = current_sig
        else:
            new_ict.append(r)
            state[key] = current_sig

    ict_new_df = pd.DataFrame(new_ict) if new_ict else pd.DataFrame()
    save_state(state)

    msg = build_message(zdf, usdt_tfs, current_dom, ict_new_df)
    if msg:
        print("\nTelegram sent:", send_telegram(msg))
    else:
        print("\nNo alerts to send.")

if __name__ == "__main__":
    main()
