# WHEN YOU ARE DONE: clean up what the reseed demos created.
#
# Destroys (doesn't eradicate) the seed06-* and seed04-* snapshots of aen-mongo-pg-multi on flasharray01 and their
# replicated copies on every target. It leaves the baseline snapshot alone, because the next run of demos 1 and 2
# depends on it to keep their transfers small, and it never touches the SafeMode protection group.
#
# Highlight a block and run it.


from helpers import *
from pypureclient.flasharray import ProtectionGroupSnapshotPatch
fa = connect()
stop_load()   # in case a demo stopped partway


# The demo snapshots on the source array and their copies on the targets.
src = ARRAYS["flasharray01"]
local = [s.name for s in fa.get_protection_group_snapshots(filter=f"source.name='{PG}'", context_names=[src], destroyed=False).items
         if s.name.split(".")[-1].startswith(("seed06-", "seed04-"))]
show(local)


targets = [t.member.name for t in check(fa.get_protection_groups_targets(group_names=[PG], context_names=[src]), "targets").items]
for name in local:
    for array, n in [(src, name)] + [(t, f"{src}:{name}") for t in targets]:
        r = fa.patch_protection_group_snapshots(names=[n], context_names=[array],
                                                protection_group_snapshot=ProtectionGroupSnapshotPatch(destroyed=True))
        print(f"{alias(n)} on {alias(array)}:", "destroyed" if not isinstance(r, flasharray.ErrorResponse) else alias(r.errors[0].message))


# Every member should be healthy before you call it done.
members()
