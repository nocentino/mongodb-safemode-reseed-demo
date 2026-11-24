# Run this BEFORE the demos, top to bottom. It does the slow, one-time work so the live demos only move changed blocks.
#
#   1. a baseline replicated snapshot of the primary's protection group to every target array
#      (the first copy of 10 TiB is the only size-of-data transfer; after it, only changed blocks cross the link),
#      then destroy the older baselines so reruns don't pile up
#   2. the SafeMode protection group for demo 3, built from clones of that snapshot (a metadata copy, no data moves)
#
# Safe to rerun. The baseline section replaces the previous baseline, and the SafeMode section skips itself once the
# group exists.
#
# Highlight a block and run it (Shift+Enter in VS Code sends it to the Python terminal).


# ===============================================
# CONNECT AND LOOK AT THE REPLICA SET
# ===============================================
# One connection to the Fusion fleet's gateway. Every array call names its array with context_names.
from helpers import *
from pypureclient.flasharray import (ProtectionGroup, ProtectionGroupSnapshotPatch, ProtectionGroupSnapshotPost,
                                     Reference, RetentionPolicy, VolumePost)
fa = connect()
members()
data_size()


# Each member's data, journal and oplog are in aen-mongo-pg-multi on its own array. The log volume never is.
for node, name in MEMBERS.items():
    pg_members = [m.member.name for m in fa.get_protection_groups_volumes(group_names=[PG], context_names=[ARRAYS[name]]).items]
    print(f"{node}  {name}  {PG}: {', '.join(pg_members)}")


# The primary's group replicates to the other three arrays. Those targets are what make a reseed possible.
targets = fa.get_protection_groups_targets(group_names=[PG], context_names=[ARRAYS["flasharray01"]])
show([t.member.name for t in check(targets, "targets").items])


# ===============================================
# BASELINE: ONE REPLICATED SNAPSHOT TO EVERY TARGET
# ===============================================
# No fsyncLock. A protection group snapshot captures the member's data, journal and oplog at the same instant.
SRC, TARGETS = ARRAYS["flasharray01"], ["flasharray02", "flasharray03", "flasharray04"]
r = fa.post_protection_group_snapshots(source_names=[PG], context_names=[SRC], replicate_now=True,
                                       protection_group_snapshot=ProtectionGroupSnapshotPost(suffix=suffix("baseline")))
baseline = list(check(r, "baseline snapshot").items)[0].name
print(baseline)


# The first copy to each array is the expensive one, about an hour for 10 TiB in my lab. Run it well before you demo.
# A rerun only moves what changed since the last baseline.
landed = {target: wait_for_transfer(fa, baseline, target, poll=60) for target in TARGETS}


# Manual snapshots of this group never expire (its retention only covers scheduled ones), so every rerun would leave
# another baseline on all four arrays. These are the older ones, on flasharray01.
old = [s.name for s in fa.get_protection_group_snapshots(filter=f"source.name='{PG}'", context_names=[SRC], destroyed=False).items
       if s.name.split(".")[-1].startswith("baseline-") and s.name != baseline]
show(old or "no older baselines")


# Destroy them and their copies on every target, now that the new baseline has landed everywhere. The next transfer
# only needs the newest snapshot the arrays have in common, which is the one we just made. Destroy, not eradicate:
# they sit in the destroyed bin until the array eradicates them on its own.
assert len(landed) == len(TARGETS), "wait for the new baseline to land on every target first"
for name in old:
    for array, n in [(SRC, name)] + [(ARRAYS[t], f"{SRC}:{name}") for t in TARGETS]:
        r = fa.patch_protection_group_snapshots(names=[n], context_names=[array],
                                                protection_group_snapshot=ProtectionGroupSnapshotPatch(destroyed=True))
        print(f"{alias(n)} on {alias(array)}:", "destroyed" if not isinstance(r, flasharray.ErrorResponse) else alias(r.errors[0].message))


# ===============================================
# SAFEMODE: A PROTECTION GROUP FOR DEMO 3
# ===============================================
# This section builds the group once. Ratcheting is permanent, so every block below checks SM_EXISTS and skips itself
# when the group is already there. Then only the last block, which just reads, does anything.
SM_ARRAY, SM_PG = ARRAYS["flasharray01"], "aen-mongo-pg-safemode"
SM_VOLS = [f"aen-mongo-safemode-{role}" for role in ROLES]
SM_EXISTS = bool(list(fa.get_protection_groups(filter=f"name='{SM_PG}'", context_names=[SM_ARRAY]).items))
print("already set up, the next blocks will skip themselves" if SM_EXISTS else "not set up yet")


# Three clones of the primary's volumes from the baseline snapshot. A clone is a metadata copy: 10 TiB, no data moves.
if SM_EXISTS:
    print("skipped: the SafeMode group already exists")
else:
    for role, v in zip(ROLES, SM_VOLS):
        r = fa.post_volumes(names=[v], context_names=[SM_ARRAY],
                            volume=VolumePost(source=Reference(name=f"{baseline}.{vol(PRIMARY, role)}")))
        print(v, "created" if not isinstance(r, flasharray.ErrorResponse) else alias(r.errors[0].message))


# A protection group that holds them, keeping every snapshot for one day.
if SM_EXISTS:
    print("skipped: the SafeMode group already exists")
else:
    check(fa.post_protection_groups(names=[SM_PG], context_names=[SM_ARRAY]), "create protection group")
    check(fa.post_protection_groups_volumes(group_names=[SM_PG], member_names=SM_VOLS, context_names=[SM_ARRAY]), "add volumes")
    check(fa.patch_protection_groups(names=[SM_PG], context_names=[SM_ARRAY],
                                     protection_group=ProtectionGroup(source_retention=RetentionPolicy(all_for_sec=86400, per_day=0, days=0))),
          "retention")


# THE ONE-WAY STEP. Ratcheting the retention lock turns on SafeMode for this group. From here, nobody with array
# credentials can unlock it, shorten its retention, or destroy its snapshots early. Only Everpure Technical Services
# can set it back to unlocked.
if SM_EXISTS:
    print("skipped: the SafeMode group already exists and is already ratcheted")
else:
    check(fa.patch_protection_groups(names=[SM_PG], context_names=[SM_ARRAY],
                                     protection_group=ProtectionGroup(retention_lock="ratcheted")), "ratchet")


# The group as demo 3 will find it.
pg = list(fa.get_protection_groups(filter=f"name='{SM_PG}'", context_names=[SM_ARRAY]).items)[0]
print(pg.name, "retention_lock:", pg.retention_lock, " manual eradication:", pg.eradication_config.manual_eradication,
      " keep all snapshots for:", pg.source_retention.all_for_sec // 3600, "hours")
