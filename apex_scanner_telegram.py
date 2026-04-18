import requests
import schedule
import time
import os
from datetime import datetime
from collections import deque

TWELVEDATA_KEY   = os.environ.get("TWELVEDATA_KEY",  "")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",  "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID","")

ASSETS = {
    "XAU/USD": {"symbol":"XAU/USD","name":"Gold","emoji":"🥇","sl_dist":35.0,"rr_mult":[1.5,2.8,5.5],"decimals":2},
    "EUR/USD": {"symbol":"EUR/USD","name":"Euro","emoji":"💶","sl_dist":0.002,"rr_mult":[1.5,2.5,4.0],"decimals":4},
}
MACRO_SYMBOLS = ["DXY","WTI/USD","VIX","TNX"]
ECO_CALENDAR  = [
    {"date":"2026-04-29","time":"18:00","event":"Fed Rate Decision","impact":"HIGH"},
    {"date":"2026-05-02","time":"14:30","event":"US Non-Farm Payrolls","impact":"HIGH"},
    {"date":"2026-05-09","time":"14:30","event":"US CPI Inflation","impact":"HIGH"},
    {"date":"2026-04-25","time":"14:30","event":"US PCE Inflation","impact":"HIGH"},
]

prices       = {s: deque(maxlen=100) for s in ASSETS}
macro_data   = {}
last_signal  = {s: 0 for s in ASSETS}
signal_count = 0

def fetch_price(symbol):
    try:
        r = requests.get("https://api.twelvedata.com/price",
            params={"symbol":symbol,"apikey":TWELVEDATA_KEY},timeout=8)
        d = r.json()
        if "price" in d: return float(d["price"])
    except: pass
    return None

def fetch_all_prices():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetch prix...")
    for sym, asset in ASSETS.items():
        p = fetch_price(asset["symbol"])
        if p: prices[sym].append(p); print(f"  {asset['emoji']} {sym}: {p:.{asset['decimals']}f}")
        time.sleep(0.8)

def fetch_macro():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetch macro...")
    for sym in MACRO_SYMBOLS:
        p = fetch_price(sym)
        if p:
            key = sym.replace("/USD","").lower()
            prev = macro_data.get(key)
            macro_data[key] = p
            if prev: macro_data[f"{key}_change"] = ((p-prev)/prev)*100
        time.sleep(0.8)

def calc_rsi(arr, p=14):
    if len(arr)<p+1: return 50.0
    data=list(arr); g=l=0
    for i in range(len(data)-p,len(data)):
        d=data[i]-data[i-1]
        if d>0: g+=d
        else:   l-=d
    return 100-100/(1+(g/(l or 0.0001)))

def calc_ema(arr, p):
    data=list(arr)
    if not data: return 0
    k=2/(p+1); e=data[0]
    for x in data[1:]: e=x*k+e*(1-k)
    return e

def calc_macd(arr): return calc_ema(arr,12)-calc_ema(arr,26)

def check_news_risk():
    now=datetime.now()
    for ev in ECO_CALENDAR:
        ev_dt=datetime.strptime(f"{ev['date']} {ev['time']}","%Y-%m-%d %H:%M")
        diff=(ev_dt-now).total_seconds()/60
        if ev["impact"]=="HIGH":
            if -5<=diff<=30:  return {"blocked":True, "warning":False,"reason":f"{ev['event']} dans {int(diff)} min"}
            if 30<diff<=120:  return {"blocked":False,"warning":True, "reason":f"{ev['event']} dans {round(diff/60,1)}h"}
    return {"blocked":False,"warning":False,"reason":None}

def get_next_news():
    now=datetime.now()
    up=[(datetime.strptime(f"{e['date']} {e['time']}","%Y-%m-%d %H:%M"),e) for e in ECO_CALENDAR]
    up=[x for x in up if x[0]>now]; up.sort(key=lambda x:x[0])
    return up[0] if up else None

def get_macro_score(symbol, direction):
    score=0; details=[]
    dxy=macro_data.get("dxy"); dch=macro_data.get("dxy_change",0)
    vix=macro_data.get("vix"); bonds=macro_data.get("tnx")
    if dxy:
        if symbol=="XAU/USD":
            if direction=="LONG"   and dch<-0.2: score+=20; details.append("DXY ↓ → Gold ✅")
            elif direction=="LONG"  and dch>0.2: score-=10; details.append("DXY ↑ → Gold ⚠️")
            elif direction=="SHORT" and dch>0.2: score+=15; details.append("DXY ↑ → Short Gold ✅")
            else: score+=5
        else:
            if direction=="LONG"   and dch<-0.2: score+=20
            elif direction=="SHORT" and dch>0.2: score+=20
            else: score+=5
    else: score+=5
    if vix:
        if vix>25:
            if symbol=="XAU/USD" and direction=="LONG": score+=20; details.append(f"VIX {vix:.1f} Safe haven ✅")
            else: score+=5
        elif vix<15: score+=10
        else:        score+=8
    else: score+=8
    if bonds:
        if symbol=="XAU/USD":
            if direction=="LONG" and bonds<4.0:   score+=15; details.append(f"Taux {bonds:.2f}% bas ✅")
            elif direction=="LONG" and bonds>4.5: score-=10; details.append(f"Taux {bonds:.2f}% hauts ⚠️")
            else: score+=5
        else: score+=8
    else: score+=8
    score+=5
    news=check_news_risk()
    if news["blocked"]: return {"score":0,"blocked":True,"reason":news["reason"],"details":details}
    if news["warning"]: score-=15; details.append(f"⚠️ News: {news['reason']}")
    else:               score+=15; details.append("Pas de news ✅")
    return {"score":max(0,min(100,score)),"blocked":False,"details":details}

def detect_signal(symbol, asset):
    arr=prices[symbol]
    if len(arr)<30: return None
    rsi=calc_rsi(arr); ema9=calc_ema(arr,9); ema21=calc_ema(arr,21)
    ema50=calc_ema(arr,50); macd=calc_macd(arr)
    data=list(arr); last=data[-1]; prev=data[-2]
    p9=calc_ema(data[:-1],9); p21=calc_ema(data[:-1],21)
    d=s=None; c=6
    if   rsi<38 and ema9>ema21 and last>ema50 and last>prev:                d="LONG"; s="RSI Oversold + Haussier";   c=7
    elif p9<=p21 and ema9>ema21 and 45<rsi<65 and macd>0:                  d="LONG"; s="Golden Cross EMA 9/21";     c=8
    elif 55<rsi<72 and last>ema9 and last>ema21 and last>ema50:             d="LONG"; s="Momentum Breakout";         c=7
    elif rsi>68 and ema9<ema21 and last<ema50 and last<prev:                d="SHORT";s="RSI Overbought + Baissier"; c=7
    elif p9>=p21 and ema9<ema21 and 35<rsi<55 and macd<0:                  d="SHORT";s="Death Cross EMA 9/21";      c=8
    elif rsi<35 and last<ema9 and last<ema21:                               d="SHORT";s="Momentum Breakdown";        c=6
    if not d: return None
    dist=asset["sl_dist"]; sl=last-dist if d=="LONG" else last+dist
    tps=[(last+dist*m) if d=="LONG" else (last-dist*m) for m in asset["rr_mult"]]
    return {"direction":d,"setup":s,"conviction":c,"entry":last,"sl":sl,
            "tp1":tps[0],"tp2":tps[1],"tp3":tps[2],"rr":asset["rr_mult"][1],"rsi":round(rsi,1),"time":datetime.now()}

def get_confluence(symbol, sig):
    macro=get_macro_score(symbol,sig["direction"])
    if macro["blocked"]: return {"score":0,"blocked":True,"reason":macro["reason"]}
    tech=min(sig["conviction"]*8+(10 if sig["rsi"]<35 or sig["rsi"]>65 else 0),60)
    return {"score":min(100,int(macro["score"]*.5+tech*.5)),"blocked":False}

def send_telegram(msg):
    try:
        r=requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id":TELEGRAM_CHAT_ID,"text":msg,"parse_mode":"HTML"},timeout=10)
        print("  ✅ Telegram OK" if r.status_code==200 else f"  ❌ {r.text}")
    except Exception as e: print(f"  ❌ Telegram: {e}")

def format_signal(symbol, asset, sig, conf):
    d=asset["decimals"]; isL=sig["direction"]=="LONG"
    bar="█"*int(conf["score"]/10)+"░"*(10-int(conf["score"]/10))
    ce="🟢" if conf["score"]>=70 else "🟡" if conf["score"]>=50 else "🟠"
    ml=[]
    if macro_data.get("dxy"): ml.append(f"  DXY: {macro_data['dxy']:.2f}")
    if macro_data.get("vix"): ml.append(f"  VIX: {macro_data['vix']:.1f}")
    if macro_data.get("tnx"): ml.append(f"  US10Y: {macro_data['tnx']:.2f}%")
    nn=get_next_news()
    nl=f"\n📅 Prochaine news : <b>{nn[1]['event']}</b> dans {round((nn[0]-datetime.now()).total_seconds()/3600,1)}h" if nn else ""
    return f"""⚡ <b>APEX TRADER — SIGNAL</b>

{asset['emoji']} <b>{symbol}</b> · {'▲ ACHETER LONG' if isL else '▼ VENDRE SHORT'}
📊 {sig['setup']}
🔥 Conviction : {sig['conviction']}/10

━━━━━━━━━━━━━━━━━━━━
🟢 <b>ENTRÉE :</b>    {sig['entry']:.{d}f}
🔴 <b>STOP LOSS :</b> {sig['sl']:.{d}f}
🏆 <b>TP1 :</b>       {sig['tp1']:.{d}f}
🏆 <b>TP2 :</b>       {sig['tp2']:.{d}f}
🏆 <b>TP3 :</b>       {sig['tp3']:.{d}f}
📐 <b>R:R :</b>       {sig['rr']}:1

{ce} <b>CONFLUENCE : {conf['score']}%</b>
{bar}

🌍 <b>MACRO</b>
{chr(10).join(ml) if ml else '  En attente...'}
{nl}

━━━━━━━━━━━━━━━━━━━━
📋 <b>EXÉCUTION</b>
1. Broker → <b>{symbol}</b>
2. <b>{'BUY MARKET' if isL else 'SELL MARKET'}</b>
3. SL → <b>{sig['sl']:.{d}f}</b>
4. TP → <b>{sig['tp2']:.{d}f}</b>
5. Risque : <b>1.5% capital</b>

⏰ {sig['time'].strftime('%H:%M:%S')} · APEX v3.0
⚠️ <i>Éducatif — trade à tes propres risques</i>"""

def analyze_all():
    global signal_count
    for symbol, asset in ASSETS.items():
        if len(prices[symbol])<30: continue
        if time.time()-last_signal[symbol]<30: continue
        sig=detect_signal(symbol,asset)
        if not sig: print(f"  🔍 {symbol}: RSI={calc_rsi(prices[symbol]):.1f} — pas de signal"); continue
        conf=get_confluence(symbol,sig)
        if conf["blocked"]:
            print(f"  🚫 {symbol}: bloqué — {conf['reason']}")
            send_telegram(f"🚫 <b>SIGNAL BLOQUÉ — {symbol}</b>\n\n⚠️ {conf['reason']}"); continue
        if conf["score"]<40: print(f"  ⚠️  {symbol}: confluence {conf['score']}% — ignoré"); continue
        last_signal[symbol]=time.time(); signal_count+=1
        print(f"\n  ⚡ SIGNAL — {symbol} {sig['direction']} | {conf['score']}%")
        send_telegram(format_signal(symbol,asset,sig,conf))

def run_cycle():
    fetch_all_prices()
    analyze_all()

if __name__=="__main__":
    print("APEX TRADER — Cloud Railway\n")
    if not all([TWELVEDATA_KEY,TELEGRAM_TOKEN,TELEGRAM_CHAT_ID]):
        print("❌ Variables manquantes !"); exit(1)
    print("✅ Config OK\n")
    fetch_all_prices(); fetch_macro(); analyze_all()
    nn=get_next_news()
    nl=f"\n📅 Prochaine news : <b>{nn[1]['event']}</b> dans {round((nn[0]-datetime.now()).total_seconds()/3600,1)}h" if nn else ""
    send_telegram(f"""🚀 <b>APEX TRADER — Cloud Actif 24h/24</b>

✅ Twelve Data : connecté
✅ Telegram : actif
📡 XAU/USD · EUR/USD
⏱️ Refresh : 15s · Macro : 60s
🚫 Blocage auto avant news HIGH
{nl}

<i>Signal dès que confluence ≥ 40%</i>""")
    schedule.every(15).seconds.do(run_cycle)
    schedule.every(60).seconds.do(fetch_macro)
    print("✅ Scanner 24h/24 actif\n")
    while True:
        schedule.run_pending()
        time.sleep(1)
