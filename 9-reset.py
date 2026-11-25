# WHEN YOU ARE DONE: clean up what the reseed demos created.
#
# Destroys (doesn't eradicate) the demo snapshots and their replicated copies on every target: seed06-* and seed04-* of
# aen-mongo-pg-multi (demos 1 and 2), and seedsh1-* of aen-mongo-pg-sharded (demo 4). It leaves the baselines alone,
# because the next run of each demo depends on them to keep its transfers small, and it never touches the SafeMode
# protection group.
#
# Highlight a block and run it.


from helpers import *
import sharded as sh
from pypureclient.flasharray import ProtectionGroupSnapshotPatch
fa = connect()
stop_load()      # in case a demo stopped partway
sh.stop_load()


# The demo snapshots on the source array, for both groups.
src = ARRAYS["flasharray01"]
demo = {group: [s.name for s in fa.get_protection_group_snapshots(filter=f"source.name='{group}'", context_names=[src], destroyed=False).items
                if s.name.split(".")[-1].startswith(prefixes)]
        for group, prefixes in [(PG, ("seed06-", "seed04-")), (sh.PG, ("seedsh1-",))]}
show(demo)


# Destroy each one on the source array and its copy on every target of its group.
for group, names in demo.items():
    targets = [t.member.name for t in check(fa.get_protection_groups_targets(group_names=[group], context_names=[src]), "targets").items]
    for name in names:
        for array, n in [(src, name)] + [(t, f"{src}:{name}") for t in targets]:
            r = fa.patch_protection_group_snapshots(names=[n], context_names=[array],
                                                    protection_group_snapshot=ProtectionGroupSnapshotPatch(destroyed=True))
            print(f"{alias(n)} on {alias(array)}:", "destroyed" if not isinstance(r, flasharray.ErrorResponse) else alias(r.errors[0].message))


# Every member should be healthy before you call it done.
members()
sh.members()
