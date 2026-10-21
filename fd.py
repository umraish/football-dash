"""Core library: ESPN fetch (cached), Dixon-Coles model, SQLite tip log, settle, render."""
import json, os, gzip, sqlite3, time, math, urllib.request, html
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from scipy.stats import poisson

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "cache"); DB = os.path.join(ROOT, "data", "tips.sqlite")
LAGOS = timezone(timedelta(hours=1))
# slug -> (name, pool). Pools are fitted jointly so cup games link domestic strengths.
def _load_leagues():
    """Every ESPN soccer competition (220 in ESPN's catalogue). Women's competitions use the same pool
    since team ids are distinct; activity is probed and inactive ones are reported as skipped."""
    d = json.load(open(os.path.join(ROOT, "data", "espn_leagues.json")))
    return {l["slug"]: (l["name"], "GLOBAL") for l in d["leagues"]}
LEAGUES = _load_leagues()
# Competitions predicted & shown but never eligible as tips (college, youth, friendlies: unreliable form/rotation)
PICK_EXCLUDE = ("ncaa", "friendly", "u17", "u19", "u20", "u21", "u23")
def pick_eligible(slug): return not any(k in slug for k in PICK_EXCLUDE)
HIST_DAYS = 330; XI = 0.004  # time-decay per day (half-life ~170d)
MIN_TEAM_GAMES = 6; BANKER_MIN = 0.70; SECONDARY_MIN = 0.58

def _get(url):
    for i in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0","Accept-Encoding":"gzip"})
            with urllib.request.urlopen(req, timeout=20) as r:
                b = r.read()
                if r.headers.get("Content-Encoding") == "gzip": b = gzip.decompress(b)
                return json.loads(b)
        except Exception:
            time.sleep(1+i)
    return None

def scoreboard(slug, ymd, force=False):
    """ESPN scoreboard for one league / one UTC date. Past days are cached permanently."""
    p = os.path.join(CACHE, slug, ymd + ".json")
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    if os.path.exists(p) and not force and ymd < (datetime.now(timezone.utc)-timedelta(days=2)).strftime("%Y%m%d"):
        return json.load(open(p))
    d = _get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates={ymd}")
    if d is None: return json.load(open(p)) if os.path.exists(p) else {"events":[]}
    evs = [parse_event(e, slug) for e in d.get("events", [])]
    os.makedirs(os.path.dirname(p), exist_ok=True); json.dump(evs, open(p, "w"))
    return evs

def parse_event(e, slug):
    c = e["competitions"][0]; t = {x["homeAway"]: x for x in c["competitors"]}
    st = e["status"]["type"]
    def sc(x):
        try: return int(float(x.get("score")))
        except Exception: return None
    return {"id": e["id"], "slug": slug, "date": e["date"], "neutral": bool(c.get("neutralSite")),
            "home": t["home"]["team"]["displayName"], "hid": t["home"]["team"]["id"],
            "away": t["away"]["team"]["displayName"], "aid": t["away"]["team"]["id"],
            "hs": sc(t["home"]), "as": sc(t["away"]), "completed": bool(st.get("completed")),
            "state": st.get("state"), "detail": st.get("detail")}

def fetch_all(days=HIST_DAYS, workers=32):
    now = datetime.now(timezone.utc); D = lambda k: (now - timedelta(days=k)).strftime("%Y%m%d")
    with ThreadPoolExecutor(workers) as ex:
        probe = list(ex.map(lambda j: (j[0], scoreboard(*j)), [(s, D(k)) for s in LEAGUES for k in range(-2, 21)]))
    active = sorted({s for s, evs in probe if evs})
    json.dump(active, open(os.path.join(ROOT, "data", "active_leagues.json"), "w"))
    jobs = [(s, D(k)) for s in active for k in range(21, days)]
    with ThreadPoolExecutor(workers) as ex:
        res = [e for _, e in probe] + list(ex.map(lambda j: scoreboard(*j), jobs))
    out = {}
    for evs in res:
        for e in (evs if isinstance(evs, list) else []): out[e["id"]+e["slug"]] = e
    return list(out.values())

# ---------------- Dixon-Coles model ----------------
def tau(x, y, lh, la, rho):
    if x == 0 and y == 0: return 1 - lh*la*rho
    if x == 0 and y == 1: return 1 + lh*rho
    if x == 1 and y == 0: return 1 + la*rho
    if x == 1 and y == 1: return 1 - rho
    return 1.0

class Pool:
    """Time-decayed Poisson attack/defence fit (iterative ML), then Dixon-Coles rho by profile likelihood."""
    def __init__(self, matches, ref):
        ids = sorted({m["hid"] for m in matches} | {m["aid"] for m in matches}); self.ix = {t:i for i,t in enumerate(ids)}
        n = len(ids); H = np.array([self.ix[m["hid"]] for m in matches]); A = np.array([self.ix[m["aid"]] for m in matches])
        hg = np.array([m["hs"] for m in matches], float); ag = np.array([m["as"] for m in matches], float)
        age = np.array([(ref - datetime.fromisoformat(m["date"].replace("Z","+00:00"))).days for m in matches], float)
        w = np.exp(-XI*np.maximum(age, 0)); neu = np.array([m["neutral"] for m in matches])
        self.games = np.bincount(H, minlength=n) + np.bincount(A, minlength=n)
        att = np.ones(n); dfn = np.ones(n); home = 1.3
        for _ in range(200):
            hf = np.where(neu, 1.0, home)
            num = np.bincount(H, w*hg, n) + np.bincount(A, w*ag, n)
            den = np.bincount(H, w*hf*dfn[A], n) + np.bincount(A, w*dfn[H], n)
            att_new = (num + 1.0) / (den + 1.0)            # light shrink toward 1
            num = np.bincount(A, w*hg, n) + np.bincount(H, w*ag, n)
            den = np.bincount(A, w*hf*att_new[H], n) + np.bincount(H, w*att_new[A], n)
            dfn_new = (num + 1.0) / (den + 1.0)
            g = np.exp(np.mean(np.log(att_new))); att_new /= g; dfn_new *= g
            mh = ~neu; home = np.sum(w[mh]*hg[mh]) / np.sum(w[mh]*att_new[H[mh]]*dfn_new[A[mh]])
            done = np.max(np.abs(att_new-att)) < 1e-6; att, dfn = att_new, dfn_new
            if done: break
        self.att, self.dfn, self.home = att, dfn, home
        lh = att[H]*dfn[A]*np.where(neu,1,home); la = att[A]*dfn[H]
        best = (0, -1e18); low = (hg<=1)&(ag<=1); x, y, l1, l2, ww = hg[low], ag[low], lh[low], la[low], w[low]
        for rho in np.linspace(-0.2, 0.2, 81):
            t = np.where((x==0)&(y==0), 1-l1*l2*rho, np.where((x==0)&(y==1), 1+l1*rho, np.where((x==1)&(y==0), 1+l2*rho, 1-rho)))
            ll = float(np.sum(ww*np.log(np.maximum(t,1e-9))))
            if ll > best[1]: best = (rho, ll)
        self.rho = best[0]; self.n_matches = len(matches)

    def predict(self, hid, aid, neutral):
        if hid not in self.ix or aid not in self.ix: return None
        h, a = self.ix[hid], self.ix[aid]
        if self.games[h] < MIN_TEAM_GAMES or self.games[a] < MIN_TEAM_GAMES: return None
        lh = self.att[h]*self.dfn[a]*(1 if neutral else self.home); la = self.att[a]*self.dfn[h]
        G = 11; M = np.outer(poisson.pmf(range(G), lh), poisson.pmf(range(G), la))
        for x in (0,1):
            for y in (0,1): M[x,y] *= tau(x,y,lh,la,self.rho)
        M /= M.sum(); i, j = np.indices(M.shape)
        top = sorted(((M[x,y], f"{x}-{y}") for x in range(6) for y in range(6)), reverse=True)[:5]
        return {"xg_h": round(lh,2), "xg_a": round(la,2),
                "1": M[i>j].sum(), "X": M[i==j].sum(), "2": M[i<j].sum(),
                "BTTS Yes": M[(i>0)&(j>0)].sum(), "BTTS No": 1-M[(i>0)&(j>0)].sum(),
                "Over 2.5": M[i+j>2].sum(), "Under 2.5": M[i+j<=2].sum(),
                "scores": [(s, round(float(p),4)) for p, s in top]}

MARKET_OF = {"1":"1X2","X":"1X2","2":"1X2","BTTS Yes":"BTTS","BTTS No":"BTTS","Over 2.5":"O/U 2.5","Under 2.5":"O/U 2.5"}

def grade(sel, hs, as_):
    return {"1": hs>as_, "X": hs==as_, "2": hs<as_, "BTTS Yes": hs>0 and as_>0, "BTTS No": not (hs>0 and as_>0),
            "Over 2.5": hs+as_>2, "Under 2.5": hs+as_<=2}[sel]

# ---------------- storage ----------------
def db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS tips(id INTEGER PRIMARY KEY, logged_at TEXT, tip_date TEXT, event_id TEXT,
      slug TEXT, league TEXT, kickoff TEXT, home TEXT, away TEXT, market TEXT, selection TEXT, prob REAL, fair_odds REAL,
      is_banker INTEGER, result TEXT DEFAULT 'pending', score TEXT, settled_at TEXT, UNIQUE(tip_date,event_id,selection))""")
    c.execute("""CREATE TABLE IF NOT EXISTS predictions(tip_date TEXT, event_id TEXT, slug TEXT, json TEXT, PRIMARY KEY(tip_date,event_id))""")
    return c
