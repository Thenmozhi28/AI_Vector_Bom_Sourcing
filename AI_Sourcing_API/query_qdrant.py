from qdrant_manager import qdrant_db

q = "TBS2204"
print(f"Searching exact: {q}")
exact = qdrant_db.exact_mpn_search(q)
print("Exact match results:", exact)

print("\nSearching semantic:")
semantic = qdrant_db.search(q, limit=5)
for idx, hit in enumerate(semantic):
    print(f"Hit {idx+1}: Score: {hit['score']:.4f} | MPN: {hit['payload'].get('mpn')} | Desc: {hit['payload'].get('description')}")
