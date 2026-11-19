from qdrant_manager import qdrant_db
import json

test_mpns = ["WT2F-P170", "LM2574-5.0YN", "9805", "M85049/60-2G12W", "WT2F-P450", "DIN 963", "BN654"]

print("Searching for test MPNs in Qdrant database...")
for mpn in test_mpns:
    results = qdrant_db.exact_mpn_search(mpn)
    print("-" * 50)
    print(f"MPN: {mpn}")
    print(f"Exact match results count: {len(results)}")
    if results:
        for idx, r in enumerate(results[:2]):
            print(f"  Result {idx+1}:")
            print(f"    Payload: {json.dumps(r.get('payload', {}), indent=2)}")
            print(f"    Score: {r.get('score')}")
    else:
        # Try semantic search
        sem_res = qdrant_db.search(mpn, limit=2)
        print(f"  Semantic match results count: {len(sem_res)}")
        for idx, r in enumerate(sem_res):
            print(f"    Result {idx+1}:")
            print(f"      Payload: {json.dumps(r.get('payload', {}), indent=2)}")
            print(f"      Score: {r.get('score')}")
