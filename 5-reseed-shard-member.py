# DEMO 4: RESEED A SHARD MEMBER FROM A REPLICATED SNAPSHOT OF ITS PRIMARY
#
# A shard is a replica set, so the same move works in a sharded cluster. We rebuild aen-mongo-sh1-03, a secondary of
# shard sh1 on flasharray03, from a replicated snapshot of aen-mongo-sh1-01, sh1's primary on flasharray01, while
# writes keep flowing through mongos. The twist: each array's snapshot carries three mongods (a member of sh1, a member
# of sh2 and a config server), and we overwrite only the rebuilt member's three volumes. No fsyncLock, no initial sync,
# and only changed blocks cross the link.
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
    print(f"{m.member.name:28} {'<- seeds ' + TARGET if m.member.name.startswith(SOURCE) else ''}")


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
print(snap)


# Only changed blocks cross the link: what changed on flasharray01 since the last replicated snapshot.
xfer = wait_for_transfer(fa, snap, TARGET_ARRAY)


# ===============================================
# OVERWRITE THE MEMBER'S VOLUMES FROM THE PRIMARY'S COPY
# ===============================================
# Stop aen-mongo-sh1-03 and unmount its data, journal and oplog. sh1 keeps a primary and a secondary, so it keeps its
# majority and writes through mongos never stop.
since = utc_now(TARGET)
detach(TARGET)


# Overwrite each of TARGET's volumes from SOURCE's volume in the replicated snapshot. Three volumes out of the nine in the
# snapshot; the copies of aen-mongo-sh2-01 and aen-mongo-cfg-01 aren't touched. The device and its serial don't change.
for role in ROLES:
    assert not ssh(TARGET, f"findmnt -n /u01/{role}", allow_fail=True), f"{TARGET} still has /u01/{role} mounted: run detach first"
    source = f"{ARRAYS[SOURCE_ARRAY]}:{snap}.{vol(SOURCE, role)}"
    check(fa.post_volumes(names=[vol(TARGET, role)], context_names=[ARRAYS[TARGET_ARRAY]], overwrite=True,
                          volume=VolumePost(source=Reference(name=source))), f"overwrite {role}")
    print(f"overwrote {vol(TARGET, role)} from {alias(source)}")


# Mount the volumes and start mongod with its usual options, as a member of sh1.
attach(fa, TARGET)
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
wait_until_caught_up("sh1")
members()
compare_members("sh1")
gaps(RUN)
metadata()
