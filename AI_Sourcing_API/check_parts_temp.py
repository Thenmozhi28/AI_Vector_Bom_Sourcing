from qdrant_client import QdrantClient
import json

# Within the Docker network, the Qdrant host is 'sourcebuddy-qdrant'
client = QdrantClient(host="sourcebuddy-qdrant", port=6333)

target_mpns = ["9805", "9111", "8540", "MPS-6033XP", "IT6332A", "IT8512H+", "TBS2204B", "MDO34 3-BW-350"]

print("Searching Qdrant Database for target MPNs:")
print("=" * 60)

for mpn in target_mpns:
    # Perform scroll filter search on 'mpn' field
    result, _ = client.scroll(
        collection_name="products",
        scroll_filter={
            "must": [
                {
                    "key": "mpn",
                    "match": {
                        "value": mpn.strip()
                    }
                }
            ]
        },
        limit=5
    )
    
    print(f"MPN: '{mpn}'")
    if result:
        print(f"  Found {len(result)} records in database:")
        for r in result:
            payload = r.payload
            print(f"    - MPN: {payload.get('mpn')}, Brand: {payload.get('manufacturer')}, Desc: {payload.get('description')[:80]}")
    else:
        print("  ❌ NOT FOUND IN DATABASE")
    print("-" * 60)
