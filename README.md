# Football Dash v1
`python run_daily.py` → fetch ESPN public scoreboards (all 220 ESPN soccer competitions probed, active ones get ~330 days history; cached in cache/), settle pending tips, fit Dixon-Coles, lock today's picks (first run of the day only), write docs/index.html + docs/history.json.
Files: fd.py (fetch/model/db), run_daily.py, render.py, template.html, settle.py, data/tips.sqlite, data/coverage.json.
Publish: push repo to github.com/umraish/football-dash, Settings → Pages → branch main, folder /docs. Run daily via cron or a GitHub Action, then commit docs/ + data/tips.sqlite.
