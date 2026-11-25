# DEMO 2: RESEED A REPLICA SET MEMBER ACROSS SITES
#
# Same move as demo 1, to the member at the other site. aen-mongo-04 lives on flasharray04, which stands in for a
# second site in my lab. Its array gets a replicated copy of the primary's protection group, so we rebuild it from
# the primary without copying 10 TiB across the WAN: only the blocks that changed since the last copy move.
#
# Blog posts: Replacing the MongoDB Replica Set Initial Sync with FlashArray Snapshots (the technique)
#   https://www.nocentino.com/posts/2026-11-17-mongodb-reseed-replica-between-flasharrays/
#
# and Expanding a MongoDB Replica Set with Everpure Fusion (how aen-mongo-04 joined the set on flasharray04)
#   https://www.nocentino.com/posts/2026-11-17-mongodb-expand-replica-set-everpure-fusion/
#
# ARCHITECTURE: the same replica set as demo 1. All four arrays are in one data center in my lab; flasharray04
# stands in for a second site, and the link to it stands in for the WAN.
#
#         4 writers
#                │
#   ┌─ SITE A ───┼─────────────────────────────────────────────────────────────┐   ┌─ SITE B ───────────────┐
#   │            ▼                                                             │   │                        │
#   │ flasharray01             flasharray02             flasharray03           │   │ flasharray04           │
#   │ ┌──────────┴─────────┐   ┌────────────────────┐   ┌────────────────────┐ │   │ ┌────────────────────┐ │
#   │ │ aen-mongo-05 (P)   │   │ aen-mongo-06       │   │ aen-mongo-07       │ │   │ │ aen-mongo-04       │ │
#   │ │ data journal oplog │   │ data journal oplog │   │ data journal oplog │ │   │ │ data journal oplog │ │
#   │ │ 1. snapshot here   │   │ keeps running      │   │ keeps running      │ │   │ │ <- 3. rebuilt      │ │
#   │ └──────────┬─────────┘   └────────────────────┘   └────────────────────┘ │   │ └──────────▲─────────┘ │
#   │            │                                                             │WAN│            │           │
#   │            └── 2. replicate_now: changed blocks only ────────────────────┼───┼────────────┘           │
#   └──────────────────────────────────────────────────────────────────────────┘   └────────────────────────┘
#
#   1. One snapshot call on flasharray01: the primary's data, journal and oplog at the same instant, no fsyncLock.
#   2. replicate_now: only the blocks that changed since the last copy cross to site B, not 10 TiB.
#   3. Stop aen-mongo-04, overwrite its three volumes from aen-mongo-05's copies on flasharray04, mount, start.
#      It rejoins as SECONDARY with no initial sync. Site A never stops serving.
#
# Highlight a block and run it (Shift+Enter in VS Code sends it to the Python terminal).


# ===============================================
# CONNECT AND LOOK AT THE REPLICA SET
# ===============================================
from helpers import *
from pypureclient.flasharray import ProtectionGroupSnapshotPost, Reference, VolumePost
fa = connect()
TARGET, TARGET_ARRAY = "aen-mongo-04", "flasharray04"
members()


# The primary's protection group on flasharray01 replicates to the arrays under every other member, both sites.
targets = fa.get_protection_groups_targets(group_names=[PG], context_names=[ARRAYS["flasharray01"]])
show([t.member.name for t in check(targets, "targets").items])


# ===============================================
# PUT THE PRIMARY UNDER LOAD
# ===============================================
start_load()


# ===============================================
# SNAPSHOT THE PRIMARY AND REPLICATE IT TO THE OTHER SITE
# ===============================================
snap = fa.post_protection_group_snapshots(source_names=[PG], context_names=[ARRAYS["flasharray01"]], replicate_now=True,
                                          protection_group_snapshot=ProtectionGroupSnapshotPost(suffix=suffix("seed04")))
snap = list(check(snap, "snapshot").items)[0].name
print(snap)


# The copy at site B. Look at what crossed the link: changed blocks, not 10 TiB.
xfer = wait_for_transfer(fa, snap, TARGET_ARRAY)


# ===============================================
# OVERWRITE THE SITE B MEMBER FROM THE PRIMARY'S COPY
# ===============================================
since = utc_now(TARGET)


# Detach the target from the replica set before overwriting its volumes
detach(TARGET)


# Overwrite each role's volume on the target with the corresponding snapshot from the primary
for role in ROLES:
    assert not ssh(TARGET, f"findmnt -n /u01/{role}", allow_fail=True), f"{TARGET} still has /u01/{role} mounted: run detach first"
    source = f"{ARRAYS['flasharray01']}:{snap}.{vol(PRIMARY, role)}"
    check(fa.post_volumes(names=[vol(TARGET, role)], context_names=[ARRAYS[TARGET_ARRAY]], overwrite=True,
                          volume=VolumePost(source=Reference(name=source))), f"overwrite {role}")
    print(f"overwrote {vol(TARGET, role)} from {alias(source)}")


# Reattach the target to the replica set
attach(fa, TARGET)


# Start the target after reattaching it to the replica set
start(TARGET)


# ===============================================
# WATCH IT REJOIN, AND PROVE IT
# ===============================================
wait_for_state(TARGET, "SECONDARY")


# Show the state transitions of the target since the overwrite operation
transitions(TARGET, since)


# Stop the load on the primary after the target has rejoined and caught up
stop_load()


# Wait until the target has caught up with the primary before proceeding
wait_until_caught_up()


# Show the current members of the replica set
members()


# Compare the current members of the replica set with the expected configuration
compare_members()


# Check for any gaps in the replication of the target
gaps(TARGET)
