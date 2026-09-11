import sys, pathlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
sys.path.insert(0, '/mnt/c/Users/הלל/Desktop/algo projects/Alpacatrader/factory/scripts')
import flush_bot as fb

ET = ZoneInfo("America/New_York")
LOGS = []
fb.jlog = lambda event, **kw: LOGS.append({"event": event, **kw})

class S:
    def __init__(self, v): self.value = v
class Side:
    def __init__(self, v): self.value = v
class Order:
    def __init__(self, sym, side, oid, px, status="new", fq=0, cid="flushbot-b-x"):
        self.symbol=sym; self.side=Side(side); self.id=oid
        self.limit_price=px; self.status=S(status); self.filled_qty=fq
        self.client_order_id=cid; self.filled_avg_price=px; self.filled_at=None
class Pos:
    def __init__(self, sym, qty, avg): self.symbol=sym; self.qty=qty; self.avg_entry_price=avg
class Br:
    live=True
    def __init__(self): self.canceled=[]; self.closed=[]; self.buys=[]
    def cancel(self, oid): self.canceled.append(oid)
    def submit_buy(self, sym, q, p):
        o=Order(sym,"buy",f"b{len(self.buys)}",p); self.buys.append(o); return o
    def sell_oco(self, sym, q, stop, lim): return Order(sym,"sell","oco1",lim)
    def close_market(self, sym): self.closed.append(sym)
    def order(self, oid): return None

et_now = datetime(2026,9,11,11,0,tzinfo=ET)
bars = pd.DataFrame({
    "ts": pd.to_datetime([f"2026-09-11T{13+i//60:02d}:{i%60:02d}:00Z" for i in range(20)]),
    "et": list(range(570,590)),
    "close": [2.0]*15 + [2.0,2.0,2.05,2.2,2.4],
})
assert len(fb.state_minutes(bars,1.0))>0 and int(fb.state_minutes(bars,1.0)["et"].iloc[-1])==589
assert fb.qty_for(1.5)==0 and fb.qty_for(4.0)==500
assert len(fb.completed(bars, 500))==20            # pre-open keeps prior session
assert len(fb.completed(bars, 575))==5             # only completed minutes
assert fb.bar_index_at(bars, "2026-09-11T13:10:00Z")==10
bars_ns = pd.DataFrame({"ts": bars["ts"], "et": bars["et"], "close": [2.0]*20})
n=35
bars35 = pd.DataFrame({"ts": pd.to_datetime([f"2026-09-11T{13+i//60:02d}:{i%60:02d}:00Z" for i in range(n)]), "et": list(range(570,570+n)), "close": [2.0]*(n-5)+[2.0,2.0,2.05,2.2,2.4]})

# T1 refresh on tick change
m={"prev_close":1.0,"order_id":"o1","anchor_ts":et_now,"entry_B":1.8}
br=Br(); ords=[Order("AAA","buy","o1",1.80)]
fb.manage_symbol(br,"AAA",m,bars,{},ords,600,et_now)
assert "o1" in br.canceled and br.buys and m["entry_B"]==2.16, (br.canceled,br.buys,m)
print("T1 refresh OK", m["entry_B"])

# T2 expiry
m={"prev_close":1.0,"order_id":"o2","anchor_ts":et_now-timedelta(minutes=121),"entry_B":1.8}
br=Br(); ords=[Order("AAA","buy","o2",1.80)]
fb.manage_symbol(br,"AAA",m,bars_ns,{},ords,600,et_now)
assert "o2" in br.canceled and m["order_id"] is None
print("T2 expiry OK")

# T3 protect adopted/position
m={"prev_close":1.0,"entry_ts":et_now,"entry_B":1.8,"entry_bar_i":19}
br=Br(); pos={"AAA":Pos("AAA",500,1.80)}
fb.manage_symbol(br,"AAA",m,bars,pos,[],600,et_now)
assert m.get("oco_id")=="oco1"
print("T3 protect OK")

# T4 tl30 with protection already in place
m={"prev_close":1.0,"entry_ts":et_now-timedelta(minutes=30),"entry_B":1.8,"entry_bar_i":0,"oco_id":"z"}
br=Br(); pos={"AAA":Pos("AAA",500,1.80)}
fb.manage_symbol(br,"AAA",m,bars35,pos,[],600,et_now)
assert br.closed==["AAA"]
print("T4 tl30 OK")

# T5 exit bookkeeping
m={"prev_close":1.0,"entry_ts":et_now-timedelta(minutes=5),"entry_B":1.8,"oco_id":"z"}
br=Br(); LOGS.clear()
fb.manage_symbol(br,"AAA",m,bars,{},[],600,et_now)
assert m["entry_ts"] is None and m.get("last_exit_ts")==et_now and LOGS[0]["event"]=="exit"
print("T5 exit OK")

# T6 day-roll: stale meta + owned resting buy + open position -> cancel, close, reset
class Br2(Br):
    def __init__(self):
        super().__init__()
        self.pos={"AAA":Pos("AAA",100,2.0)}
    def clock(self): return True, fb.now_et()
    def open_orders(self):
        return [] if "o9" in self.canceled else [Order("AAA","buy","o9",1.8,cid="flushbot-aaa-1")]
    def positions(self): return dict(self.pos)
    def close_market(self, sym): self.closed.append(sym); self.pos.pop(sym,None)
    def bars(self, sym, start): return pd.DataFrame()

fb.now_et = lambda: datetime(2026,9,11,11,0,tzinfo=ET)
fb.day_dir = lambda: pathlib.Path("/tmp/2026-09-11")
fb.scan_candidates = lambda: (pd.DataFrame({"symbol":[],"close":[],"change":[],"rank":[]}), "test")
meta = {"_day":"2026-09-10", "AAA":{"prev_close":1.0,"order_id":"o9","anchor_ts":et_now}}
br2=Br2(); LOGS.clear()
fb.poll(br2, meta, probe=True)
assert "o9" in br2.canceled, br2.canceled
assert "AAA" in br2.closed, br2.closed
assert meta == {"_day":"2026-09-11"}, meta
assert any(l["event"]=="day_roll" for l in LOGS), LOGS
print("T6 day-roll OK")

# T7 clock: retry once, then local ET fallback
class TCfail:
    def __init__(self, n): self.n=n
    def get_clock(self):
        if self.n>0:
            self.n-=1; raise RuntimeError("500")
        class C: is_open=True; timestamp="T"
        return C()
fb.now_et = lambda: datetime(2026,9,11,11,0,tzinfo=ET)
b=fb.Broker.__new__(fb.Broker); b.tc=TCfail(99); b.dc=None; LOGS.clear()
open_, ts = b.clock()
assert open_ is True and ts.hour==11
assert any(l["event"]=="clock_fallback" for l in LOGS)
b2=fb.Broker.__new__(fb.Broker); b2.tc=TCfail(1); b2.dc=None; LOGS.clear()
o2, t2 = b2.clock()
assert o2 is True and t2=="T" and not any(l["event"]=="clock_fallback" for l in LOGS)
print("T7 clock retry/fallback OK")

print("ALL MOCK TESTS PASS")
