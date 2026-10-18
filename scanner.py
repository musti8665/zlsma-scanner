# ============================================================
# Ultimate Scanner: ICT Only + Smart USDT.D (RSI + EMA + BB)
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
            with open(STATE_FILE) as f:
                data = json.load(f)
                if isinstance(data, dict): return data
        except Exception:
            pass
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# ==========================================
# 🟢 تحلیل هوشمند دامیننس تتر در هر تایم‌فریم
# ==========================================
def get_usdt_dominance_trend():
    tfs = {"5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h"}
    results = {}
    current_dom = None
    
    # گرفتن دامیننس فعلی
    try:
        r = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
        data = r.json()["data"]
        r2 = requests.get("https://api.coingecko.com/api/v3/coins/tether", timeout=10)
        usdt_data = r2.json()
        current_dom = round((usdt_data["market_data"]["market_cap"]["usd"] / data["total_market_cap"]["usd"]) * 100, 2)
    except:
        pass

    for tf, interval in tfs.items():
        try:
            # دریافت دیتا در همان تایم‌فریم
            days = 2 if tf in ["5m", "15m"] else 7 if tf == "1h" else 30
            df = yf.download("USDT-USD", period=f"{days}d", interval=interval, progress=False)
            
            if df.empty or len(df) < 20:
                results[tf] = {"trend": "داده کافی نیست", "bias": "neutral", "emoji": "➡️"}
                continue

            df = df[["Open","High","Low","Close"]].dropna()
            df.columns = ["open","high","low","close"]
            
            # 🟢 ۱. محاسبه RSI (۱۴ دوره)
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['rsi'] = 100 - (100 / (1 + rs))
            
            # 🟢 ۲. محاسبه EMA ۲۰ و ۵۰
            df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
            df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
            
            # 🟢 ۳. محاسبه Bollinger Bands (۲۰ دوره)
            df['bb_mid'] = df['close'].rolling(20).mean()
            df['bb_std'] = df['close'].rolling(20).std()
            df['bb_upper'] = df['bb_mid'] + (df['bb_std'] * 2)
            df['bb_lower'] = df['bb_mid'] - (df['bb_std'] * 2)
            
            # آخرین مقادیر
            last = df.iloc[-1]
            rsi = last['rsi']
            ema20 = last['ema20']
            ema50 = last['ema50']
            close = last['close']
            bb_upper = last['bb_upper']
            bb_lower = last['bb_lower']
            
            # 🟢 منطق تصمیم‌گیری
            analysis = ""
            bias = "neutral"
            emoji = "➡️"
            
            # تعیین وضعیت بر اساس RSI
            if rsi > 70:
                rsi_state = "نزدیک مقاومت (اشباع خرید)"
            elif rsi < 30:
                rsi_state = "نزدیک حمایت (اشباع فروش)"
            elif rsi > 50:
                rsi_state = "بالای وسط (نزولی خفیف)"
            else:
                rsi_state = "زیر وسط (صعودی خفیف)"
                
            # تعیین وضعیت بر اساس EMA
            if ema20 > ema50:
                ema_state = "روند صعودی"
            else:
                ema_state = "روند نزولی"
                
            # تعیین وضعیت بر اساس Bollinger
            if close > bb_upper:
                bb_state = "بالاتر از باند بالا (مقاومت قوی)"
            elif close < bb_lower:
                bb_state = "پایین‌تر از باند پایین (حمایت قوی)"
            else:
                bb_state = "داخل باندها (نوسان عادی)"
                
            # ترکیب نهایی
            if rsi < 30 and ema20 > ema50:
                analysis = f"🟢 صعودی قوی | RSI: {rsi:.1f} | {ema_state} | {bb_state}"
                bias = "bullish"
                emoji = "📉"
            elif rsi < 50 and ema20 > ema50:
                analysis = f"🟡 صعودی خفیف | RSI: {rsi:.1f} | {ema_state} | {bb_state}"
                bias = "bullish"
                emoji = "📉"
            elif rsi > 70 and ema20 < ema50:
                analysis = f"🔴 نزولی قوی | RSI: {rsi:.1f} | {ema_state} | {bb_state}"
                bias = "bearish"
                emoji = "📈"
            elif rsi > 50 and ema20 < ema50:
                analysis = f"🟠 نزولی خفیف | RSI: {rsi:.1f} | {ema_state} | {bb_state}"
                bias = "bearish"
                emoji = "📈"
            else:
                analysis = f"⚪ خنثی | RSI: {rsi:.1f} | {ema_state} | {bb_state}"
                bias = "neutral"
                emoji = "➡️"
                
            results[tf] = {"trend": analysis, "bias": bias, "emoji": emoji}
        except Exception as e:
            print(f"USDT.D {tf} err: {e}")
            results[tf] = {"trend": "خطا در تحلیل", "bias": "neutral", "emoji": "➡️"}
            
    return results, current_dom

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
                
                if len(signals) > 1:
                    signals = [max(signals, key=lambda x: x["score"])]

                for sig in signals:
                    if sig["score"] < 2:
                        continue
                        
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

def build_message(usdt_tfs, current_dom, ict_df, follow_ups):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    msg = f"<b>📊 اسکنر ICT</b> — {now}\n\n"
    
    msg += f"<b>📉 تحلیل دامیننس تتر ({current_dom}%):</b>\n"
    for tf, data in usdt_tfs.items():
        msg += f"• {tf}: {data['trend']}\n"
    msg += "\n"

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

    if ict_df.empty and not follow_ups:
        return None

    msg += "⚠️ این پیام صرفاً تحلیلی است، نه سیگنال خرید/فروش."
    return msg

def main():
    print(f"Run: {datetime.utcnow():%Y-%m-%d %H:%M:%S} UTC\n")

    usdt_tfs, current_dom = get_usdt_dominance_trend()

    print("=" * 70); print("ICT Liquidity Sweep Scanner"); print("=" * 70)
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
                    current_df = yf.download(data["Symbol"] + "-USD", period="7d", interval=tf, progress=False)
                    if isinstance(current_df.columns, pd.MultiIndex):
                        current_df.columns = current_df.columns.get_level_values(0)
                    if not current_df.empty and len(current_df) > 0:
                        current_df = current_df.iloc[:-1]
                        current_price = current_df["Close"].iloc[-1]
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

    msg = build_message(usdt_tfs, current_dom, ict_new_df, follow_ups)
    if msg:
        print("\nTelegram sent:", send_telegram(msg))
    else:
        print("\nNo alerts to send.")

if __name__ == "__main__":
    main()
