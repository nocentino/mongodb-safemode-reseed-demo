# Run this BEFORE the sharded demo, top to bottom. It does the slow, one-time work so the live demo only moves changed
# blocks.
#
#   1. make flasharray03 a replication target of flasharray01's aen-mongo-pg-sharded (one call, made once)
#   2. a baseline replicated snapshot of that group to flasharray03 (the first copy is the only size-of-data transfer;
#      after it, only changed blocks cross the link), then destroy the older sharded baselines so reruns don't pile up
#
# Safe to rerun: the target is only added if it's missing, and each baseline replaces the one before it.
#
# Highlight a block and run it.


# ===============================================
# CONNECT AND LOOK AT THE SHARDED CLUSTER
# ===============================================
from sharded import *
from pypureclient.flasharray import ProtectionGroupSnapshotPatch, ProtectionGroupSnapshotPost
fa = connect()
SRC, TARGET_ARRAY = ARRAYS["flasharray01"], "flasharray03"
members()
data_size()


# Each array's aen-mongo-pg-sharded holds its three mongods: a member of sh1, a member of sh2 and a config server.
for name in ["flasharray01", "flasharray02", "flasharray03"]:
    pg_members = [m.member.name for m in fa.get_protection_groups_volumes(group_names=[PG], context_names=[ARRAYS[name]]).items]
    print(f"{name}  {PG}: {', '.join(pg_members)}")


# ===============================================
# REPLICATE FLASHARRAY01'S GROUP TO FLASHARRAY03
# ===============================================
# aen-mongo-sh1-03 can only be seeded by another member of sh1, so flasharray03 needs copies of flasharray01's group.
# There's no replication schedule: copies only move when a snapshot asks for replicate_now.
have = [t.member.name for t in check(fa.get_protection_groups_targets(group_names=[PG], context_names=[SRC]), "targets").items]
if ARRAYS[TARGET_ARRAY] in have:
    print(f"{PG} on flasharray01 already replicates to {TARGET_ARRAY}")
else:
    check(fa.post_protection_groups_targets(group_names=[PG], member_names=[ARRAYS[TARGET_ARRAY]], context_names=[SRC]), "add target")
    print(f"{PG} on flasharray01 now replicates to {TARGET_ARRAY}")


# The target array has to allow the group before anything can land there.
for t in check(fa.get_protection_groups_targets(group_names=[PG], context_names=[SRC]), "targets").items:
    print(f"{alias(t.member.name)}: allowed={getattr(t, 'allowed', None)}")


# ===============================================
# BASELINE: ONE REPLICATED SNAPSHOT TO FLASHARRAY03
# ===============================================
# No fsyncLock. The group snapshot captures every mongod on flasharray01 at the same instant.
r = fa.post_protection_group_snapshots(source_names=[PG], context_names=[SRC], replicate_now=True,
                                       protection_group_snapshot=ProtectionGroupSnapshotPost(suffix=suffix("baseline")))
baseline = list(check(r, "baseline snapshot").items)[0].name
print(baseline)


# The first copy is the expensive one: everything on flasharray01's three mongods, about 4 TiB on disk in my lab.
# Run it well before you demo. A rerun only moves what changed since the last baseline.
landed = wait_for_transfer(fa, baseline, TARGET_ARRAY, poll=60)


# Manual snapshots of this group never expire (retention only covers scheduled ones), so every rerun would leave another
# baseline on both arrays. These are the older ones, on flasharray01.
old = [s.name for s in fa.get_protection_group_snapshots(filter=f"source.name='{PG}'", context_names=[SRC], destroyed=False).items
       if s.name.split(".")[-1].startswith("baseline-") and s.name != baseline]
show(old or "no older baselines")


# Destroy them and their copies on flasharray03, now that the new baseline has landed. Destroy, not eradicate.
assert landed, "wait for the new baseline to land on flasharray03 first"
for name in old:
    for array, n in [(SRC, name), (ARRAYS[TARGET_ARRAY], f"{SRC}:{name}")]:
        r = fa.patch_protection_group_snapshots(names=[n], context_names=[array],
                                                protection_group_snapshot=ProtectionGroupSnapshotPatch(destroyed=True))
        print(f"{alias(n)} on {alias(array)}:", "destroyed" if not isinstance(r, flasharray.ErrorResponse) else alias(r.errors[0].message))
