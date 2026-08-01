# Always-on scan worker (Render Background Worker)

Scanning currently runs as a daemon thread inside the Streamlit process
(`app/runtime/scan_supervisor.py`). Streamlit Community Cloud only runs the app
while a browser is connected, so no viewer means no scanning — and a redeploy
replaces the container mid-scan. This moves scanning to a process that does not
depend on either.

Streamlit stays as the viewer. Nothing about the trading logic changes.

---

## 1. Create the service

**GitHub repo, not Docker.** Render's native Python runtime defaults to 3.14.3
and this project runs 3.14.5 locally, so there is no version gap to bridge and
nothing in the dependency set needs system packages. Docker would add a build
step to maintain for no benefit. `.python-version` pins `3.14` in the repo root.

<https://dashboard.render.com/worker/new> → connect this repository.

| Setting | Value |
| --- | --- |
| Type | **Background Worker** |
| Region | **Virginia (US East)** — same as the Neon database |
| Branch | `Claude_Overtake_Dravya_GPT` |
| Build command | `pip install -r requirements.txt` |
| Start command | `python -m app.runtime.scan_loop` |
| Instance | **Starter, $7/mo, 512 MB** |

512 MB is enough: a full 26-symbol scan peaks at **155 MB** resident (16 MB
before imports, 125 MB after). Roughly 3.3× headroom.

## 2. Environment variables

Copy every value from local `.env` into the Render service. `.env` is gitignored
and untracked, so nothing ships in the repo — the worker gets its configuration
entirely from Render.

The one that must differ:

```
SCAN_ENGINE_OWNER = worker
```

`scan_loop` already defaults its own heartbeat identity to `worker`, but setting
it explicitly is what makes the value visible in the Render dashboard next to
everything else.

## 3. Cut over

Ownership moves by environment variable, never by deploy. A deploy replaces the
container, which kills the in-flight scan and drops open positions — so this has
to be movable during market hours, when pushing is barred.

1. Deploy the worker with **`SCAN_ENGINE_OWNER = dashboard`** on the Streamlit
   side and `worker` on Render. Both heartbeat; only the dashboard scans.
   Confirm the worker appears in the sidebar System block.
2. Watch for one session.
3. **After 16:00 ET**, set Streamlit's `SCAN_ENGINE_OWNER = worker`. The
   in-process supervisor stops starting; the worker is now the only scanner.
4. Confirm the System block shows `Engine … · worker` and no conflict banner.

### The one dangerous configuration

Two engines scanning at once. `app/runtime/scan_lock.py` is a **file** on local
disk, so it cannot serialise anything across two hosts — both engines will open
positions for the same candidate.

The System block raises this as an error whenever more than one owner has
heartbeated inside 15 minutes. Treat that banner as an incident.

## 4. What the worker does not solve

Ephemeral disk. Render gives a Background Worker no persistent disk by default,
so `data/daily/` is wiped on every deploy exactly as it is on Streamlit Cloud
today. This is survivable because Postgres is the durable store — `paper_trades`,
`candidate_snapshot` and the rest all persist, and `restore_open_trades_from_db()`
re-adopts open positions at the first scan of a new process.

Alert dedup **was** the subscriber-visible one and is now closed:
`telegram_alert_state` (migration 027) mirrors every dedup key, and
`_hydrate_alert_state_from_db()` re-adopts them on the first read in a new
process. A restart can no longer re-send the day's review alerts or a second
copy of the weekly results.

Still local-only, and still a gap:

| State | Risk on restart |
| --- | --- |
| `suggested_trade_state.json` | Suggestion lifecycle resets. No table exists. Not subscriber-visible. |
| `auto_paper_settings.json` | Falls back to code defaults. Gitignored, and now on a *different host* from the sidebar controls that write it — those controls become decorative once the worker owns scanning. Decide whether to move them to Postgres or remove them. |
