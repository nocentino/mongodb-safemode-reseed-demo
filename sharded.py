"""Plumbing for the sharded demo (4-sharded-prep.py, 5-reseed-shard-member.py): the same helper names as helpers.py,
pointed at the sharded cluster, so the demo reads block for block like demos 1 and 2.

The cluster is the one from the sharded posts in the MongoDB on Everpure series: shards sh1 and sh2 and the config server
set cfg, one member of each per array on flasharray01 to 03, and mongos on aen-mongo-router-01. aen-mongo-pg-sharded on
each array holds that array's three mongods (data, journal, oplog; never the log), so one snapshot carries three members.
"""
# ssh, mongosh, alias, show, connect, check, suffix, direct, utc_now, transitions and wait_for_transfer come from
# helpers.py as they are. The cluster and member functions below replace helpers.py's rs-multi versions.
from helpers import *

# host: (array, replica set). One member of each set per array.
LAYOUT = {
    "aen-mongo-sh1-01": ("flasharray01", "sh1"), "aen-mongo-sh2-01": ("flasharray01", "sh2"), "aen-mongo-cfg-01": ("flasharray01", "cfg"),
    "aen-mongo-sh1-02": ("flasharray02", "sh1"), "aen-mongo-sh2-02": ("flasharray02", "sh2"), "aen-mongo-cfg-02": ("flasharray02", "cfg"),
    "aen-mongo-sh1-03": ("flasharray03", "sh1"), "aen-mongo-sh2-03": ("flasharray03", "sh2"), "aen-mongo-cfg-03": ("flasharray03", "cfg"),
}
SETS = {rs: [h for h, (_, r) in LAYOUT.items() if r == rs] for rs in ("sh1", "sh2", "cfg")}
SHARDS = ["sh1", "sh2"]
ROUTER = "aen-mongo-router-01"
MONGOS = f"mongodb://{ROUTER}.{DOMAIN}:27017"
PG = "aen-mongo-pg-sharded"
WRITERS = 4


def vol(node, role):
    return f"{node}-{role}"


def dbpath(node):
    return f"/u01/data/{LAYOUT[node][1]}"


# ---------- the cluster ----------

def primary(rs):
    """The set's primary, as its members see it. Only a host name counts: a down member's error can end in :27017 too."""
    for node in SETS[rs]:
        p = mongosh(node, "print(db.hello().primary || '')", uri=direct(node), allow_fail=True)
        if p.startswith("aen-mongo-") and p.endswith(f".{DOMAIN}:27017"):
            return p.split(".")[0]
    return None


def members(sets=("sh1", "sh2", "cfg")):
    """Every member's state, as its set's primary sees it."""
    for rs in sets:
        p = primary(rs)
        line = mongosh(p, "print(rs.status().members.map(m => m.name.split('.')[0] + ' ' + m.stateStr).join(', '))",
                       uri=direct(p)) if p else "no primary"
        print(f"{rs:4} {line}")


def state(node):
    p = primary(LAYOUT[node][1])
    if not p:
        return None
    return mongosh(p, f'print(rs.status().members.filter(m => m.name.startsWith("{node}"))[0].stateStr)',
                   uri=direct(p), allow_fail=True)


def wait_for_state(node, want="SECONDARY", timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if state(node) == want:
            print(f"{node} is {want} {time.time() - t0:.0f} seconds after it started")
            return True
        time.sleep(2)
    print(f"{node} didn't reach {want} in {timeout} seconds; it's {state(node)}")
    return False


def data_size():
    """What the cluster holds, through mongos, and how the demo collection splits across the shards."""
    out = mongosh(ROUTER, """const b = db.getSiblingDB('bulk').stats(), d = db.getSiblingDB('admin').aggregate([
  { $shardedDataDistribution: {} }, { $match: { ns: 'demo.orders' } }]).toArray()[0];
print((b.dataSize / 2**40).toFixed(2) + ' ' + b.objects + ' ' + d.shards.map(s => s.shardName + '=' + s.numOwnedDocuments).join(','))""",
                  uri=MONGOS).split()
    print(f"bulk database: {out[0]} TiB of data, {int(out[1]):,} documents, both shards; "
          f"demo.orders owned per shard: {out[2].replace(',', ', ')}")


# ---------- one member ----------

def start(node):
    """Start mongod with the member's usual options, printing each step. The journal and local database live on
    their own volumes."""
    rs, d = LAYOUT[node][1], dbpath(node)
    role = "--configsvr" if rs == "cfg" else "--shardsvr"
    ssh(node, f"sudo runuser -u mongod -- mkdir -p {d} && "
              f"( [ -L {d}/journal ] || sudo runuser -u mongod -- ln -s /u01/journal {d}/journal ) && "
              f"( [ -L {d}/local ] || sudo runuser -u mongod -- ln -s /u01/oplog {d}/local )")
    print(f"{node}: {d}/journal -> /u01/journal, {d}/local -> /u01/oplog")
    print(f"{node}: starting mongod --replSet {rs} {role} --dbpath {d} --fork")
    t0 = time.time()
    out = ssh(node, f"sudo runuser -u mongod -- {BIN}/mongod --replSet {rs} {role} --port 27017 --bind_ip_all --dbpath {d} "
                    f"--directoryperdb --logpath {LOG} --logappend --oplogSize 990 --wiredTigerCacheSizeGB 2 --fork")
    pid = next((l.split()[-1] for l in out.splitlines() if l.startswith("forked process")), "unknown")
    print(f"{node}: mongod is up, pid {pid}, {time.time() - t0:.0f} seconds after the start "
          f"(crash recovery from the copy happens before this)")


def stop(node):
    """Clean shutdown, then wait until mongod has really exited. A healthy secondary spends about 15 s in quiesce."""
    ssh(node, f"sudo runuser -u mongod -- {BIN}/mongod --shutdown --dbpath {dbpath(node)}", allow_fail=True)
    while ssh(node, "pgrep -x mongod", allow_fail=True):
        time.sleep(2)


def detach(node):
    """Stop the member and unmount its data, journal and oplog, printing each step. The log volume stays mounted."""
    print(f"{node}: stopping mongod (a clean shutdown; a healthy secondary spends about 15 s in quiesce)")
    t0 = time.time()
    stop(node)
    print(f"{node}: mongod stopped after {time.time() - t0:.0f} seconds")
    for role in ROLES:
        dev = ssh(node, f"findmnt -n -o SOURCE /u01/{role}", allow_fail=True)
        if dev:
            ssh(node, f"sudo umount /u01/{role}")
            print(f"{node}: unmounted /u01/{role} ({dev})")
        else:
            print(f"{node}: /u01/{role} was already unmounted")
    print(f"{node}: /u01/log stays mounted, so the member's own log survives the overwrite")


def attach(fa, node):
    """Mount the member's volumes by device path. A copy of another member carries that member's file system UUIDs,
    so fstab won't match until you update it."""
    array = ARRAYS[LAYOUT[node][0]]
    for role in ROLES:
        v = list(check(fa.get_volumes(filter=f"name='{vol(node, role)}'", context_names=[array])).items)[0]
        wwn = f"/dev/disk/by-id/wwn-0x624a9370{v.serial.lower()}"
        dev = ssh(node, f"sudo mount {wwn} /u01/{role} && readlink -f {wwn}")
        print(f"{node}: mounted {v.name} on /u01/{role} ({dev}, by its wwn, serial {v.serial})")
    print(f"{node}: data, journal and oplog mounted")


# ---------- load through mongos, and proof ----------

def start_load(run):
    """Four writers insert numbered documents into demo.orders through mongos until stop_load(). The collection is
    sharded on a hashed _id, so every writer lands on both shards. _ids look like <run>-w<writer>-<seq>."""
    ssh(ROUTER, "rm -f /tmp/stop-shard-load")
    for w in range(WRITERS):
        js = f"""const c = db.getSiblingDB('demo').orders; let s = 0;
while (true) {{ c.insertOne({{ _id: '{run}-w{w}-' + s, run: '{run}', w: {w}, seq: s }}); s++;
  if (s % 100 === 0 && require('fs').existsSync('/tmp/stop-shard-load')) break; }}"""
        b64 = base64.b64encode(js.encode()).decode()
        ssh(ROUTER, f"nohup bash -c 'mongosh --quiet \"{MONGOS}\" --eval \"$(echo {b64} | base64 -d)\"' "
                    f"</dev/null >/tmp/shard-writer{w}.log 2>&1 &")
    time.sleep(5)
    print(f"load running through mongos on {ROUTER}: {ops_per_second()} inserts a second, run {run}")


def stop_load():
    ssh(ROUTER, "touch /tmp/stop-shard-load")
    time.sleep(5)   # each writer checks the stop file every 100 inserts
    print("load stopped")


def ops_per_second():
    js = "const o = db.serverStatus().opcounters; print(Number(o.insert))"
    a = int(mongosh(ROUTER, js, uri=MONGOS))
    time.sleep(5)
    return (int(mongosh(ROUTER, js, uri=MONGOS)) - a) // 5


def wait_until_caught_up(rs="sh1", timeout=300):
    """Wait until every member of a set has applied the same last operation."""
    js = "const t = rs.status().members.map(m => m.optime.ts.t + '.' + m.optime.ts.i); print(new Set(t).size === 1 ? 'same' : 'diff')"
    t0 = time.time()
    while time.time() - t0 < timeout:
        p = primary(rs)
        if p and mongosh(p, js, uri=direct(p), allow_fail=True) == "same":
            print(f"every member of {rs} is on the same last operation")
            return True
        time.sleep(2)
    print(f"members of {rs} are NOT on the same last operation")
    return False


def compare_members(rs="sh1"):
    """The demo database's document count and dbHash on every member of one shard, read on that member. A dbHash of demo
    takes about 50 s per member in my lab, so the three run at once."""
    from concurrent.futures import ThreadPoolExecutor
    js = """print(db.getSiblingDB('demo').orders.countDocuments({}) + ' ' + db.getSiblingDB('demo').runCommand({ dbHash: 1 }).md5)"""
    with ThreadPoolExecutor(len(SETS[rs])) as pool:
        results = list(pool.map(lambda n: mongosh(n, js, uri=direct(n)).split(), SETS[rs]))
    for n, (c, h) in zip(SETS[rs], results):
        print(f"{rs}  {n}  demo.orders documents: {int(c):>9,}   demo dbHash: {h[:12]}")


def gaps(run):
    """Every writer numbers its documents from 0, so n documents has to top out at n - 1. Counted through mongos."""
    print(mongosh(ROUTER, f"""db.getSiblingDB('demo').orders.aggregate([
  {{ $match: {{ _id: {{ $gte: '{run}-', $lt: '{run}.' }} }} }},
  {{ $group: {{ _id: '$w', n: {{ $sum: 1 }}, max: {{ $max: '$seq' }} }} }}, {{ $sort: {{ _id: 1 }} }}])
  .forEach(r => print('writer ' + r._id + ': ' + r.n + ' documents, highest seq ' + r.max + (r.n === r.max + 1 ? ', no gaps' : ', GAPS')))""",
                  uri=MONGOS))


def metadata():
    """MongoDB's own check of the cluster's routing metadata, through mongos."""
    n = mongosh(ROUTER, "print(db.getSiblingDB('admin').checkMetadataConsistency().toArray().length)", uri=MONGOS)
    print("checkMetadataConsistency through mongos:", "no inconsistencies" if n == "0" else f"{n} INCONSISTENCIES")
