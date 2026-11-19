from qdrant_manager import qdrant_db

mpns = ["TMM-105-01-F-D-SM-A-009-P-TR", "LM2574-5.0YN", "HTST-105-01-L-D"]

for mpn in mpns:
    print(f"\n==========================================")
    print(f"🔍 QUERYING FOR MPN: {mpn}")
    print(f"==========================================")
    exact = qdrant_db.exact_mpn_search(mpn)
    print("Exact search results:")
    if exact:
        for idx, item in enumerate(exact):
            payload = item.get('payload', {})
            print(f"  [{idx+1}] ID: {item.get('id')} | MPN: {payload.get('mpn')} | Manufacturer: {payload.get('manufacturer')} | Desc: {payload.get('description')}")
    else:
        print("  No exact match found.")
        
    print("Semantic search results (top 3):")
    semantic = qdrant_db.search(mpn, limit=3)
    if semantic:
        for idx, hit in enumerate(semantic):
            payload = hit.get('payload', {})
            print(f"  [{idx+1}] Score: {hit.get('score'):.4f} | MPN: {payload.get('mpn')} | Manufacturer: {payload.get('manufacturer')} | Desc: {payload.get('description')}")
    else:
        print("  No semantic results.")
