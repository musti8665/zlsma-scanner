# ============================================================
# Combined Scanner: ZLSMA (3m/5m/15m/1h/4h) + ICT Liquidity Sweep
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
    "SHIBUSDT","PEOPLEUSDT","ZECUSDT","ARKMUSDT","MYXUSDT","TIAUSDT",
    "ICPUSDT","RUNEUSDT","DOGSUSDT","BOMEUSDT","SEIUSDT",
    "NOTUSDT","APTUSDT","WIFUSDT","AVAXUSDT","WLDUSDT","MEMEUSDT","PEPEUSDT",
    "ARUSDT","ADAUSDT","NEARUSDT","OPUSDT","SUIUSDT","BONKUSDT",
    "CKBUSDT","POPCATUSDT",
]
ZLSMA_TF = {"3m":"3m", "5m":"5m", "15m":"15m", "1h":"1h", "4h":"4h"}
ZLSMA_LEN = 32
ZLSMA_OFF = 0
BINANCE_BASE = "https://api.binance.com/api/v3/klines"

ICT_SYMBOLS = [
    "SHIB-USD","ICP-USD","SEI-USD","NOT-USD","WIF-USD","AVAX-USD",
    "WLD-USD","PEPE-USD","ADA-USD","NEAR-USD","OP-USD","BONK-USD",
    "SOL-USD","INJ-USD","FIL-USD","ARB-USD","GALA-USD","AAVE-USD",
    "STX-USD","THETA-USD","RUNE-USD","DOGE-USD","HBAR-USD",
]
ICT_TF = "15m"
ICT_LOOKBACK_DAYS = 7
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

def fetch_klines(symbol, interval, limit=200):
    r = requests.get(BINANCE_BASE,
                     params={"symbol": symbol, "interval": interval, "limit": limit},
                     timeout=15)
    r.raise_for_status()
    data = r.json()
    df = pd.DataFrame(data, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","qav","trades","tbbav","tbqav","ignore"])
    df["close"] = df["close"].astype(float)
    return df

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
        row = {"Symbol": sym}
        for tf_name, tf in ZLSMA_TF.items():
            try:
                limit = 200 if tf_name in ("3m","5m","15m") else 300
                df = fetch_klines(sym, tf, limit=limit)
                z  = zlsma(df["close"])
                row[tf_name] = trend_of(z)
            except Exception:
                row[tf_name] = "ERR"
            time.sleep(0.12)
        rows.append(row)
    df = pd.DataFrame(rows)
    df["Score"] = (df[tf_keys] == "UP").sum(axis=1)
    df = df.sort_values("Score", ascending=False).reset_index(drop=True)
    return df

def get_usdt_dominance_trend():
    try:
        r = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
        data = r.json()["data"]
        r2 = requests.get("https://api.coingecko.com/api/v3/coins/tether", timeout=10)
        usdt_data = r2.json()
        usdt_mcap  = usdt_data["market_data"]["market_cap"]["usd"]
        total_mcap = data["total_market_cap"]["usd"]
        dominance  = (usdt_mcap / total_mcap) * 100
        market_chg = data["market_cap_change_percentage_24h_usd"]
        usdt_chg   = usdt_data["market_data"]["market_cap_change_percentage_24h"]
        if usdt_chg > market_chg + 0.3:
            trend, emoji, bias = "صعودی", "📈", "bearish"
        elif usdt_chg < market_chg - 0.3:
            trend, emoji, bias = "نزولی", "📉", "bullish"
        else:
            trend, emoji, bias = "خنثی", "➡️", "neutral"
        return {"dominance": round(dominance, 2), "trend": trend, "emoji": emoji,
                "bias": bias, "usdt_change": round(usdt_chg, 2),
                "market_change": round(market_chg, 2)}
    except Exception as e:
        print("USDT.D error:", e)
        return {"dominance": None, "trend": "نامشخص", "emoji": "❓",
                "bias": "neutral", "usdt_change": 0, "market_change": 0}

def yf_data(symbol, interval=ICT_TF, days=ICT_LOOKBACK_DAYS):
    end = datetime.now(); start = end - timedelta(days=days)
    df = yf.download(symbol, start=start, end=end, interval=interval, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if df.empty or len(df) < 60: return None
    df = df[["Open","High","Low","Close"]].dropna()
    df.columns = ["open","high","low","close"]
    return df

def find_pivots(df, length=PIVOT_LENGTH):
    highs = df["high"].values; lows = df["low"].values
    n = len(df); ph, pl = [], []
    for i in range(length, n - length):
        if highs[i] == np.max(highs[i-length:i+length+1]):
            ph.append({"index": i, "price": highs[i]})
        if lows[i] == np.min(lows[i-length:i+length+1]):
            pl.append({"index": i, "price": lows[i]})
    return ph, pl

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
    if final <= 3:   return final, "ابتدای راه", "🟢"
    elif final <= 6: return final, "وسط راه",   "🟡"
    else:            return final, "آخر راه",    "🔴"

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

def scan_ict():
    usdt = get_usdt_dominance_trend()
    results = []
    for symbol in ICT_SYMBOLS:
        try:
            df = yf_data(symbol)
            if df is None: continue
            ph, pl = find_pivots(df)
            signals = detect_sweeps(df, ph, pl)
            clean = symbol.replace("-USD", "/USDT")
            for sig in signals:
                if usdt["bias"] == "bullish":
                    rel = "موافق روند" if sig["type"]=="LONG"  else "خلاف روند"
                elif usdt["bias"] == "bearish":
                    rel = "موافق روند" if sig["type"]=="SHORT" else "خلاف روند"
                else:
                    rel = "خنثی"
                results.append({"Symbol": clean, "Signal": sig["type"],
                                "Score": sig["score"], "Stage": sig["stage"],
                                "USDT.D Relation": rel,
                                "Level": round(sig["level"], 8),
                                "Price": round(sig["price"], 8)})
        except Exception as e:
            print(f"ICT {symbol} err: {str(e)[:80]}")
    return usdt, pd.DataFrame(results) if results else pd.DataFrame()

def build_message(zdf, usdt, ict_df):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    msg = f"<b>📊 اسکنر ترکیبی</b> — {now}\n\n"

    # --- بخش ZLSMA ---
    if not zdf.empty:
        msg += "📈 <b>ZLSMA Trend (وضعیت ارزها)</b>\n"
        msg += "<pre>\n"
        msg += f"{'Symbol':<9} | {'3m':<2} | {'5m':<2} | {'15m':<3} | {'1h':<2} | {'4h':<2} | Score\n"
        msg += "-" * 45 + "\n"
        
        for _, r in zdf.iterrows():
            sym_short = r['Symbol'].replace("USDT", "")[:9]
            t3 = "🟢" if r.get('3m') == "UP" else "🔴"
            t5 = "🟢" if r.get('5m') == "UP" else "🔴"
            t15 = "🟢" if r.get('15m') == "UP" else "🔴"
            t1h = "🟢" if r.get('1h') == "UP" else "🔴"
            t4h = "🟢" if r.get('4h') == "UP" else "🔴"
            score = int(r['Score'])
            msg += f"{sym_short:<9} | {t3}  | {t5}  | {t15}  | {t1h}  | {t4h}  | {score}/5\n"
            
        msg += "</pre>\n\n"

    # --- بخش ICT ---
    if not ict_df.empty:
        msg += f"💧 <b>ICT Liquidity Sweep</b> ({len(ict_df)} سیگنال)\n"
        msg += f"USDT.D: {usdt['emoji']} {usdt['trend']}"
        if usdt["dominance"]:
            msg += f" ({usdt['dominance']}%)"
        msg += "\n\n"
        for _, r in ict_df.iterrows():
            emo = "🟢" if r["Signal"] == "LONG" else "🔴"
            msg += (f"{emo} <code>{r['Symbol']}</code> {r['Signal']} "
                    f"| ⭐{r['Score']} {r['Stage']} | {r['USDT.D Relation']}\n")
        msg += "\n"

    if zdf.empty and ict_df.empty:
        return None

    msg += "⚠️ این پیام صرفاً تحلیلی است، نه سیگنال خرید/فروش."
    return msg

def main():
    print(f"Run: {datetime.utcnow():%Y-%m-%d %H:%M:%S} UTC\n")

    # 1) ZLSMA
    print("=" * 70); print("ZLSMA Scanner (5 TFs)"); print("=" * 70)
    zdf = scan_zlsma()
    print(zdf.to_string(index=False))

    # 2) ICT
    print("\n" + "=" * 70); print("ICT Liquidity Sweep"); print("=" * 70)
    usdt, ict_df = scan_ict()
    print(f"USDT.D: {usdt['emoji']} {usdt['trend']} ({usdt.get('dominance')}%)")
    if not ict_df.empty: print(ict_df.to_string(index=False))

    # 3) منطق ضد اسپم (۲ ساعته + تغییر جهت)
    state = load_state()
    new_ict = []
    now_ts = time.time()
    TWO_HOURS = 2 * 60 * 60  # 2 ساعت به ثانیه

    for _, r in ict_df.iterrows() if not ict_df.empty else []:
        sym = r['Symbol']
        sig_type = r['Signal'] 
        key = f"ict_{sym}"
        current_sig = f"{sig_type}_{now_ts}"
        
        if key in state:
            last_sig_str = state[key]
            last_type = last_sig_str.split("_")[0] 
            last_ts = float(last_sig_str.split("_")[1])
            
            if sig_type != last_type or (now_ts - last_ts) >= TWO_HOURS:
                new_ict.append(r)
                state[key] = current_sig
        else:
            new_ict.append(r)
            state[key] = current_sig

    ict_new_df = pd.DataFrame(new_ict) if new_ict else pd.DataFrame()
    save_state(state)

    # 4) ارسال پیام
    msg = build_message(zdf, usdt, ict_new_df)
    if msg:
        print("\nTelegram sent:", send_telegram(msg))
    else:
        print("\nNo alerts to send.")

if __name__ == "__main__":
    main()
