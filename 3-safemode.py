# DEMO 3: SAFEMODE SNAPSHOTS
#
# aen-mongo-pg-safemode on flasharray01 holds clones of the primary's data, journal and oplog, 10 TiB made from a
# snapshot in a second, no data copied. Its retention lock is ratcheted: SafeMode. We take a snapshot, try to get
# rid of it the way an attacker with admin credentials would, try to loosen the rules, and then restore from it.
#
# Highlight a block and run it (Shift+Enter in VS Code sends it to the Python terminal).


# ===============================================
# CONNECT AND LOOK AT THE SAFEMODE PROTECTION GROUP
# ===============================================
import time
from helpers import *
from pypureclient.flasharray import (ProtectionGroup, ProtectionGroupSnapshotPatch, ProtectionGroupSnapshotPost,
                                     Reference, RetentionPolicy, VolumePatch, VolumePost)
fa = connect()
A, SM_PG = ARRAYS["flasharray01"], "aen-mongo-pg-safemode"
pg = list(fa.get_protection_groups(filter=f"name='{SM_PG}'", context_names=[A]).items)[0]
print(pg.name, "| retention_lock:", pg.retention_lock, "| manual eradication:", pg.eradication_config.manual_eradication,
      "| keep every snapshot for", pg.source_retention.all_for_sec // 3600, "hours")
print("volumes:", [m.member.name for m in fa.get_protection_groups_volumes(group_names=[SM_PG], context_names=[A]).items])


# How long a destroyed object waits before the array eradicates it on its own, with manual eradication disabled.
delay = list(fa.get_arrays(context_names=[A]).items)[0].eradication_config.disabled_delay
print(f"SafeMode eradication delay on flasharray01: {delay // 3_600_000} hours")


# ===============================================
# TAKE A SAFEMODE SNAPSHOT
# ===============================================
snap = fa.post_protection_group_snapshots(source_names=[SM_PG], context_names=[A], apply_retention=True,
                                          protection_group_snapshot=ProtectionGroupSnapshotPost(suffix=suffix("safemode")))
snap = list(check(snap, "snapshot").items)[0].name
print(snap)


# ===============================================
# TRY TO DELETE IT
# ===============================================
# An admin, or someone holding stolen admin credentials, deletes the snapshot.
r = fa.patch_protection_group_snapshots(names=[snap], context_names=[A],
                                        protection_group_snapshot=ProtectionGroupSnapshotPatch(destroyed=True))
s = list(fa.get_protection_group_snapshots(filter=f"name='{snap}'", context_names=[A], destroyed=True).items)[0]
print(f"{s.name}: destroyed={s.destroyed}, the array eradicates it in {s.time_remaining // 3_600_000} hours")


# It isn't gone. It's waiting out the SafeMode delay. Now try to eradicate it, so it's gone for good.
r = fa.delete_protection_group_snapshots(names=[snap], context_names=[A])
print("eradicate:", "BLOCKED:", alias(r.errors[0].message) if isinstance(r, flasharray.ErrorResponse) else "ALLOWED")


# And put it back. A destroyed SafeMode snapshot comes back with one call.
check(fa.patch_protection_group_snapshots(names=[snap], context_names=[A],
                                          protection_group_snapshot=ProtectionGroupSnapshotPatch(destroyed=False)), "recover")
print(snap, "recovered")


# ===============================================
# TRY TO LOOSEN THE RULES
# ===============================================
# Shorten the retention so the snapshots expire sooner, unlock the group, or destroy it outright.
attempts = {
    "shorten retention to 1 hour": fa.patch_protection_groups(names=[SM_PG], context_names=[A],
        protection_group=ProtectionGroup(source_retention=RetentionPolicy(all_for_sec=3600, per_day=0, days=0))),
    "unlock the retention lock": fa.patch_protection_groups(names=[SM_PG], context_names=[A],
        protection_group=ProtectionGroup(retention_lock="unlocked")),
    "remove a volume from the group": fa.delete_protection_groups_volumes(group_names=[SM_PG], context_names=[A],
        member_names=["aen-mongo-safemode-oplog"]),
    "destroy the protection group": fa.patch_protection_groups(names=[SM_PG], context_names=[A],
        protection_group=ProtectionGroup(destroyed=True)),
}
for what, r in attempts.items():
    print(f"{what:32}", f"BLOCKED: HTTP {r.status_code} {alias(r.errors[0].message or 'Forbidden')}"
          if isinstance(r, flasharray.ErrorResponse) else "ALLOWED")


# Turning the lock off takes Everpure Technical Services, not an array admin.


# ===============================================
# RESTORE FROM IT, INSTANTLY
# ===============================================
# A new volume from the SafeMode snapshot's data volume: 16 TiB, and no recovery volume to size up front.
restored = suffix("aen-mongo-safemode-restore")
t0 = time.time()
r = fa.post_volumes(names=[restored], context_names=[A],
                    volume=VolumePost(source=Reference(name=f"{snap}.aen-mongo-safemode-data")))
v = list(check(r, "restore").items)[0]
print(f"{v.name}: {v.provisioned / 2**40:.0f} TiB, ready in {time.time() - t0:.2f} seconds")


# Clean up the restored volume. It isn't in the SafeMode group, so it can go.
check(fa.patch_volumes(names=[restored], context_names=[A], volume=VolumePatch(destroyed=True)), "destroy restored volume")
print(restored, "destroyed")
