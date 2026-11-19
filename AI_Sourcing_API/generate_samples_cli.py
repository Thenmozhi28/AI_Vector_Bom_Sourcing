import os
import json
import httpx
import argparse
from tqdm import tqdm
from qdrant_manager import qdrant_db

def generate_samples(limit: int):
    print(f"🚀 Starting standalone synthetic data generation for {limit} records...")
    
    # 1. Scroll records from Qdrant
    print("Fetching records from Qdrant...")
    records, next_page = qdrant_db.client.scroll(
        collection_name=qdrant_db.collection_name,
        limit=limit,
        with_payload=True
    )
    
    if not records:
        print("❌ No records found in Qdrant!")
        return

    print(f"Found {len(records)} records. Generating messy versions using LLaMA3...")
    
    generated_count = 0
    output_dir = "./model_cache/training_data"
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, "training_triplets.jsonl")
    
    with open(output_file, "a", encoding="utf-8") as f:
        for record in tqdm(records, desc="Generating"):
            payload = record.payload
            desc = payload.get("description", "")
            mpn = payload.get("catalog_mpn", "")
            if not desc: 
                continue
            
            # 2. Ask Ollama to generate a messy version
            prompt = (
                f"Act as a lazy procurement engineer. "
                f"Write a messy, highly abbreviated, 3-to-5 word BOM description "
                f"for this electronic component: '{desc}'. "
                f"Return ONLY the messy string, no quotes, no explanation."
            )
            
            try:
                res = httpx.post(
                    "http://172.17.0.1:8010/api/generate",
                    json={"model": "llama3:8b", "prompt": prompt, "stream": False},
                    timeout=30.0
                )
                if res.status_code == 200:
                    messy_anchor = res.json().get("response", "").strip().strip('"\'')
                    
                    triplet = {
                        "anchor": messy_anchor,
                        "positive_mpn": mpn,
                        "positive_description": desc,
                        "negative_description": None
                    }
                    f.write(json.dumps(triplet) + "\n")
                    generated_count += 1
            except Exception as e:
                print(f"\nFailed on record {mpn}: {e}")
                
    print(f"\n✅ Successfully generated {generated_count} synthetic samples!")
    print(f"Saved to: {output_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic training data.")
    parser.add_argument("--limit", type=int, default=1000, help="Number of samples to generate")
    args = parser.parse_args()
    
    generate_samples(args.limit)
