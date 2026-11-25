# DEMO 1: RESEED A REPLICA SET MEMBER FROM A SNAPSHOT OF THE PRIMARY, WITHIN A SITE
#
# rs-multi holds 10 TiB, one member per FlashArray. We rebuild aen-mongo-06 on flasharray02 from a replicated
# snapshot of the primary, aen-mongo-05 on flasharray01. No fsyncLock, no initial sync, and only the blocks that
# changed since the last replicated snapshot cross the link. Every other member keeps running the whole time.
#
# ARCHITECTURE: rs-multi, one member per FlashArray, each member's data, journal and oplog in aen-mongo-pg-multi on
# its own array (the log volume stays out). All four arrays are in one data center; flasharray04 stands in for site B.
#
#         4 writers
#                │
#   ┌─ SITE A ───┼─────────────────────────────────────────────────────────────┐   ┌─ SITE B ───────────────┐
#   │            ▼                                                             │   │                        │
#   │ flasharray01             flasharray02             flasharray03           │   │ flasharray04           │
#   │ ┌──────────┴─────────┐   ┌────────────────────┐   ┌────────────────────┐ │   │ ┌────────────────────┐ │
#   │ │ aen-mongo-05 (P)   │   │ aen-mongo-06       │   │ aen-mongo-07       │ │   │ │ aen-mongo-04       │ │
#   │ │ data journal oplog │   │ data journal oplog │   │ data journal oplog │ │   │ │ data journal oplog │ │
#   │ │ 1. snapshot here   │   │ <- 3. rebuilt      │   │ keeps running      │ │   │ │ keeps running      │ │
#   │ └──────────┬─────────┘   └──────────▲─────────┘   └────────────────────┘ │   │ └────────────────────┘ │
#   │            │                        │                                    │   │                        │
#   │            └─ 2. changed blocks ────┘                                    │   │                        │
#   └──────────────────────────────────────────────────────────────────────────┘   └────────────────────────┘
#
#   1. One snapshot call on flasharray01: the primary's data, journal and oplog at the same instant, no fsyncLock.
#   2. replicate_now: only the blocks that changed since the last replicated snapshot cross to flasharray02.
#   3. Stop aen-mongo-06, overwrite its three volumes (a metadata operation on the array), mount, start.
#      It rejoins as SECONDARY with no initial sync and catches up from the primary's oplog.
#
# Highlight a block and run it (Shift+Enter in VS Code sends it to the Python terminal).

/Users/anocentino/Documents/GitHub/mongodb-safemode-reseed-demo/.venv/bin/python

# ===============================================
# CONNECT AND LOOK AT THE REPLICA SET
# ===============================================
from helpers import *
from pypureclient.flasharray import ProtectionGroupSnapshotPost, Reference, VolumePost
fa = connect()
TARGET, TARGET_ARRAY = "aen-mongo-06", "flasharray02"
members()
data_size()


# Each member has its own data, journal and oplog volumes, in its own protection group on its own array.
for role in ROLES:
    print(f"{TARGET} /u01/{role:8} {vol(TARGET, role):28} on {TARGET_ARRAY}, in {PG}")


# ===============================================
# PUT THE PRIMARY UNDER LOAD
# ===============================================
# Four writers insert numbered documents through the primary for the rest of the demo.
start_load()


# ===============================================
# SNAPSHOT THE PRIMARY AND REPLICATE IT NOW
# ===============================================
# One call, no fsyncLock. The protection group captures the primary's data, journal and oplog at the same instant,
# so the copy is crash-consistent. replicate_now starts the transfer to every target array right away.
snap = fa.post_protection_group_snapshots(source_names=[PG], context_names=[ARRAYS["flasharray01"]], replicate_now=True,
                                          protection_group_snapshot=ProtectionGroupSnapshotPost(suffix=suffix("seed06")))
snap = list(check(snap, "snapshot").items)[0].name
print(snap)


# Only changed blocks cross the link. 10 TiB of data, and the transfer is what changed since the last copy.
xfer = wait_for_transfer(fa, snap, TARGET_ARRAY)


# ===============================================
# OVERWRITE THE MEMBER'S VOLUMES FROM THE PRIMARY'S COPY
# ===============================================
# Stop aen-mongo-06 and unmount its data, journal and oplog. The other three members carry on.
since = utc_now(TARGET)

detach(TARGET)


# Overwrite each volume from the primary's volume in the replicated snapshot on flasharray02. It's a metadata
# operation on the array, so 10 TiB takes the same time as 10 GB. The device and its serial don't change.
for role in ROLES:
    assert not ssh(TARGET, f"findmnt -n /u01/{role}", allow_fail=True), f"{TARGET} still has /u01/{role} mounted: run detach first"
    source = f"{ARRAYS['flasharray01']}:{snap}.{vol(PRIMARY, role)}"
    check(fa.post_volumes(names=[vol(TARGET, role)], context_names=[ARRAYS[TARGET_ARRAY]], overwrite=True,
                          volume=VolumePost(source=Reference(name=source))), f"overwrite {role}")
    print(f"overwrote {vol(TARGET, role)} from {alias(source)}")


# Mount the volumes
attach(fa, TARGET)

# Start mongod with its usual options.
start(TARGET)


# ===============================================
# WATCH IT REJOIN
# ===============================================
# No initial sync: crash recovery, then replication recovery from its own oplog,
wait_for_state(TARGET, "SECONDARY")


# Then it catches up from a sync source.
transitions(TARGET, since)


# ===============================================
# PROVE IT
# ===============================================
# Stop the load, wait for every member to apply the same last operation, then compare them.
stop_load()
wait_until_caught_up()
members()
compare_members()
gaps(TARGET)
