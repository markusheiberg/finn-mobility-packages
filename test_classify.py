from finn_mobility_packages_requests import get_finnkodes_for_bucket, classify_ad

fks = get_finnkodes_for_bucket(375000, 400000)
print(f"\nFound {len(fks)} finnkodes\n")
for fk in fks[:10]:
    pkg, dbg = classify_ad(fk)
    print(fk, pkg, dbg)
