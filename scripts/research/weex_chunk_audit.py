from __future__ import annotations

import hashlib
import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ENDPOINT = "https://api-contract.weex.com/capi/v3/market/historyKlines"
STEP_MS = 300_000
LIMIT = 100
SYMBOL = os.environ["SYMBOL"]
RUN_LABEL = os.environ["RUN_LABEL"]
CHUNK = os.environ["CHUNK"]
START = datetime.fromisoformat(os.environ["START"].replace("Z", "+00:00"))
END = datetime.fromisoformat(os.environ["END"].replace("Z", "+00:00"))
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)
OUT = Path(f".audit/{RUN_LABEL}-{SYMBOL}-{CHUNK}.json")

@dataclass(frozen=True)
class Row:
    t:int; o:Decimal; h:Decimal; l:Decimal; c:Decimal; bv:Decimal; ct:int; qv:Decimal; n:Decimal; tb:Decimal; tq:Decimal
    def key(self)->tuple[Any,...]:
        return (self.t,str(self.o),str(self.h),str(self.l),str(self.c),str(self.bv),self.ct,str(self.qv),str(self.n),str(self.tb),str(self.tq))

def iso(ms:int)->str:
    return datetime.fromtimestamp(ms/1000,tz=UTC).isoformat().replace("+00:00","Z")

def d(v:Any)->Decimal:
    if v is None or isinstance(v,bool) or not str(v).strip(): raise InvalidOperation(f"bad decimal {v!r}")
    x=Decimal(str(v))
    if not x.is_finite(): raise InvalidOperation(f"nonfinite {v!r}")
    return x

def parse(raw:Any)->Row:
    if not isinstance(raw,list) or len(raw)!=11: raise ValueError(f"bad row shape {raw!r}")
    return Row(int(raw[0]),d(raw[1]),d(raw[2]),d(raw[3]),d(raw[4]),d(raw[5]),int(raw[6]),d(raw[7]),d(raw[8]),d(raw[9]),d(raw[10]))

def fetch(params:dict[str,Any])->tuple[list[Any],int]:
    url=ENDPOINT+"?"+urllib.parse.urlencode(params)
    retries=0
    for attempt in range(4):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"atb-weex-chunk-audit/1.0"})
            with urllib.request.urlopen(req,timeout=30) as r:
                data=json.loads(r.read().decode())
            if not isinstance(data,list): raise RuntimeError("non-list response")
            return data,retries
        except urllib.error.HTTPError as e:
            if not (e.code==429 or 500<=e.code<=599) or attempt==3: raise
        except (urllib.error.URLError,TimeoutError,ConnectionError,http.client.IncompleteRead,http.client.RemoteDisconnected):
            if attempt==3: raise
        retries+=1; time.sleep(2**attempt)
    raise AssertionError

def main()->None:
    rows:dict[int,Row]={}; dup:dict[int,list[tuple[Any,...]]]=defaultdict(list)
    malformed=[]; off=set(); bad_ohlc=set(); bad_price=set(); bad_add=set(); bad_close=set(); retries=0; requests=0; unrecovered=[]
    end=END_MS-STEP_MS
    while end>=START_MS:
        start=max(START_MS,end-(LIMIT-1)*STEP_MS)
        requests+=1
        try:
            raw_rows,r=fetch({"symbol":SYMBOL,"interval":"5m","startTime":start,"endTime":end,"limit":((end-start)//STEP_MS)+1,"priceType":"LAST"})
            retries+=r
        except Exception as e:
            unrecovered.append({"start":iso(start),"end":iso(end),"error":f"{type(e).__name__}: {e}"}); break
        for raw in raw_rows:
            try: candidate=int(raw[0]) if isinstance(raw,list) and raw else None
            except Exception: candidate=None
            if candidate is None:
                malformed.append({"t":None,"reason":"bad open time"}); continue
            if candidate<start or candidate>end: continue
            try: row=parse(raw)
            except Exception as e:
                malformed.append({"t":iso(candidate),"reason":f"{type(e).__name__}: {e}"}); continue
            if not (START_MS<=row.t<END_MS): continue
            if (row.t-START_MS)%STEP_MS: off.add(row.t)
            if row.ct!=row.t+STEP_MS: bad_close.add(row.t)
            ps=(row.o,row.h,row.l,row.c)
            if any(x<=0 for x in ps): bad_price.add(row.t)
            if not (row.h>=max(row.o,row.c) and row.l<=min(row.o,row.c) and row.h>=row.l): bad_ohlc.add(row.t)
            if any(x<0 for x in (row.bv,row.qv,row.n,row.tb,row.tq)): bad_add.add(row.t)
            old=rows.get(row.t)
            if old is None: rows[row.t]=row
            else: dup[row.t].append(row.key())
        end=start-STEP_MS
        time.sleep(0.05)
    expected=set(range(START_MS,END_MS,STEP_MS)); missing=sorted(expected-set(rows)); conflicts=sorted(t for t,vals in dup.items() if any(v!=rows[t].key() for v in vals))
    result={
      "run_label":RUN_LABEL,"symbol":SYMBOL,"chunk":CHUNK,"range":[START.isoformat(),END.isoformat()],
      "expected":len(expected),"unique":len(rows),"missing_count":len(missing),"missing":[iso(x) for x in missing],
      "duplicate_count":len(dup),"duplicates":[iso(x) for x in sorted(dup)],"conflict_count":len(conflicts),"conflicts":[iso(x) for x in conflicts],
      "malformed_count":len(malformed),"malformed":malformed,"off_grid_count":len(off),"off_grid":[iso(x) for x in sorted(off)],
      "invalid_ohlc_count":len(bad_ohlc),"invalid_ohlc":[iso(x) for x in sorted(bad_ohlc)],"nonpositive_price_count":len(bad_price),"nonpositive_price":[iso(x) for x in sorted(bad_price)],
      "invalid_additive_count":len(bad_add),"invalid_additive":[iso(x) for x in sorted(bad_add)],"close_misalignment_count":len(bad_close),"close_misalignment":[iso(x) for x in sorted(bad_close)],
      "requests":requests,"retries":retries,"unrecovered_count":len(unrecovered),"unrecovered":unrecovered,
    }
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    summary={k:result[k] for k in ("run_label","symbol","chunk","expected","unique","missing_count","duplicate_count","conflict_count","malformed_count","off_grid_count","invalid_ohlc_count","nonpositive_price_count","invalid_additive_count","close_misalignment_count","requests","retries","unrecovered_count")}
    summary["sha256"]=hashlib.sha256(OUT.read_bytes()).hexdigest(); print(json.dumps(summary,indent=2),flush=True)

if __name__=="__main__": main()
