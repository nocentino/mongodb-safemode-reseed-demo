# MongoDB reseed and SafeMode demo

Three live demos, built on the lab and the helpers behind the
[MongoDB on Everpure](https://www.nocentino.com/posts/2026-11-17-mongodb-on-everpure-why-it-matters/) blog series. Each numbered
file is meant to be highlighted and run one block at a time (Shift+Enter in VS Code sends the selection to the Python terminal).

| File | What it shows | Time in my lab (2026-11-24) |
|---|---|---|
| `0-prep.py` | Run first. A baseline replicated snapshot to every target, and the SafeMode protection group | baseline: about 4 minutes; SafeMode group: already built |
| `1-reseed-within-site.py` | Rebuild `aen-mongo-06` on flasharray02 from a replicated snapshot of the primary, under load, no fsyncLock | 2.5 minutes. 4.9 MiB crossed the link, SECONDARY 5 s after start |
| `2-reseed-across-sites.py` | The same for `aen-mongo-04` on flasharray04, standing in for a second site | 2 minutes. 46.7 MiB crossed the link, SECONDARY 5 s after start |
| `3-safemode.py` | Delete a SafeMode snapshot (soft delete only), try to eradicate it (blocked), recover it, try to loosen the rules (blocked), restore instantly | 10 seconds. 16 TiB restore volume ready in 0.25 s |
| `9-reset.py` | When you are done. Destroys the demo reseed snapshots and their copies; keeps the baseline and never touches SafeMode | under a minute |

`helpers.py` is plumbing: SSH, `mongosh`, mount and unmount, waits, and printing. The array calls that matter stay in the
numbered files, in the open.

## The lab

`rs-multi` holds 10 TiB (1.34 billion documents, 5.29 TiB on disk per member), one member per FlashArray:

| Member | Array | Role in the demo |
|---|---|---|
| `aen-mongo-05` | flasharray01 | Primary and seed source. Its `aen-mongo-pg-multi` replicates to flasharray02, 03 and 04 |
| `aen-mongo-06` | flasharray02 | Rebuilt within a site (demo 1) |
| `aen-mongo-07` | flasharray03 | Stays up |
| `aen-mongo-04` | flasharray04 | Rebuilt "across sites" (demo 2). All four arrays are in one data center; flasharray04 stands in for site B |

Each member's data, journal, and oplog volumes are in `aen-mongo-pg-multi` on its own array; the log volume is not. Output
shows the arrays as flasharray01 to 04.

## Before you run the demos

1. `pip install -r requirements.txt` into `.venv` (done), and open the folder in VS Code so `.vscode/settings.json` picks up the venv.
2. Credentials come from `FA_ENDPOINT`, `FA_USERNAME`, and `FA_PASSWORD`, or from the file in `DEMO_ENV`
   (default `~/Documents/GitHub/mongodb-flasharray-backup-python/.env`). Nothing prints them.
3. Run `0-prep.py`'s baseline section the day you run the demos, so demos 1 and 2 only move what changed since then.
   Skip its SafeMode section: the group already exists, and the first block of that section says so.
4. Check every member is healthy: `from helpers import *; members()`.

## SafeMode, and what it really does

`aen-mongo-pg-safemode` on flasharray01 holds clones of the primary's data, journal, and oplog, made from the baseline snapshot
(no data copied). It keeps every snapshot for 24 hours, and its retention lock is **ratcheted**. That's permanent: only
Everpure Technical Services can set it back to unlocked. Tested on 2026-11-24:

| Action | Result |
|---|---|
| Destroy (delete) a snapshot | Allowed, but only as a soft delete: it's destroyed with 192 hours left before the array eradicates it on its own |
| Eradicate the destroyed snapshot | **Blocked:** `Eradication is disabled.` |
| Recover the destroyed snapshot | Allowed, one call |
| Shorten retention, unlock the lock, remove a volume | **Blocked:** HTTP 403 Forbidden |
| Destroy the protection group | **Blocked:** `Invalid operation on protected objects.` |
| New volume from the snapshot | 16 TiB volume ready in 0.25 s |

So the honest line: an admin, or someone holding stolen admin credentials, can't make a SafeMode snapshot go
away before its time. A delete only starts an 8-day clock that nobody can shorten, and the snapshot can be recovered the whole time.

## When you are done

Run `9-reset.py`. It leaves the baseline snapshot (the next run depends on it) and the SafeMode group in place.
