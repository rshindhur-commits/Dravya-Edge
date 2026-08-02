# Database storage and backup

How the database is kept from filling up, and how the data is preserved before
it gets deleted.

---

## Why this exists

### The problem

The database was going to run out of space in about two weeks, and when that
happened the scanner would have stopped recording anything.

The database is on Neon's free plan, which allows about **512 MB**. On 2 August
2026 it was at **241 MB** and growing by about **34 MB every trading day**. At
that rate it had roughly nine or ten trading days left. A full database rejects
new data, which would have stopped both the scanner and the dashboard.

Nothing in the system was deleting old data. Every scan wrote detailed logs and
they simply accumulated, going back to the day each table was created.

Trading records were not the cause. Every trade the system has ever recorded
takes up less than 1 MB in total. The growth was almost entirely diagnostic
logs — records of what the scanner looked at, which rules passed or failed, and
why it did or did not take a trade. Two tables alone accounted for three
quarters of the growth.

There was also no backup. If the database had been lost, everything would have
gone with it.

### The solution

Two things, working independently.

**1. Delete old diagnostic logs automatically.** The Render worker now removes
logs past a set age every day, so those tables stop growing instead of
expanding forever. Trading records are never deleted. This holds the database at
around 330 MB instead of letting it climb past 512 MB.

**2. Copy the whole database to your laptop twice a week.** Every table is
exported, compressed, and checked. This runs on Sundays and Wednesdays, more
often than the shortest deletion limit of seven days, so nothing is deleted
before it has been copied.

The first part stops the database filling up. The second part means the deleted
data still exists somewhere. Neither would be safe on its own.

---

## Two separate mechanisms

There are two independent pieces. They are not connected, and it is important
to understand that they are separate.

| | Retention | Export |
| --- | --- | --- |
| What it does | Deletes old rows from the database | Copies the whole database to your laptop |
| Where it runs | On the Render worker, in the cloud | On your laptop |
| How often | Every day | Every Sunday and Wednesday, 6:00 PM |
| Triggered by | The scanner's own loop | Windows Task Scheduler |

**Retention deletes. Export preserves. Retention does not back anything up
before deleting.** If the export stops running, retention will keep deleting and
that data will be gone permanently.

The reason they cannot be combined: the Render worker's storage is wiped every
time it restarts or is redeployed. Anything the worker saved to its own disk
would disappear. A backup has to be written to a machine that keeps its files,
which means your laptop.

---

## Part 1: Retention (automatic deletion)

### What gets deleted

Twelve diagnostic tables have a time limit. Any row older than that limit is
deleted. The limits are different per table because the tables grow at very
different rates and are needed for different lengths of time.

| Time limit | Tables |
| --- | --- |
| **7 days** | `activity_trace_event` |
| **10 days** | `event_stream` |
| **21 days** | `scanner_snapshot`, `decision_waterfall`, `rule_evaluation`, `alert_events`, `gate_decisions`, `candidate_snapshot`, `candidate_evidence`, `candidate_outcome`, `auto_paper_decision`, `scanner_runs` |

`activity_trace_event` has the shortest limit because it is the largest single
source of growth, at about 16 MB per trading day on its own.

`scanner_snapshot` is kept longer than its size would justify because the
historical replay tools read from it. `tools/regression_ab.py` needs roughly ten
trading days of history to work. A shorter limit would not produce an error — it
would quietly return less data and the comparison would silently be based on
too little history.

### What never gets deleted

Twenty-two tables are excluded permanently, listed by name in
`app/db/retention.py` under `NEVER_PRUNED`. This includes every table holding a
trading record: `trade`, `paper_trades`, `recommendation_fact`,
`trade_exit_analysis`, and the regression baselines.

These are the records behind every profit and loss figure the system reports.
They total under 1 MB, so deleting them would save nothing meaningful. There is
a test that fails if anyone ever adds a rule targeting one of them.

### How "days" are counted

The limits are in **calendar days**, not trading days. Because the market is
closed on weekends, a 21-calendar-day limit holds about 15 trading days of data,
and a 7-calendar-day limit holds about 5 trading days.

This distinction caused a mistake during setup. `scanner_snapshot` was
originally set to 14 days on the belief that this comfortably exceeded the ten
days the replay tools need. It does not — 14 calendar days is exactly 10 trading
days, with no margin at all, and fewer than 10 in any week containing a public
holiday. The limit was raised to 21 days to correct this.

### When it runs

The Render worker runs the scanner in a loop that never exits. It wakes up every
few minutes, decides whether to scan, then goes back to sleep. Retention is
attached to that loop. Each time the loop wakes up, two conditions are checked:

1. **Is the market closed?** If the market is open, retention is skipped
   entirely. Deleting rows while scans are writing to the same tables would slow
   both down.

2. **Has it already run today?** The worker writes today's date to a small file,
   `data/live/retention_state.json`, after each run. If the date in that file
   matches today, nothing happens.

So over a weekend the loop reaches this check hundreds of times and runs the
deletion exactly twice — once on Saturday, once on Sunday. On a weekday it runs
after the market closes.

If the worker restarts, that small file is lost and retention runs one extra
time. This causes no harm: deleting rows older than seven days a second time
finds nothing left to delete. This is also why the marker is a file rather than
a database table — the consequence of losing it is trivial.

If retention fails for any reason, the error is recorded and the scanner
continues normally. The file is not updated, so it will try again on the next
pass rather than skipping the day.

### How the deletion is done

Rows are deleted in batches of 5,000 rather than all at once. A single delete
covering tens of thousands of rows would hold a lock on the table long enough to
interfere with anything else using it.

### Why the database size does not drop

This is the most confusing part of the system, and it is expected behaviour.

When rows are deleted, PostgreSQL marks that space as free for reuse but does
not return it to Neon. The reported database size stays the same. The space is
reused by new rows as they arrive, so the database stops growing — but the
number does not go down.

The `--vacuum` option runs a cleanup that makes the freed space available for
reuse more promptly. It still does not shrink the reported total.

Only a command called `VACUUM FULL` actually returns space to Neon, and it locks
the entire table while it runs, which would stop the scanner. It is deliberately
not part of this system.

**The correct way to judge whether retention is working is that the database
size stops climbing, not that it falls.**

### Running it by hand

```
python tools/run_retention.py                    # show what would be deleted, delete nothing
python tools/run_retention.py --apply            # actually delete
python tools/run_retention.py --apply --vacuum   # delete, then free the space for reuse
```

Without `--apply` nothing is deleted. This is the default because deletion
cannot be undone.

### Changing the limits

Each table's limit can be overridden with an environment variable, for example:

```
RETENTION_KEEP_DAYS_ACTIVITY_TRACE_EVENT=10
```

A value that is not a whole number, or is less than 1, is ignored and the
built-in default is used instead. This is deliberate: a value of 0 would mean
"keep nothing" and would empty the table.

Before raising any limit, note that the limits were chosen to fit the 512 MB
budget. Keeping 14 days of everything would settle at about 476 MB, which is 93%
of the limit. The current settings settle at about 330 MB, which is 64%.

---

## Part 2: Export (automatic backup)

### What it produces

Each export creates a new folder named with the date and time it ran, in UTC:

```
D:\Dravya_Trade_Works_backup\20260802T200606Z\
    schema.sql          instructions for recreating the tables
    manifest.json       a record of what was exported and the verification result
    tables\
        trade.csv.gz
        activity_trace_event.csv.gz
        ... one file per table, 34 in total
```

Every table is exported, including the ones retention never touches. Each is a
spreadsheet-style text file, compressed. The compression is substantial: 241 MB
inside the database becomes about 33 MB of files, because the data is mostly
text and text compresses well.

### Why this format

Compressed text files were chosen over more specialised formats for three
reasons:

1. They are produced by a PostgreSQL command called `COPY`, which handles every
   kind of data the database can store without any conversion step. A conversion
   step is somewhere errors can occur silently.
2. They are loaded back with a matching command, so restoring is straightforward.
3. They can be opened by ordinary tools without any database software.

### The verification step

After writing the files, the export reads every one of them back and counts the
records, then compares that count against the number of rows the database
reported. If any table does not match, the export reports a failure.

The counting is done by properly parsing each file, not by counting lines. Some
of the data contains line breaks inside individual values, so counting lines
would produce a number that is too high and the check would be meaningless.

The result of this check is written into `manifest.json`, so you can confirm
after the fact that an old archive was verified when it was created.

### When it runs

Windows Task Scheduler runs it every **Sunday and Wednesday at 6:00 PM**.

Twice a week rather than once, because the shortest retention limit is seven
days. A weekly backup would sit exactly on that boundary, so a single missed run
would mean data was deleted that was never copied. Two runs per week leave
several days of margin.

Three settings on the scheduled task matter:

- **Run task as soon as possible after a scheduled start is missed** — if the
  laptop is off or asleep at 6:00 PM, the backup runs when it is next switched
  on, rather than being skipped until the following week.
- **Start the task even if the computer is on battery** — otherwise it would be
  skipped whenever the laptop is unplugged.
- **Do not stop if the computer switches to battery** — otherwise unplugging
  during the export would interrupt it.

### Old archives

By default the ten most recent archives are kept and older ones are deleted.
At two per week that is about five weeks of history, using roughly 330 MB.

Two rules protect this from going wrong:

- Old archives are only deleted **after** the new export has passed
  verification. If an export fails, nothing is removed, and the failure message
  says so explicitly.
- A folder is only ever considered for deletion if it contains a
  `manifest.json`. An unrelated folder placed in the backup directory will not
  be touched.

Use `--keep 6` to hold fewer, or `--no-prune` to keep everything.

### Checking whether you are covered

```
python tools/export_db.py --check
```

This reports how long ago the last export ran and compares it against the
shortest retention limit. If the gap has been exceeded, it says `STALE` and
exits with an error code, which means data may already have been deleted without
having been copied.

This reads the archive folder on your laptop. It tells you whether your backup
is current. It does not tell you whether retention ran on the worker.

### Running it by hand

```
python tools/export_db.py               # full export, keeping the newest 10
python tools/export_db.py --keep 6      # keep fewer
python tools/export_db.py --no-prune    # keep all
python tools/export_db.py --check       # report backup freshness only
```

Note that `python` must be the one inside the project's `.venv` folder. The
system-wide Python does not have the required packages installed. From the
project directory:

```
.\.venv\Scripts\python.exe tools\export_db.py
```

---

## Using an archive

### Reading the data without a database

Each `.csv.gz` file can be opened directly in Python:

```python
import pandas as pd
df = pd.read_csv('tables/activity_trace_event.csv.gz')
```

### Loading an archive into a database

`schema.sql` creates the tables, then each file is loaded into its table:

```
psql "postgresql://user:password@host/dbname?sslmode=require" -f schema.sql
psql "postgresql://user:password@host/dbname?sslmode=require" \
  -c "\copy activity_trace_event FROM PROGRAM 'gzip -dc tables/activity_trace_event.csv.gz' CSV HEADER"
```

This works against any PostgreSQL database — the existing one, a new Neon
database, or a different provider entirely. This requires the PostgreSQL command
line tools to be installed.

### A limitation of schema.sql

`schema.sql` is assembled by inspecting the existing database rather than by
using PostgreSQL's own backup tool. It correctly captures the columns, their
data types, default values, which columns are required, primary keys, and
indexes.

It does **not** capture foreign key relationships, check constraints, sequence
ownership, or user permissions.

This is enough to recreate the tables and load the data back in. It is not a
complete reproduction of the database structure. If a guaranteed-complete
structural backup is needed, install the PostgreSQL command line tools and run:

```
pg_dump --schema-only "postgresql://user:password@host/dbname?sslmode=require" > schema_full.sql
```

Note the connection address for this must not contain `-pooler`. Neon provides
two addresses; the pooled one is designed for many short connections and can
interrupt the long-running connections these tools need.

---

## Where everything lives

| File | Purpose |
| --- | --- |
| `app/db/retention.py` | The time limits, the excluded tables, and the deletion logic |
| `app/runtime/retention_scheduler.py` | Decides whether today's deletion is due |
| `app/runtime/scan_loop.py` | Calls the scheduler on each idle pass |
| `tools/run_retention.py` | Run or preview deletion by hand |
| `tools/export_db.py` | Create, verify, check and prune backups |
| `tests/test_db_retention.py` | Confirms the rules and their safety limits |
| `tests/test_retention_scheduler.py` | Confirms when deletion is and is not allowed to run |
| `data/live/retention_state.json` | Records the date deletion last ran |
| `.vscode/tasks.json` | Lets the backup be launched from inside VS Code |

---

## Things that would silently break this

These are the failure modes that produce no error message.

**The backup stops running and nobody notices.** Retention will keep deleting on
schedule. The only warning is `--check` reporting `STALE`, and only if you run
it. This is the most likely way to lose data.

**Scanning is switched back to the dashboard.** Retention is attached to the
Render worker's loop, below the check that decides whether this worker should be
scanning. If `SCAN_ENGINE_OWNER` is changed away from `worker`, the worker parks
itself and retention stops running with it. The database resumes growing with no
indication that anything changed.

**`DB_WRITE_ENABLED` is not set on the worker.** Deleting rows counts as writing.
If this variable is missing or set to false, retention exits immediately and
records a line in the log. Everything else continues to work normally.

**A retention limit is shortened below what the replay tools need.** Reducing
`scanner_snapshot` below roughly 14 calendar days leaves the historical
comparison with too little data. It will still run and still produce a result.
A test guards the built-in default, but an environment variable override is not
covered by that test.

---

*Last updated: 2 August 2026*
