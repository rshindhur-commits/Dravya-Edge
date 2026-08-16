"""Is the exit a TREND question rather than a profit-percentage question?

The operator's objection, and it is the better framing: a give-back rule keyed to
percent gained is a P&L rule. What should end a trade is the trend that started
it breaking -- whether the position is up 3% or 30%.

The app already exits on trend. It does it in 21 minutes on a single 5-minute
bar's excursion, which is noise rather than a broken trend. `EXIT_EMA_CONFIRM_BARS`
exists to make it wait for confirmation and has never been measured.

Trend is read on the UNDERLYING throughout. The option's own EMA is contaminated
by theta and spread, so a trend break measured on premium is partly a decay
signal.
"""
import pathlib, sys, statistics as st
sys.path.insert(0, str(pathlib.Path.cwd()))
from dotenv import load_dotenv; load_dotenv()
import pandas as pd
from sqlalchemy import text
from app.db.connection import get_engine
from app.backtesting.historical_market_data import fetch_bars

def num(v):
    try:
        f=float(v); return None if f!=f else f
    except (TypeError, ValueError): return None

ARM, KEEP, BE, STOP_ATR = 25.0, 0.5, 10.0, 1.5
ARMS = ["ACTUAL","ema9 now","ema9 confirm1","ema9 confirm2","ema9 confirm3",
        "ema20 now","two-tier P&L","ema9c2 + two-tier"]

with get_engine().begin() as c:
    rows = c.execute(text("""
        SELECT symbol, direction, option_ticker, entry_price, option_entry_mid,
               option_close_mid, pnl_pct, days_held, opened_at
        FROM paper_trades WHERE status='CLOSED' AND option_ticker IS NOT NULL
          AND option_entry_mid > 0 ORDER BY opened_at""")).mappings().all()

res = {k: [] for k in ARMS}; peaks = []

for r in rows:
    if (num(r["days_held"]) or 1) > 1: continue
    paid = num(r["option_entry_mid"])
    day = str(pd.Timestamp(r["opened_at"]).tz_convert("America/New_York").date())
    try:
        o = fetch_bars(r["option_ticker"], day, day, multiplier=5, timespan="minute")
        u = fetch_bars(r["symbol"], day, day)
    except Exception: continue
    if o is None or not len(o) or u is None or not len(u): continue
    o = o.copy(); o.index = pd.to_datetime(o.index, utc=True).tz_convert("America/New_York")
    o = o.between_time("09:30","16:00")
    u = u.copy(); u.index = u.index.tz_convert("America/New_York"); u = u.between_time("09:30","16:00")
    u["e9"]  = u["Close"].ewm(span=9,  adjust=False).mean()
    u["e20"] = u["Close"].ewm(span=20, adjust=False).mean()
    cl = u["Close"]
    sp = pd.concat([u["High"]-u["Low"], (u["High"]-cl.shift()).abs(),
                    (u["Low"]-cl.shift()).abs()], axis=1).max(axis=1)
    u["atr"] = sp.ewm(span=14, adjust=False).mean()

    op = pd.Timestamp(r["opened_at"]).tz_convert("America/New_York")
    fw = o[o.index >= op]
    if len(fw) < 4: continue
    before = u[u.index <= op]
    if not len(before): continue
    atr = num(before["atr"].iloc[-1]); ep = num(r["entry_price"])
    is_call = str(r["direction"] or "").upper() == "CALL"
    hard = (ep - STOP_ATR*atr if is_call else ep + STOP_ATR*atr) if (ep and atr) else None

    got = num(r["option_close_mid"])
    res["ACTUAL"].append(((got-paid)/paid*100) if got else (num(r["pnl_pct"]) or 0.0))
    peaks.append(max((num(fw["High"].max())-paid)/paid*100, -100.0))

    def broken(ts, col, confirm):
        w = u[u.index <= ts]
        if len(w) < confirm + 1: return False
        for k in range(confirm + 1):
            bar = w.iloc[-1-k]
            c2, e = num(bar["Close"]), num(bar[col])
            if c2 is None or e is None: return False
            if not ((c2 < e) if is_call else (c2 > e)): return False
        return True

    for arm in ARMS[1:]:
        peak, out = -100.0, None
        for ts, bar in fw.iterrows():
            hi, c2 = num(bar["High"]), num(bar["Close"])
            if hi is None or c2 is None: continue
            if hard is not None:
                w = u[(u.index >= op) & (u.index <= ts)]
                if len(w):
                    lo3, hi3 = num(w["Low"].iloc[-1]), num(w["High"].iloc[-1])
                    if lo3 is not None and hi3 is not None:
                        if (lo3 <= hard) if is_call else (hi3 >= hard):
                            out = (c2-paid)/paid*100; break
            peak = max(peak, (hi-paid)/paid*100); gain = (c2-paid)/paid*100
            trend_out = False
            if arm == "ema9 now":            trend_out = broken(ts,"e9",0)
            elif arm == "ema9 confirm1":     trend_out = broken(ts,"e9",1)
            elif arm in ("ema9 confirm2","ema9c2 + two-tier"): trend_out = broken(ts,"e9",2)
            elif arm == "ema9 confirm3":     trend_out = broken(ts,"e9",3)
            elif arm == "ema20 now":         trend_out = broken(ts,"e20",0)
            if trend_out: out = gain; break
            if arm in ("two-tier P&L","ema9c2 + two-tier"):
                floor = peak*KEEP if peak >= ARM else (0.0 if peak >= BE else None)
                if floor is not None and gain <= floor: out = gain; break
        if out is None: out = (num(fw["Close"].iloc[-1])-paid)/paid*100
        res[arm].append(out)

n = len(peaks); big = sum(1 for x in peaks if x >= 25.0)
print("")
print("  %d live trades. Trend read on the UNDERLYING; hard stop on every arm." % n)
print("")
print("  %-20s%9s%9s%10s%7s%10s%11s" % ("arm","mean","-top5","total","win","RNDTRIP","kept>=25%"))
print("  " + "-"*76)
for k in ARMS:
    v = res[k]
    if not v: continue
    green = [i for i,x in enumerate(peaks) if x >= 10.0]
    trip = sum(1 for i in green if v[i] <= 0)/len(green)*100 if green else 0
    kept = sum(1 for i,x in enumerate(peaks) if x >= 25.0 and v[i] >= 25.0)
    print("  %-20s%+8.2f%%%+8.2f%%%+9.1f%%%6.0f%%%9.0f%%%10.0f%%"
          % (k, st.mean(v), st.mean(sorted(v)[:-5]), sum(v),
             sum(1 for x in v if x>0)/len(v)*100, trip, kept/max(big,1)*100))
