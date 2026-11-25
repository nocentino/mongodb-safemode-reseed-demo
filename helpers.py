"""Plumbing for the demo scripts: SSH, mongosh, mounting, waiting, and printing.

The array calls that matter (snapshot, replicate, overwrite, SafeMode) stay in the numbered demo files, in
the open, so they can be highlighted and run one block at a time. This file is the same set of helpers that
backs the MongoDB on Everpure blog series, trimmed to what the demos use.

Credentials: FA_ENDPOINT, FA_USERNAME and FA_PASSWORD come from the environment, or from the file in
DEMO_ENV (default ~/Documents/GitHub/mongodb-flasharray-backup-python/.env). Nothing here prints them.
"""
import base64
import os
import subprocess
import time
from datetime import datetime, timezone

from pypureclient import flasharray


def _load_env():
    path = os.environ.get("DEMO_ENV", os.path.expanduser("~/Documents/GitHub/mongodb-flasharray-backup-python/.env"))
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    if "FA_ENDPOINT" in os.environ:
        os.environ["FA_ENDPOINT"] = os.environ["FA_ENDPOINT"].removeprefix("https://")


_load_env()

DOMAIN = "fsa.lab"
BIN = os.environ.get("MONGO_BIN", "/opt/mongodb/9.0.8/bin")
MONGOSH = os.environ.get("MONGOSH_BIN", "/opt/mongosh/2.14.2/bin")

# The lab's arrays by the names the demo shows on screen. flasharray04 stands in for a second site.
ARRAYS = {"flasharray01": "sn1-x90r2-f07-27", "flasharray02": "sn1-x90r2-f06-27",
          "flasharray03": "sn1-x90r2-f06-33", "flasharray04": "sn1-c60-e12-16"}
_ALIAS = {real: name for name, real in ARRAYS.items()}

# rs-multi: one member per array. aen-mongo-05 is the primary and the seed source.
MEMBERS = {"aen-mongo-05": "flasharray01", "aen-mongo-06": "flasharray02",
           "aen-mongo-07": "flasharray03", "aen-mongo-04": "flasharray04"}
PRIMARY = "aen-mongo-05"
URI = "mongodb://" + ",".join(f"{n}.{DOMAIN}:27017" for n in MEMBERS) + "/?replicaSet=rs-multi"
DBPATH = "/u01/data/rs-multi"
LOG = "/u01/log/mongod.log"
PG = "aen-mongo-pg-multi"          # every member's data, journal and oplog, one group per array, never the log
ROLES = ["data", "journal", "oplog"]
WRITERS = 4


def alias(text):
    """Swap the lab's real array names for flasharray01..04 in anything we print."""
    text = str(text)
    for real, name in _ALIAS.items():
        text = text.replace(real, name)
    return text


def show(obj):
    print(alias(obj))


def connect():
    """One connection to the Fusion fleet's gateway array. context_names routes each call to an array."""
    return flasharray.Client(os.environ["FA_ENDPOINT"], username=os.environ["FA_USERNAME"],
                             password=os.environ["FA_PASSWORD"], verify_ssl=False)


def check(r, what="call"):
    """Array calls return an ErrorResponse instead of raising. Stop on one, and say what it was."""
    if isinstance(r, flasharray.ErrorResponse):
        raise RuntimeError(f"{what} failed: {alias([e.message for e in r.errors])}")
    return r


def secs(t):
    """A duration for printing: 1 second, 17 seconds."""
    n = round(t)
    return f"{n} second" + ("" if n == 1 else "s")


def suffix(word):
    """A snapshot suffix that's unique per run, like seed06-20261124-1502."""
    return f"{word}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def vol(node, role):
    return f"aen-mongo-multi-{node[-2:]}-{role}"


# ---------- hosts and MongoDB ----------

def ssh(node, command, allow_fail=False):
    """Run a bash command on a node, with mongod and mongosh on the PATH."""
    remote = f"export PATH={BIN}:{MONGOSH}:$PATH; {command}"
    for _ in range(3):   # the lab network sometimes drops a connection when many open at once
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
                            f"packer@{node}.{DOMAIN}", remote], capture_output=True, text=True)
        if r.returncode != 255:
            break
        time.sleep(3)
    if r.returncode != 0 and not allow_fail:
        raise RuntimeError(f"ssh {node} failed: {r.stdout}{r.stderr}")
    return (r.stdout + r.stderr).strip()


def mongosh(node, js, uri=None, allow_fail=False):
    """Run JavaScript through mongosh on a node. It travels base64 encoded so quoting never bites."""
    b64 = base64.b64encode(js.encode()).decode()
    target = f"'{uri}'" if uri else ""
    return ssh(node, f'mongosh --quiet {target} --eval "$(echo {b64} | base64 -d)"', allow_fail)


def direct(node):
    return f"mongodb://{node}.{DOMAIN}:27017/?directConnection=true"


def members():
    """Each member's state, as the primary sees it, one line per member."""
    out = mongosh(PRIMARY, "rs.status().members.forEach(m => print(m.name.split('.')[0] + ' ' + m.stateStr))", uri=URI)
    for line in out.splitlines():
        node, st = line.split(" ", 1)
        print(f"{node}: {st} on {MEMBERS.get(node, 'an unknown array')}")


def state(node):
    return mongosh(PRIMARY, f'print(rs.status().members.filter(m => m.name.startsWith("{node}"))[0].stateStr)',
                   uri=URI, allow_fail=True)


def wait_for_state(node, want="SECONDARY", timeout=600):
    """Poll the member's state as the primary sees it, print every change, and stop when it reaches want."""
    t0, last = time.time(), None
    while time.time() - t0 < timeout:
        s = state(node)
        if s != last:
            print(f"{node}: {s}, {secs(time.time() - t0)} after mongod started")
            last = s
        if s == want:
            return True
        time.sleep(2)
    print(f"{node}: didn't reach {want} in {timeout} seconds; it's {last}")
    return False


def data_size():
    """How much data the replica set holds: the bulk database's dataSize and what's on disk."""
    data, disk, docs = mongosh(PRIMARY, """const s = db.getSiblingDB('bulk').stats();
print(s.dataSize + ' ' + s.storageSize + ' ' + s.objects)""", uri=URI).split()
    print(f"rs-multi: the bulk database holds {int(data) / 2**40:.2f} TiB of data, {int(disk) / 2**40:.2f} TiB on disk "
          f"per member, {int(docs):,} documents")


def start(node):
    """Start mongod with the member's usual options, printing each step. The journal and local database live on
    their own volumes."""
    ssh(node, f"sudo runuser -u mongod -- mkdir -p {DBPATH} && "
              f"( [ -L {DBPATH}/journal ] || sudo runuser -u mongod -- ln -s /u01/journal {DBPATH}/journal ) && "
              f"( [ -L {DBPATH}/local ] || sudo runuser -u mongod -- ln -s /u01/oplog {DBPATH}/local )")
    print(f"{node}: {DBPATH}/journal -> /u01/journal, {DBPATH}/local -> /u01/oplog")
    print(f"{node}: starting mongod --replSet rs-multi --dbpath {DBPATH} --fork")
    t0 = time.time()
    out = ssh(node, f"sudo runuser -u mongod -- {BIN}/mongod --replSet rs-multi --bind_ip_all --dbpath {DBPATH} "
                    f"--directoryperdb --logpath {LOG} --logappend --oplogSize 990 --wiredTigerCacheSizeGB 2 --fork")
    pid = next((l.split()[-1] for l in out.splitlines() if l.startswith("forked process")), "unknown")
    print(f"{node}: mongod is up, pid {pid}, {secs(time.time() - t0)} after the start "
          f"(crash recovery from the copy happens before this)")


def stop(node):
    """Clean shutdown, then wait until mongod has really exited."""
    ssh(node, f"sudo runuser -u mongod -- {BIN}/mongod --shutdown --dbpath {DBPATH}", allow_fail=True)
    while ssh(node, "pgrep -x mongod", allow_fail=True):
        time.sleep(2)


def detach(node):
    """Stop the member and unmount its data, journal and oplog, printing each step. The log volume stays mounted."""
    print(f"{node}: stopping mongod (a clean shutdown)")
    t0 = time.time()
    stop(node)
    print(f"{node}: mongod stopped after {secs(time.time() - t0)}")
    for role in ROLES:
        dev = ssh(node, f"findmnt -n -o SOURCE /u01/{role}", allow_fail=True)
        if dev:
            ssh(node, f"sudo umount /u01/{role}")
            print(f"{node}: unmounted /u01/{role} ({dev})")
        else:
            print(f"{node}: /u01/{role} was already unmounted")
    print(f"{node}: /u01/log stays mounted, so the member's own log survives the overwrite")


def attach(fa, node):
    """Mount the member's volumes by device path. A copy of another member carries that member's file system
    UUIDs, so fstab won't match until you update it."""
    array = ARRAYS[MEMBERS[node]]
    for role in ROLES:
        v = list(check(fa.get_volumes(filter=f"name='{vol(node, role)}'", context_names=[array])).items)[0]
        wwn = f"/dev/disk/by-id/wwn-0x624a9370{v.serial.lower()}"
        dev = ssh(node, f"sudo mount {wwn} /u01/{role} && readlink -f {wwn}")
        print(f"{node}: mounted {v.name} on /u01/{role} ({dev}, by its wwn, serial {v.serial})")
    print(f"{node}: data, journal and oplog mounted")


def utc_now(node):
    return ssh(node, "date -u +%Y-%m-%dT%H:%M:%S")


def transitions(node, since):
    """The member's state changes since a point in time, from its log. The log volume isn't in the protection
    group, so a restore doesn't rewind it. These logs run to many GB, so it reads only the last 300 MB (about half an
    hour in my lab) unless `since` is older than that, and parses only the lines that can match."""
    py = f"""import json, os
since, path, window = {since!r}, {LOG!r}, 300_000_000
keys = (b'Replica set state transition', b'No initial sync required', b'Too stale', b'Starting rollback', b'Changed sync source')
f = open(path, 'rb')
if os.path.getsize(path) > window:
    f.seek(os.path.getsize(path) - window); f.readline(); start = f.tell()
    try: older = json.loads(f.readline())['t']['$date'][:19] > since
    except Exception: older = True
    f.seek(0 if older else start)
for raw in f:
    if not any(k in raw for k in keys): continue
    try: d = json.loads(raw)
    except Exception: continue
    m = d.get('msg', ''); t = d['t']['$date'][:19]
    if t < since: continue
    t = t.replace('T', ' ') + ' UTC'
    if m == 'Replica set state transition': print(t, ' ', d['attr']['oldState'] + ' -> ' + d['attr']['newState'])
    elif m.startswith('No initial sync required') or m.startswith('Too stale') or m.startswith('Starting rollback'): print(t, ' ', m[:80])
    elif m == 'Changed sync source': print(t, '  syncing from', d['attr']['newSyncSource'].split('.')[0])
"""
    b64 = base64.b64encode(py.encode()).decode()
    out = ssh(node, f'sudo python3 -c "$(echo {b64} | base64 -d)"', allow_fail=True)
    for line in out.splitlines() or [f"no state changes in its log since {since} UTC"]:
        print(f"{node}: {line}")


# ---------- load and proof ----------

def _background(js, log):
    b64 = base64.b64encode(js.encode()).decode()
    ssh(PRIMARY, f"nohup bash -c 'mongosh --quiet \"{URI}\" --eval \"$(echo {b64} | base64 -d)\"' </dev/null >{log} 2>&1 &")


def start_load():
    """Four writers that number their own documents, through the primary, until stop_load()."""
    ssh(PRIMARY, "rm -f /tmp/stop-load")
    mongosh(PRIMARY, "db.getSiblingDB('demo').load.drop()", uri=URI)
    print(f"{PRIMARY}: dropped demo.load for a fresh numbered run")
    for w in range(WRITERS):
        _background(f"""const c = db.getSiblingDB('demo').load; const pad = 'p'.repeat(200); let s = 0;
while (true) {{ c.insertOne({{ _id: 'w{w}-' + s, w: {w}, seq: s, pad: pad }}); s++;
  if (s % 100 === 0 && require('fs').existsSync('/tmp/stop-load')) break; }}""", f"/tmp/writer{w}.log")
    print(f"{PRIMARY}: started {WRITERS} writers through the primary, each numbering its own documents from 0")
    time.sleep(5)
    print(f"{PRIMARY}: {ops_per_second():,} inserts a second")


def stop_load():
    ssh(PRIMARY, "touch /tmp/stop-load")
    print(f"{PRIMARY}: told the writers to stop (each checks every 100 inserts)")
    time.sleep(5)
    print(f"{PRIMARY}: writers stopped, {ops_per_second():,} inserts a second now")


def ops_per_second():
    js = "const o = db.serverStatus().opcounters; print(Number(o.insert) + Number(o.update))"
    a = int(mongosh(PRIMARY, js, uri=direct(PRIMARY)))
    time.sleep(5)
    return (int(mongosh(PRIMARY, js, uri=direct(PRIMARY))) - a) // 5


def wait_until_caught_up(timeout=300):
    """Wait until every member has applied the same last operation."""
    js = "const t = rs.status().members.map(m => m.optime.ts.t + '.' + m.optime.ts.i); print(new Set(t).size === 1 ? 'same' : 'diff')"
    t0 = time.time()
    while time.time() - t0 < timeout:
        if mongosh(PRIMARY, js, uri=URI, allow_fail=True) == "same":
            print(f"rs-multi: every member is on the same last operation, {secs(time.time() - t0)} after the load stopped")
            return True
        time.sleep(2)
    print(f"rs-multi: members are NOT on the same last operation after {timeout} seconds")
    return False


def compare_members():
    """The demo database's document count and dbHash on every member, read on that member."""
    for n in MEMBERS:
        c = mongosh(n, "db.getMongo().setReadPref('secondaryPreferred'); print(db.getSiblingDB('demo').load.countDocuments())",
                    uri=direct(n))
        h = mongosh(n, "print(db.getSiblingDB('demo').runCommand({ dbHash: 1 }).md5)", uri=direct(n))
        print(f"{n}: {int(c):,} load documents, demo dbHash {h[:12]}")


def gaps(node):
    """Every writer numbers its documents from 0, so n documents has to top out at n - 1. Read on the member."""
    out = mongosh(node, """db.getMongo().setReadPref('secondaryPreferred');
db.getSiblingDB('demo').load.aggregate([{ $group: { _id: '$w', n: { $sum: 1 }, max: { $max: '$seq' } } }, { $sort: { _id: 1 } }])
  .forEach(r => print(r._id + ' ' + r.n + ' ' + r.max))""", uri=direct(node))
    for line in out.splitlines():
        w, n, top = (int(x) for x in line.split())
        print(f"{node}: writer {w}, {n:,} documents, highest seq {top:,}, {'no gaps' if n == top + 1 else 'GAPS'}")


# ---------- replication ----------

def gib(n):
    return f"{n / 2**30:,.1f} GiB" if n >= 2**30 else f"{n / 2**20:,.1f} MiB"


def wait_for_transfer(fa, snapshot, target, poll=5):
    """Poll the replicated copy of a snapshot on a target array until its transfer completes.
    The copy is named <source array>:<snapshot> on the target."""
    remote = f"{ARRAYS['flasharray01']}:{snapshot}"
    t0 = time.time()
    while True:
        r = fa.get_protection_group_snapshots_transfer(filter=f"name='{remote}'", context_names=[ARRAYS[target]])
        x = next(iter(getattr(r, "items", [])), None)   # unset fields raise AttributeError, hence getattr below
        if x is not None and getattr(x, "completed", None):
            print(f"{target}: {alias(remote)} landed in {secs(time.time() - t0)}, "
                  f"{gib(getattr(x, 'data_transferred', 0))} crossed the link, "
                  f"{gib(getattr(x, 'physical_bytes_written', 0))} written")
            return x
        if x is not None and getattr(x, "progress", None) is not None:
            print(f"{target}: {x.progress * 100:.0f}% after {secs(time.time() - t0)}", flush=True)
        time.sleep(poll)
