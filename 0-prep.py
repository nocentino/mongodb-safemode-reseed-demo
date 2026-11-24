# Run this BEFORE the demos, top to bottom. It does the slow, one-time work so the live demos only move changed blocks.
#
#   1. a baseline replicated snapshot of the primary's protection group to every target array
#      (the first copy of 10 TiB is the only size-of-data transfer; after it, only changed blocks cross the link)
#   2. the SafeMode protection group for demo 3, built from clones of that snapshot (a metadata copy, no data moves)
#
# Highlight a block and run it (Shift+Enter in VS Code sends it to the Python terminal).


# ===============================================
# CONNECT AND LOOK AT THE REPLICA SET
# ===============================================
# One connection to the Fusion fleet's gateway. Every array call names its array with context_names.
from helpers import *
from pypureclient.flasharray import (ProtectionGroup, ProtectionGroupSnapshotPost, Reference, RetentionPolicy,
                                     VolumePost)
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
baseline = fa.post_protection_group_snapshots(source_names=[PG], context_names=[ARRAYS["flasharray01"]], replicate_now=True,
                                              protection_group_snapshot=ProtectionGroupSnapshotPost(suffix=suffix("baseline")))
baseline = list(check(baseline, "baseline snapshot").items)[0].name
print(baseline)


# The first copy to each array is the expensive one, about an hour for 10 TiB in my lab. Run it well before you demo.
for target in ["flasharray02", "flasharray04"]:
    wait_for_transfer(fa, baseline, target, poll=60)


# ===============================================
# SAFEMODE: A PROTECTION GROUP FOR DEMO 3
# ===============================================
# This section runs once. Ratcheting is permanent, so check first: if the group already exists, skip to the last block.
SM_ARRAY, SM_PG = ARRAYS["flasharray01"], "aen-mongo-pg-safemode"
SM_VOLS = [f"aen-mongo-safemode-{role}" for role in ROLES]
print("already set up" if list(fa.get_protection_groups(filter=f"name='{SM_PG}'", context_names=[SM_ARRAY]).items) else "not set up yet")


# Three clones of the primary's volumes from the baseline snapshot. A clone is a metadata copy: 10 TiB, no data moves.
for role, v in zip(ROLES, SM_VOLS):
    r = fa.post_volumes(names=[v], context_names=[SM_ARRAY],
                        volume=VolumePost(source=Reference(name=f"{baseline}.{vol(PRIMARY, role)}")))
    print(v, "created" if not isinstance(r, flasharray.ErrorResponse) else alias(r.errors[0].message))


# A protection group that holds them, keeping every snapshot for one day.
check(fa.post_protection_groups(names=[SM_PG], context_names=[SM_ARRAY]), "create protection group")
check(fa.post_protection_groups_volumes(group_names=[SM_PG], member_names=SM_VOLS, context_names=[SM_ARRAY]), "add volumes")
check(fa.patch_protection_groups(names=[SM_PG], context_names=[SM_ARRAY],
                                 protection_group=ProtectionGroup(source_retention=RetentionPolicy(all_for_sec=86400, per_day=0, days=0))),
      "retention")


# THE ONE-WAY STEP. Ratcheting the retention lock turns on SafeMode for this group. From here, nobody with array
# credentials can unlock it, shorten its retention, or destroy its snapshots early. Only Everpure Technical Services
# can set it back to unlocked.
check(fa.patch_protection_groups(names=[SM_PG], context_names=[SM_ARRAY],
                                 protection_group=ProtectionGroup(retention_lock="ratcheted")), "ratchet")


# The group as demo 3 will find it.
pg = list(fa.get_protection_groups(filter=f"name='{SM_PG}'", context_names=[SM_ARRAY]).items)[0]
print(pg.name, "retention_lock:", pg.retention_lock, " manual eradication:", pg.eradication_config.manual_eradication,
      " keep all snapshots for:", pg.source_retention.all_for_sec // 3600, "hours")
