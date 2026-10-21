"""One command: fetch -> settle -> predict today -> render docs/index.html.  Usage: python run_daily.py [--date YYYY-MM-DD]"""
import sys, json
from datetime import datetime, timezone
import fd
from render import render

def main():
    now = datetime.now(timezone.utc)
    today = sys.argv[sys.argv.index("--date")+1] if "--date" in sys.argv else now.astimezone(fd.LAGOS).strftime("%Y-%m-%d")
    print("Fetching ESPN scoreboards ..."); evs = fd.fetch_all()
    byid = {(e["slug"], e["id"]): e for e in evs}
    c = fd.db()
    # settle
    n = 0
    for tid, slug, eid, sel in c.execute("SELECT id,slug,event_id,selection FROM tips WHERE result='pending'").fetchall():
        e = byid.get((slug, eid))
        if e and e["completed"] and e["hs"] is not None:
            r = "won" if fd.grade(sel, e["hs"], e["as"]) else "lost"
            c.execute("UPDATE tips SET result=?,score=?,settled_at=? WHERE id=?", (r, f'{e["hs"]}-{e["as"]}', now.isoformat(), tid)); n += 1
        elif e and e["state"] == "post" and not e["completed"]:
            c.execute("UPDATE tips SET result='void',score=?,settled_at=? WHERE id=?", (e["detail"], now.isoformat(), tid)); n += 1
    print(f"Settled {n} tips")
    # coverage + fit
    done = [e for e in evs if e["completed"] and e["hs"] is not None and e["date"] < now.isoformat()]
    status = {}
    for s, (name, pool) in fd.LEAGUES.items():
        status[s] = {"name": name, "hist": sum(1 for e in done if e["slug"] == s), "today": 0, "predicted": 0}
    pools = {}
    for p in {v[1] for v in fd.LEAGUES.values()}:
        ms = [e for e in done if fd.LEAGUES[e["slug"]][1] == p]
        if len(ms) > 50: pools[p] = fd.Pool(ms, now); print(f"Pool {p}: {len(ms)} matches, home={pools[p].home:.2f}, rho={pools[p].rho:.3f}")
    fixtures = [e for e in evs if e["state"] == "pre" and
                datetime.fromisoformat(e["date"].replace("Z","+00:00")).astimezone(fd.LAGOS).strftime("%Y-%m-%d") == today]
    preds = []
    for e in sorted(fixtures, key=lambda e: e["date"]):
        status[e["slug"]]["today"] += 1
        pool = pools.get(fd.LEAGUES[e["slug"]][1]); pr = pool.predict(e["hid"], e["aid"], e["neutral"]) if pool else None
        if not pr: continue
        status[e["slug"]]["predicted"] += 1
        pr = {k: (round(float(v),4) if isinstance(v,(float,int)) or hasattr(v,"dtype") else v) for k, v in pr.items()}
        preds.append({"event": e, "p": pr, "league": fd.LEAGUES[e["slug"]][0]})
        c.execute("INSERT OR IGNORE INTO predictions VALUES(?,?,?,?)", (today, e["id"], e["slug"], json.dumps(pr)))
    # picks: lock on first run of the day (no silent re-picking)
    if not c.execute("SELECT 1 FROM tips WHERE tip_date=?", (today,)).fetchone() and preds:
        cands = sorted(((x["p"][sel], sel, x) for x in preds if fd.pick_eligible(x['event']['slug']) for sel in fd.MARKET_OF), key=lambda t: -t[0])
        used, picks = set(), []
        for prob, sel, x in cands:
            if x["event"]["id"] in used: continue
            if not picks and prob < fd.BANKER_MIN: break
            if picks and prob < fd.SECONDARY_MIN: break
            picks.append((prob, sel, x)); used.add(x["event"]["id"])
            if len(picks) == 6: break
        for k, (prob, sel, x) in enumerate(picks):
            e = x["event"]
            c.execute("INSERT OR IGNORE INTO tips(logged_at,tip_date,event_id,slug,league,kickoff,home,away,market,selection,prob,fair_odds,is_banker) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (now.isoformat(), today, e["id"], e["slug"], x["league"], e["date"], e["home"], e["away"], fd.MARKET_OF[sel], sel, round(prob,4), round(1/prob,2), int(k==0)))
        if not picks: print("No pick met the banker threshold today.")
    c.commit()
    json.dump(status, open(fd.ROOT+"/data/coverage.json","w"), indent=1)
    render(c, preds, status, today, now)
    for r in c.execute("SELECT is_banker,league,home,away,selection,prob,fair_odds FROM tips WHERE tip_date=? ORDER BY is_banker DESC, prob DESC", (today,)):
        print(("BANKER " if r[0] else "  tip  "), r[1], f"{r[2]} v {r[3]}", r[4], f"p={r[5]:.1%} fair={r[6]}")

if __name__ == "__main__": main()
