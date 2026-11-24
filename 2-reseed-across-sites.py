# DEMO 2: RESEED A REPLICA SET MEMBER ACROSS SITES
#
# Same move as demo 1, to the member at the other site. aen-mongo-04 lives on flasharray04, which stands in for a
# second site in my lab. Its array gets a replicated copy of the primary's protection group, so we rebuild it from
# the primary without copying 10 TiB across the WAN: only the blocks that changed since the last copy move.
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
detach(TARGET)


for role in ROLES:
    assert not ssh(TARGET, f"findmnt -n /u01/{role}", allow_fail=True), f"{TARGET} still has /u01/{role} mounted: run detach first"
    source = f"{ARRAYS['flasharray01']}:{snap}.{vol(PRIMARY, role)}"
    check(fa.post_volumes(names=[vol(TARGET, role)], context_names=[ARRAYS[TARGET_ARRAY]], overwrite=True,
                          volume=VolumePost(source=Reference(name=source))), f"overwrite {role}")
    print(f"overwrote {vol(TARGET, role)} from {alias(source)}")


attach(fa, TARGET)
start(TARGET)


# ===============================================
# WATCH IT REJOIN, AND PROVE IT
# ===============================================
wait_for_state(TARGET, "SECONDARY")
transitions(TARGET, since)


stop_load()
wait_until_caught_up()
members()
compare_members()
gaps(TARGET)
