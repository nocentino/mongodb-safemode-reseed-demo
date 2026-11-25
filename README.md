# MongoDB reseed and SafeMode demo

Four live demos, built on the lab and the helpers behind the
[MongoDB on Everpure](https://www.nocentino.com/posts/2026-11-17-mongodb-on-everpure-why-it-matters/) blog series. Each numbered
file is meant to be highlighted and run one block at a time (Shift+Enter in VS Code sends the selection to the Python terminal).

| File | What it shows | Time in my lab (2026-11-25 runs) |
|---|---|---|
| `0-prep.py` | Run first. A baseline replicated snapshot to every target, and the SafeMode protection group | baseline (2026-11-24): about 4 minutes; SafeMode group: already built |
| `1-reseed-within-site.py` | Rebuild `aen-mongo-06` on flasharray02 from a replicated snapshot of the primary, under load, no fsyncLock | 2 minutes (125 s). 125.3 MiB crossed the link, SECONDARY 1 s after mongod started; 4 members match, no gaps |
| `2-reseed-across-sites.py` | The same for `aen-mongo-04` on flasharray04, standing in for a second site | 2 minutes (117 s). 32.5 MiB crossed the link, SECONDARY 1 s after mongod started; 4 members match, no gaps |
| `3-safemode.py` | Delete a SafeMode snapshot (soft delete only), try to eradicate it (blocked), recover it | 5 seconds |
| `4-sharded-prep.py` | Run before demo 4. Makes flasharray03 a replication target of flasharray01's sharded group, and a baseline replicated snapshot | first baseline (2026-11-25): 12 minutes, 2,134.7 GiB crossed the link |
| `5-reseed-shard-member.py` | Demo 4. Rebuild `aen-mongo-sh1-03` from a replicated snapshot of `sh1`'s primary, under writes through `mongos`, overwriting only its 3 of the snapshot's 9 volumes | 2.5 minutes (159 s). 268.3 MiB crossed the link, SECONDARY 3 s after mongod started; `sh1`'s 3 members match, no gaps; 5 of 5 runs passed |
| `9-reset.py` | When you are done. Destroys the demo reseed snapshots and their copies; keeps the baseline and never touches SafeMode | under a minute |

`helpers.py` is plumbing: SSH, `mongosh`, mount and unmount, waits, and printing. `sharded.py` is the same for the sharded
cluster, with the same function names, so demo 4 reads block for block like demos 1 and 2. The array calls that matter stay
in the numbered files, in the open.

### What the output looks like

Every helper prints one line per step, starting with the host, replica set, or array it's about, so you can follow the demo
by reading down the left edge. The middle of demo 1, for example:

```
aen-mongo-06: stopping mongod (a clean shutdown)
aen-mongo-06: mongod stopped after 17 seconds
aen-mongo-06: unmounted /u01/data (/dev/sdd)
aen-mongo-06: unmounted /u01/journal (/dev/sdc)
aen-mongo-06: unmounted /u01/oplog (/dev/sdg)
aen-mongo-06: /u01/log stays mounted, so the member's own log survives the overwrite
aen-mongo-06: overwrote aen-mongo-multi-06-data from flasharray01:aen-mongo-pg-multi.seed06-20261125-133602.aen-mongo-multi-05-data
...
aen-mongo-06: mounted aen-mongo-multi-06-data on /u01/data (/dev/sdd, by its wwn, serial 81F096D1C1642A690330AFB7)
...
aen-mongo-06: starting mongod --replSet rs-multi --dbpath /u01/data/rs-multi --fork
aen-mongo-06: mongod is up, pid 206106, 2 seconds after the start (crash recovery from the copy happens before this)
aen-mongo-06: SECONDARY, 1 second after mongod started
aen-mongo-06: 2026-11-25 19:36:47 UTC   No initial sync required. Attempting to begin steady replication
```

The overwrite loop in demos 1, 2 and 4 checks that each volume is unmounted before it overwrites it, so a skipped `detach`
stops the demo instead of overwriting a running member's volumes.

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

### The sharded cluster (demo 4)

The cluster from the sharded posts in the series: shards `sh1` and `sh2` and the config server set `cfg`, one member of each
per array on flasharray01 to 03, and `mongos` on `aen-mongo-router-01`. It holds 10 TiB in `bulk` (5 TiB per shard). On each
array, `aen-mongo-pg-sharded` holds that array's three `mongod`s, so one snapshot carries a member of `sh1`, a member of `sh2`
and a config server.

```
       flasharray01                 flasharray02                 flasharray03
   aen-mongo-sh1-01 (P) ──┐     aen-mongo-sh1-02             aen-mongo-sh1-03  <- rebuilt
   aen-mongo-sh2-01       │     aen-mongo-sh2-02 (P)         aen-mongo-sh2-03
   aen-mongo-cfg-01       │     aen-mongo-cfg-02             aen-mongo-cfg-03 (P)
                          └──── replicated to flasharray03 ───────┘
```

A member can only be seeded from its own shard, so flasharray01's group replicates to flasharray03. The demo overwrites only
`aen-mongo-sh1-03`'s data, journal, and oplog, from `aen-mongo-sh1-01`'s copies in the snapshot; the copies of
`aen-mongo-sh2-01` and `aen-mongo-cfg-01` aren't touched. The balancer is on, and the demo doesn't change it.

## Before you run the demos

1. `pip install -r requirements.txt` into `.venv` (done), and open the folder in VS Code so `.vscode/settings.json` picks up the venv.
2. Credentials come from `FA_ENDPOINT`, `FA_USERNAME`, and `FA_PASSWORD`, or from the file in `DEMO_ENV`
   (default `~/Documents/GitHub/mongodb-flasharray-backup-python/.env`). Nothing prints them.
3. Run `0-prep.py`'s baseline section the day you run the demos, so demos 1 and 2 only move what changed since then.
   Once the new baseline lands on all three targets, it destroys the older baselines (destroy, not eradicate), so
   reruns don't pile up. The SafeMode section is safe to run too: the group already exists, so each block skips itself.
4. Check every member is healthy: `from helpers import *; members()`.
5. For demo 4, run `4-sharded-prep.py` the same day, so the demo only moves what changed since then, and check the sharded
   cluster: `from sharded import *; members()`.

## SafeMode, and what it really does

`aen-mongo-pg-safemode` on flasharray01 holds clones of the primary's data, journal, and oplog, made from the baseline snapshot
(no data copied). It keeps every snapshot for 24 hours, and its retention lock is **ratcheted**. That's permanent: only
Everpure Technical Services can set it back to unlocked. Tested on 2026-11-24 (demo 3 now stops after the recover step;
the last three rows come from that day's tests):

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
