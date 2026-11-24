# Handoff: MongoDB reseed and SafeMode demo

For the next agent picking this up. Read this, `README.md`, and `helpers.py` before touching the lab.

## What this is

A standalone demo of three things on FlashArray: rebuilding a MongoDB replica set member from a replicated snapshot of
the primary within a site, doing the same across sites where only changed blocks move, and SafeMode making those
snapshots immutable. It builds on Anthony's post
[Replacing the MongoDB Replica Set Initial Sync with FlashArray Snapshots](https://www.nocentino.com/posts/2026-11-17-mongodb-reseed-replica-between-flasharrays/).

The repo (`github.com/nocentino/mongodb-safemode-reseed-demo`, private) holds the demos in Python, run block by block in
VS Code, in the style of Anthony's numbered demo scripts.

## Rules Anthony set

- **Don't commit or push without his OK**, in any repo.
- **No em or en dashes** in anything on screen or published. Write in his voice (`~/Documents/GitHub/nocentino-static/anthony-style.md`).
- **Array names on screen are flasharray01 to 04** (`helpers.alias`). Real names live only in `helpers.ARRAYS`.
- **Never print secrets** from `.env` or `FA_Cred.xml`. Touch only `aen-mongo-*` objects. Destroy, never eradicate, without his OK.

## Lab state (2026-11-25)

- `rs-multi`: four members, 10 TiB (1,342,177,280 documents, 5.29 TiB on disk each), MongoDB 9.0.8, all healthy.
  `aen-mongo-05` (flasharray01, primary), `-06` (flasharray02), `-07` (flasharray03), `-04` (flasharray04, "site B").
  All four arrays are in one data center; flasharray04 stands in for a second site, and the demo says so.
- `aen-mongo-pg-multi` on flasharray01 replicates to flasharray02, 03, 04. Its retention applies only to scheduled
  snapshots and both schedules are off, so manual snapshots persist until destroyed.
- **Baseline:** `aen-mongo-pg-multi.baseline-20261124-134309` and its copies on every target. Always keep the newest
  baseline: it's why the demo transfers are megabytes, not terabytes. Rerun `0-prep.py`'s baseline section the day you run
  the demos. It waits for the new one to land on flasharray02, 03 and 04, then destroys (not eradicates) the older ones.
  The SafeMode clones were made from the first baseline but don't depend on it; a clone is an independent volume.
- **Demo snapshots:** `seed04-20261124-143304`, `seed06-20261124-145151` and `seed04-20261124-145741` are live from
  Anthony's runs. `9-reset.py` destroys them and their copies.
- **SafeMode (permanent):** `aen-mongo-pg-safemode` on flasharray01, retention lock **ratcheted** (Anthony approved),
  every snapshot kept 24 hours. It holds `aen-mongo-safemode-{data,journal,oplog}`, clones of the primary's volumes from
  the first baseline. Only Everpure Technical Services can unlock it. Its snapshots expire on their own 24 hours after
  they're taken; two destroyed test snapshots (`test0-…`, `test1-…`) eradicate on their own about 2026-12-02.
- flasharray04 also has a 10-minute snapshot schedule on `aen-mongo-04`'s volumes (`aen-mongo-multi-04-snap-10min`). It
  isn't part of this demo; leave it alone.

## What the tests showed (2026-11-24)

| Demo | Result |
|---|---|
| 1 within a site | 4.9 MiB crossed to flasharray02; `aen-mongo-06` SECONDARY 5 s after start; 4 members match (count, dbHash, no gaps); 2.5 min |
| 2 across sites | 46.7 MiB crossed to flasharray04; `aen-mongo-04` SECONDARY 5 s after start; all match; 2 min |
| 3 SafeMode | delete = soft destroy with 192 h to auto-eradication; eradicate blocked ("Eradication is disabled."); recover works; shorten retention / unlock / remove volume = HTTP 403; destroy group = "Invalid operation on protected objects."; 16 TiB restore volume in 0.25 s |

**SafeMode nuance to keep straight:** deleting a snapshot is *allowed* (it becomes destroyed and recoverable); *erasing* it
early is what's blocked. Anthony first asked for "trying to delete the snap and have that deletion be blocked"; the demo
was adjusted to the real behavior ("can't erase early", "a delete starts an 8-day clock nobody can shorten").

## Gotchas already hit

- py-pure-client models raise `AttributeError` for unset fields: use `getattr(x, "completed", None)`.
- Through the gateway, look up by `filter="name='…'"`, not `names=`, when passing `context_names`. `get_audits` refuses a
  custom `sort` with `context_names`; filter on `time>…` instead.
- `mongosh` renames its own process, so `pgrep -f` can't find the writers; `stop_load()` just waits 5 s.
- SafeMode refusals come back as HTTP 403 with an empty message; the demo prints the status code.
- **Sending blocks to the terminal.** The venv is Python 3.14. VS Code's Shift+Enter (Python extension, "Run Selection")
  queued selections and never sent them in Anthony's setup. He uses another shortcut instead, and line 9 of
  `1-reseed-within-site.py` is the venv's Python path, there on purpose to start the REPL in the integrated terminal.
  Don't set `PYTHON_BASIC_REPL=1`: the classic REPL prints the extension's bracketed paste codes as text and runs each
  statement of a block separately.
- **A dropped block once let an overwrite hit live volumes.** On 2026-11-24 `detach(TARGET)` silently didn't run, and the
  overwrite replaced `aen-mongo-04`'s volumes under a running, mounted `mongod`. It was fixed by detaching, overwriting
  again from the same replicated snapshot, and reattaching (all members matched after). The overwrite loop in demos 1 and
  2 now asserts each volume is unmounted first, inside the loop so it holds in any REPL.

## Before you run the demos

1. Rerun the `0-prep.py` baseline, check `members()`, and do one dry run of each demo.
2. When you're done, run `9-reset.py`.
