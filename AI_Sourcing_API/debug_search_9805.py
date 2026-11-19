import sys
import re
sys.path.append(".")
from qdrant_manager import qdrant_db

query_text = "AC POWER SOURCE 1500 VA 9805 0 ~ 300VAC Output Bench (AC to AC) Power Supply VFD 0 ~ 12A Output Features Memory, Programmable, GPIB, LAN, RS232, and USB Interface"

def extract_ratings(text: str):
    pattern = r'\b(\d+(?:\.\d+)?)\s*(v|vac|vdc|w|watt|watts|volt|volts|a|amp|amps|ma|milliamp|milliamps)\b'
    matches = re.findall(pattern, text.lower())
    ratings = {}
    for val, unit in matches:
        try:
            val_num = float(val)
            if val_num == 0: continue
            if unit in ('v', 'vac', 'vdc', 'volt', 'volts'):
                ratings['v'] = val_num
            elif unit in ('w', 'watt', 'watts'):
                ratings['w'] = val_num
            elif unit in ('a', 'amp', 'amps'):
                ratings['a'] = val_num
            elif unit in ('ma', 'milliamp', 'milliamps'):
                ratings['a'] = val_num / 1000.0
        except ValueError:
            continue
    return ratings

print("🔍 Executing Semantic Search on Qdrant Database...")
print(f"Query Text: '{query_text}'\n")

# Run semantic search
candidates = qdrant_db.search(query_text, limit=10)

print(f"Found {len(candidates)} raw semantic candidates:")
print("=" * 100)

q_ratings = extract_ratings(query_text)
print(f"Extracted Query Specs: {q_ratings}\n")

for i, cand in enumerate(candidates):
    payload = cand.get("payload", {})
    desc = payload.get("description", "")
    mpn = payload.get("mpn", "")
    brand = payload.get("manufacturer", "")
    score = cand.get("score", 0.0)
    
    cand_ratings = extract_ratings(desc)
    
    rating_conflict = False
    for key in ('v', 'w', 'a'):
        if key in q_ratings and key in cand_ratings:
            q_val = q_ratings[key]
            res_val = cand_ratings[key]
            if abs(q_val - res_val) / max(q_val, res_val) > 0.1:
                rating_conflict = True
                break
                
    cand_conf = score * 100
    if rating_conflict:
        cand_conf = 30
        
    print(f"Candidate #{i+1}:")
    print(f"  MPN: {mpn}")
    print(f"  Brand: {brand}")
    print(f"  Desc: {desc}")
    print(f"  Raw Vector Similarity Score: {score:.4f} ({score*100:.1f}%)")
    print(f"  Extracted Specs: {cand_ratings}")
    print(f"  Physical Rating Conflict Found: {rating_conflict}")
    print(f"  Final Post-Guard Score: {cand_conf:.1f}%")
    print("-" * 100)
