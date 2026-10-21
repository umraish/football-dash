import json, os
import fd
def stats(rows):
    s = [r for r in rows if r["result"] in ("won","lost")]; w = sum(r["result"]=="won" for r in s)
    return {"n": len(s), "won": w, "lost": len(s)-w, "rate": (w/len(s) if s else None),
            "exp": (sum(r["prob"] for r in s)/len(s) if s else None),
            "pl": round(sum((r["fair_odds"]-1) if r["result"]=="won" else -1 for r in s),2)}
def render(c, preds, status, today, now):
    c.row_factory = None
    cols = [d[1] for d in c.execute("PRAGMA table_info(tips)")]
    tips = [dict(zip(cols, r)) for r in c.execute("SELECT * FROM tips ORDER BY tip_date DESC, is_banker DESC, prob DESC")]
    summ = {"Overall": stats(tips), "Banker only": stats([t for t in tips if t["is_banker"]])}
    for key in ("market","league"):
        for v in sorted({t[key] for t in tips}): summ[f"{key.title()}: {v}"] = stats([t for t in tips if t[key]==v])
    data = {"today": today, "generated": now.astimezone(fd.LAGOS).strftime("%Y-%m-%d %H:%M WAT"), "tips": tips,
            "summary": summ, "preds": preds, "coverage": status,
            "params": {"banker_min": fd.BANKER_MIN, "secondary_min": fd.SECONDARY_MIN, "xi": fd.XI, "min_games": fd.MIN_TEAM_GAMES}}
    tpl = open(os.path.join(fd.ROOT, "template.html")).read()
    os.makedirs(os.path.join(fd.ROOT,"docs"), exist_ok=True)
    open(os.path.join(fd.ROOT, "docs", "index.html"), "w").write(tpl.replace("/*DATA*/null", json.dumps(data).replace("</", "<\\/")))
    json.dump(tips, open(os.path.join(fd.ROOT, "docs", "history.json"), "w"), indent=1)
