import os
import json
import random
import re

# We will use qdrant_client. But wait, if qdrant_client isn't installed in the host, we'll run this inside docker.
# We'll use the qdrant REST API since it's foolproof.
import requests

QDRANT_URL = "http://sourcebuddy-qdrant:6333/collections/products/points/scroll"
TRIPLETS_FILE = "./model_cache/training_data/training_triplets.jsonl"

def augment_description(text: str, mpn: str) -> str:
    """Generate a realistic messy BOM string from exact product data."""
    choice = random.randint(1, 4)
    words = text.split()
    
    if choice == 1:
        # Partial match, missing words
        keep = max(1, int(len(words) * 0.6))
        messy = " ".join(random.sample(words, keep))
        return f"{mpn} {messy}"
    elif choice == 2:
        # Scrambled order with MPN inside
        words.append(mpn)
        random.shuffle(words)
        return " ".join(words)
    elif choice == 3:
        # Typo: replace 'a' with 'e' and delete 'i'
        messy = text.replace('a', 'e').replace('i', '')
        return f"{messy} {mpn}"
    else:
        # Just MPN and some random characters simulating OCR errors
        messy = re.sub(r'[aeiou]', '', text, flags=re.IGNORECASE)
        return f"{mpn} {messy}"

def generate_data():
    os.makedirs(os.path.dirname(TRIPLETS_FILE), exist_ok=True)
    
    payload = {
        "limit": 1500,
        "with_payload": True,
        "with_vector": False
    }
    
    try:
        response = requests.post(QDRANT_URL, json=payload)
        response.raise_for_status()
        points = response.json().get('result', {}).get('points', [])
        
        if not points:
            print("No points returned from Qdrant!")
            return
            
        print(f"Loaded {len(points)} records from Qdrant.")
        
        triplets = []
        for p in points:
            mpn = p.get('payload', {}).get('mpn', '')
            desc = p.get('payload', {}).get('description', '')
            if not mpn or not desc:
                continue
                
            # Create two augmented variations
            for _ in range(2):
                anchor = augment_description(desc, mpn)
                triplets.append({
                    "anchor": anchor,
                    "positive_mpn": mpn,
                    "positive_description": desc,
                    "negative_description": None
                })
        
        # Add some exact matches to strongly anchor the model
        for p in points:
            mpn = p.get('payload', {}).get('mpn', '')
            desc = p.get('payload', {}).get('description', '')
            if not mpn or not desc:
                continue
            triplets.append({
                "anchor": f"{mpn} {desc}",
                "positive_mpn": mpn,
                "positive_description": desc,
                "negative_description": None
            })
            
        random.shuffle(triplets)
        
        with open(TRIPLETS_FILE, "w", encoding="utf-8") as f:
            for t in triplets:
                f.write(json.dumps(t) + "\n")
                
        print(f"Successfully generated {len(triplets)} high-quality training triplets at {TRIPLETS_FILE}")
        
    except Exception as e:
        print(f"Error generating data: {e}")

if __name__ == "__main__":
    generate_data()
