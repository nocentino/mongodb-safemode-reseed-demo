# DEMO 4: RESEED A SHARD MEMBER FROM A REPLICATED SNAPSHOT OF ITS PRIMARY
#
# A shard is a replica set, so the same move works in a sharded cluster. We rebuild aen-mongo-sh1-03, a secondary of
# shard sh1 on flasharray03, from a replicated snapshot of aen-mongo-sh1-01, sh1's primary on flasharray01, while
# writes keep flowing through mongos. The twist: each array's snapshot carries three mongods (a member of sh1, a member
# of sh2 and a config server), and we overwrite only the rebuilt member's three volumes. No fsyncLock, no initial sync,
# and only changed blocks cross the link.
#
# Blog post: Reseeding a MongoDB Shard Member from a FlashArray Snapshot
#   https://www.nocentino.com/posts/2026-12-09-mongodb-reseed-shard-member-flasharray-snapshot/
#
# ARCHITECTURE: shards sh1 and sh2 and the config server set cfg, one member of each per array. On each array,
# aen-mongo-pg-sharded holds that array's three mongods (data, journal and oplog; never the log).
#
#                                 4 writers
#                                     │
#   ┌─ SITE A (one data center) ──────┼────────────────────────────────────────────────────────────────┐
#   │                                 ▼                                                                │
#   │                     mongos on aen-mongo-router-01 routes each write to its shard's primary       │
#   │                       │ sh1                            │ sh2                                     │
#   │                       ▼                                ▼                                         │
#   │ ┌─ flasharray01 ──────┴──────┐   ┌─ flasharray02 ──────┴──────┐   ┌─ flasharray03 ─────────────┐ │
#   │ │ aen-mongo-sh1-01 (P)  seed │   │ aen-mongo-sh1-02           │   │ aen-mongo-sh1-03 <- rebuilt│ │
#   │ │ aen-mongo-sh2-01           │   │ aen-mongo-sh2-02 (P)       │   │ aen-mongo-sh2-03           │ │
#   │ │ aen-mongo-cfg-01           │   │ aen-mongo-cfg-02           │   │ aen-mongo-cfg-03 (P)       │ │
#   │ │ 1. snapshot all three      │   │ keeps running              │   │ 3. only sh1-03's volumes   │ │
#   │ └──────────────┬─────────────┘   └────────────────────────────┘   └──────────────▲─────────────┘ │
#   │                │                                                                 │               │
#   │                └── 2. replicate_now to flasharray03: changed blocks only ────────┘               │
#   └──────────────────────────────────────────────────────────────────────────────────────────────────┘
#
#   1. One snapshot call on flasharray01: all three of its mongods, 9 volumes, at the same instant, no fsyncLock.
#   2. replicate_now: only the blocks that changed since the last copy cross to flasharray03.
#   3. Stop aen-mongo-sh1-03 and overwrite only its data, journal and oplog from aen-mongo-sh1-01's copies.
#      The copies of aen-mongo-sh2-01 and aen-mongo-cfg-01 sit unused. Mount, start: SECONDARY, no initial sync.
#
# Run 4-sharded-prep.py first. Highlight a block and run it.

/Users/anocentino/Documents/GitHub/mongodb-safemode-reseed-demo/.venv/bin/python

# ===============================================
# CONNECT AND LOOK AT THE SHARDED CLUSTER
# ===============================================
from sharded import *
from pypureclient.flasharray import ProtectionGroupSnapshotPost, Reference, VolumePost
fa = connect()
TARGET, TARGET_ARRAY = "aen-mongo-sh1-03", "flasharray03"
SOURCE, SOURCE_ARRAY = "aen-mongo-sh1-01", "flasharray01"
members()
data_size()


# flasharray01's group holds three mongods. Only SOURCE's volumes will seed TARGET; the other two stay unused.
for m in fa.get_protection_groups_volumes(group_names=[PG], context_names=[ARRAYS[SOURCE_ARRAY]]).items:
    print(f"{SOURCE_ARRAY}: {m.member.name:28} {'<- seeds ' + TARGET if m.member.name.startswith(SOURCE) else 'not used'}")


# ===============================================
# PUT THE CLUSTER UNDER LOAD
# ===============================================
# Four writers insert numbered documents through mongos for the rest of the demo. demo.orders is sharded on a hashed
# _id, so the writes land on both shards.
RUN = suffix("sh1")
start_load(RUN)


# ===============================================
# SNAPSHOT FLASHARRAY01'S GROUP AND REPLICATE IT NOW
# ===============================================
# One call, no fsyncLock. The protection group captures the primary's data, journal and oplog at the same instant, so
# the copy is crash-consistent. replicate_now starts the transfer to flasharray03 right away.
snap = fa.post_protection_group_snapshots(source_names=[PG], context_names=[ARRAYS[SOURCE_ARRAY]], replicate_now=True,
                                          protection_group_snapshot=ProtectionGroupSnapshotPost(suffix=suffix("seedsh1")))
snap = list(check(snap, "snapshot").items)[0].name
print(f"{SOURCE_ARRAY}: took {snap}, replicating it now")


# Only changed blocks cross the link: what changed on flasharray01 since the last replicated snapshot.
xfer = wait_for_transfer(fa, snap, TARGET_ARRAY)


# ===============================================
# OVERWRITE THE MEMBER'S VOLUMES FROM THE PRIMARY'S COPY
# ===============================================
# Stop aen-mongo-sh1-03 and unmount its data, journal and oplog. 
# sh1 keeps a primary and a secondary, so it keeps its
# majority and writes through mongos never stop.
since = utc_now(TARGET)


# Detach TARGET's volumes from the host so they can be safely overwritten.
detach(TARGET)


# Overwrite each of TARGET's volumes from SOURCE's volume in the replicated snapshot. Three volumes out of the nine in the
# snapshot; the copies of aen-mongo-sh2-01 and aen-mongo-cfg-01 aren't touched. The device and its serial don't change.
for role in ROLES:
    assert not ssh(TARGET, f"findmnt -n /u01/{role}", allow_fail=True), f"{TARGET} still has /u01/{role} mounted: run detach first"
    source = f"{ARRAYS[SOURCE_ARRAY]}:{snap}.{vol(SOURCE, role)}"
    check(fa.post_volumes(names=[vol(TARGET, role)], context_names=[ARRAYS[TARGET_ARRAY]], overwrite=True,
                          volume=VolumePost(source=Reference(name=source))), f"overwrite {role}")
    print(f"{TARGET}: overwrote {vol(TARGET, role)} from {alias(source)}")


# Mount the volumes and start mongod with its usual options, as a member of sh1.
attach(fa, TARGET)


# Start the mongod process on TARGET as a member of sh1.
start(TARGET)


# ===============================================
# WATCH IT REJOIN
# ===============================================
# No initial sync. It finds sh1's configuration and shard identity in the copy, then catches up from the primary's oplog.
wait_for_state(TARGET, "SECONDARY")
transitions(TARGET, since)


# ===============================================
# PROVE IT
# ===============================================
# Stop the load, wait for sh1's members to apply the same last operation, then compare them. sh2 was never touched.
stop_load()


# Wait until all members of sh1 have caught up with the primary before checking the replica set state.
wait_until_caught_up("sh1")


# Print the current state of all members of sh1.
members()


# Compare the members of sh1 to ensure they are consistent.
compare_members("sh1")



# Show any gaps in the replication for the run.
gaps(RUN)


# Show metadata for the run.
metadata()
