from qdrant_client import QdrantClient
from qdrant_manager import qdrant_db

client = qdrant_db.client

print("Loading fine-tuned AI model...")
qdrant_db._initialize_model()

target_mpns = ["CRCW040210K0FKED", "C0603C104K5RACTU", "STM32F103RBT6", "395311006", "282080-1"]
neutral_id = "0141375"

points_to_update = []

# 1. Fetch points matching target MPNs
for mpn in target_mpns:
    print(f"Scrolling points for MPN: {mpn}...")
    res, _ = client.scroll(
        collection_name="products",
        scroll_filter={
            "must": [
                {
                    "key": "mpn",
                    "match": {
                        "value": mpn
                    }
                }
            ]
        },
        limit=50
    )
    if res:
        print(f"Found {len(res)} points for {mpn}")
        for p in res:
            points_to_update.append(p)
    else:
        print(f"No points found for {mpn}")

# 2. Fetch neutral resistor matching product ID
print(f"Scrolling points for Neutral Resistor ID: {neutral_id}...")
# We do a scroll filter for raw_data containing '0141375'
res, _ = client.scroll(
    collection_name="products",
    scroll_filter={
        "must": [
            {
                "key": "raw_data",
                "match": {
                    "text": neutral_id
                }
            }
        ]
    },
    limit=10
)
if res:
    print(f"Found {len(res)} points matching {neutral_id}")
    for p in res:
        points_to_update.append(p)

print(f"Total points to re-embed: {len(points_to_update)}")

upsert_points = []
for p in points_to_update:
    raw_data = p.payload.get('raw_data', '')
    if not raw_data:
        continue
    # Reconstruct the original search_text exactly as done during ingestion
    row_list = [x.replace('"', '') for x in raw_data.split('|')]
    search_text = " ".join([str(x) for x in row_list if str(x) and str(x).lower() not in ['nan', 'none']])
    
    # Generate new embedding using the fine-tuned model
    print(f"Encoding text for {p.payload.get('mpn', 'Neutral')}: {search_text[:60]}...")
    vector = qdrant_db.get_embeddings([search_text])[0]
    
    upsert_points.append({
        "id": p.id,
        "vector": vector,
        "payload": p.payload
    })

if upsert_points:
    print("Upserting re-embedded points to Qdrant...")
    qdrant_db.upsert_batch(upsert_points)
    print("✅ Successfully updated target points in Qdrant!")
else:
    print("⚠️ No points were re-embedded.")

# Quick search test
print("\nRunning Verification Search for 'TX Instr 10k res 0402':")
results = qdrant_db.search("TX Instr 10k res 0402", limit=5)
for idx, r in enumerate(results):
    payload = r['payload']
    print(f"[{idx+1}] Score: {r['score']:.4f} | MPN: {payload.get('mpn')} | Brand: {payload.get('manufacturer')} | Desc: {payload.get('description')}")
