"""Independent, read-only recomputation from persisted quote fields for audit only."""
from __future__ import annotations
import json, random, re, sqlite3, statistics, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.sli import config

FIELDS = [f'{side}_{kind}_{level}' for side in ('bid','ask') for level in range(1,4) for kind in ('price','volume')]
EXCLUDED = {'one_sided_limit','auction_phase','off_tick','crossed_book','late_message','future_source_timestamp','invalid_source_timestamp'}


def tick(price, market):
    if price is None or price <= 0: return None
    market = (market or 'HOSE').upper()
    if market == 'HOSE':
        if price < 10_000: return 10.0
        if price <= 49_950: return 50.0
        if price >= 50_000: return 100.0
        return None
    if market in {'HNX','UPCOM'}: return 100.0
    return None


def snapshot_metrics(row):
    bid, ask = row['bid_price_1'], row['ask_price_1']
    mid = spread_bps = spread_ticks = None
    if bid is not None and ask is not None and bid > 0 and ask > 0 and ask >= bid:
        mid = (bid + ask) / 2
        if mid > 0:
            spread_bps = (ask - bid) / mid * 10_000
            step = tick(mid, row['market'])
            spread_ticks = (ask - bid) / step if step else None
    side = {}
    for name in ('bid','ask'):
        levels=[]
        for i in range(1,4):
            price, volume = row[f'{name}_price_{i}'], row[f'{name}_volume_{i}']
            if price is not None and volume is not None and price > 0 and volume >= 0:
                levels.append((float(price),float(volume)))
        side[name+'_qty'] = sum(q for _,q in levels) if levels else None
        side[name+'_value'] = sum(p*q for p,q in levels) if levels else None
    bq,aq,bv,av=side['bid_qty'],side['ask_qty'],side['bid_value'],side['ask_value']
    tq=bq+aq if bq is not None and aq is not None else None
    tv=bv+av if bv is not None and av is not None else None
    return {'spread_bps':spread_bps,'spread_ticks':spread_ticks,'total_depth_value':tv,
            'queue_imbalance':(bq-aq)/tq if tq and tq>0 else None}


def api_bars_readonly(symbol):
    """Call the actual FastAPI route with its DB connector patched to URI mode=ro."""
    from fastapi.testclient import TestClient
    from src.sli import api, database
    uri=config.DB_PATH.resolve().as_uri()+'?mode=ro'
    opened=[]
    def ro_connect():
        db=sqlite3.connect(uri,uri=True,timeout=15,check_same_thread=False)
        db.row_factory=sqlite3.Row;opened.append(db);return db
    original=database.connect
    database.connect=ro_connect
    try:
        response=TestClient(api.app).get(f'/api/bars?symbol={symbol}&source=ssi&limit=10000')
        if response.status_code != 200:
            return response.status_code, []
        return response.status_code,response.json().get('bars',[])
    finally:
        database.connect=original
        for db in opened:
            try: db.close()
            except sqlite3.Error: pass


def main():
    generated=datetime.now(config.TIMEZONE).isoformat(timespec='seconds')
    dbpath=config.DB_PATH
    result={'generated_at_vn':generated,'database_read_only':True,'source':'ssi','random_seed':1106,
            'method':'Independent formulas from persisted bid/ask fields; median by received minute; excludes invalid and flagged rows exactly as documented for bars. API queried through GET /api/bars using read-only SQLite connections.',
            'tolerance':1e-6,'selected_symbols':[],'checks':[],'summary':{}}
    if not dbpath.exists():
        result['summary']={'status':'UNVERIFIED','reason':'No local SQLite database.'}
        return result
    con=sqlite3.connect(dbpath.resolve().as_uri()+'?mode=ro',uri=True,timeout=30);con.row_factory=sqlite3.Row
    candidates=[r[0] for r in con.execute("""SELECT DISTINCT b.symbol FROM bars_1m b
       JOIN securities_catalog c ON c.source='ssi' AND c.symbol=b.symbol
       WHERE b.source='ssi' AND lower(coalesce(c.stock_type,'stock')) IN
       ('stock','common stock','equity','ordinary share','common equity') ORDER BY b.symbol""")]
    if len(candidates)<5:
        result['summary']={'status':'UNVERIFIED','reason':f'Only {len(candidates)} stock symbols with bars; five are required.'}
        con.close();return result
    random.seed(1106);symbols=random.sample(candidates,5);result['selected_symbols']=symbols
    all_errors=[];api_checked=0;api_errors=[]
    for symbol in symbols:
        raw=con.execute("""SELECT symbol,received_at_vn,market,session_phase,validation_status,flags,
           bid_price_1,bid_volume_1,bid_price_2,bid_volume_2,bid_price_3,bid_volume_3,
           ask_price_1,ask_volume_1,ask_price_2,ask_volume_2,ask_price_3,ask_volume_3
           FROM observations WHERE source='ssi' AND symbol=? ORDER BY received_at_vn,id""",(symbol,)).fetchall()
        grouped=defaultdict(lambda:defaultdict(list));sample_counts=defaultdict(int)
        for row in raw:
            if row['validation_status']!='valid' or row['session_phase'] not in {'CONTINUOUS_AM','CONTINUOUS_PM'}: continue
            try: flags=set(json.loads(row['flags'] or '[]'))
            except (TypeError,ValueError): flags=set()
            if flags & EXCLUDED: continue
            stamp=row['received_at_vn'] or ''
            key=stamp[:16]
            if len(key)!=16: continue
            metrics=snapshot_metrics(row)
            sample_counts[key]+=1
            for field,value in metrics.items():
                if value is not None: grouped[key][field].append(value)
        stored={r['minute_vn'][:16]:dict(r) for r in con.execute("SELECT * FROM bars_1m WHERE source='ssi' AND symbol=? ORDER BY minute_vn",(symbol,))}
        recomputed={}
        for minute,metrics in grouped.items():
            recomputed[minute]={k:statistics.median(v) for k,v in metrics.items() if v}
        compared=0;errors={k:0.0 for k in ('spread_bps','spread_ticks','total_depth_value','queue_imbalance')};missing=[]
        for minute,calc in recomputed.items():
            bar=stored.get(minute)
            if not bar: missing.append(minute);continue
            for field in errors:
                a,b=calc.get(field),bar.get(field)
                if a is None and b is None: continue
                if a is None or b is None:
                    errors[field]=float('inf');continue
                errors[field]=max(errors[field],abs(float(a)-float(b)))
                compared+=1
        status,api_rows=api_bars_readonly(symbol)
        api_by_min={r['minute_vn'][:16]:r for r in api_rows}
        api_count=0;api_max=0.0
        for minute,bar in stored.items():
            api=api_by_min.get(minute)
            if not api:continue
            api_count+=1
            for field in errors:
                a,b=bar.get(field),api.get(field)
                if a is None and b is None:continue
                if a is None or b is None:api_max=float('inf');continue
                api_max=max(api_max,abs(float(a)-float(b)))
        api_checked+=api_count
        if status!=200:api_errors.append({'symbol':symbol,'status_code':status})
        all_errors.extend(v for v in errors.values())
        result['checks'].append({'symbol':symbol,'raw_snapshots':len(raw),'eligible_minutes_recomputed':len(recomputed),
            'bars_compared':len(set(recomputed)&set(stored)),'metric_value_comparisons':compared,'missing_bar_minutes':len(missing),
            'raw_vs_bars_max_abs_error':errors,'api_status':status,'api_bars_compared':api_count,
            'bars_vs_api_max_abs_error':api_max})
    con.close()
    numeric=[x for x in all_errors if x!=float('inf')]
    max_error=max(numeric,default=0.0)
    any_inf=any(x==float('inf') for x in all_errors)
    result['summary']={'raw_vs_bars_status':'PASS' if not any_inf and max_error<1e-6 else 'FAIL',
        'api_status':'PASS' if not api_errors and api_checked>0 and all(c['bars_vs_api_max_abs_error']<1e-6 for c in result['checks']) else 'UNVERIFIED' if api_errors or not api_checked else 'FAIL',
        'max_abs_error':max_error,'api_bar_comparisons':api_checked,'api_errors':api_errors}
    return result


if __name__=='__main__':
    out=main(); target=Path(__file__).with_name('results.json')
    target.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(out['summary'],ensure_ascii=False))
