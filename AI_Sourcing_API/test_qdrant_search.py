from qdrant_manager import qdrant_db

print("Starting test...")
res = qdrant_db.exact_mpn_search("9805")
print("Exact Match '9805' Results:", res)

# Let's also do a general search to see what matches
res_search = qdrant_db.search("9805", limit=5)
print("\nGeneral Search '9805' Results:")
for r in res_search:
    payload = r.get('payload', {})
    print(f"  MPN: {payload.get('mpn')}, Score: {r.get('score')}, Brand: {payload.get('manufacturer')}, Desc: {payload.get('description')}")
