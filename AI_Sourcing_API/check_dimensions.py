import torch
from sentence_transformers import SentenceTransformer
import os

model_path = "./model_cache/sourcebuddy-electronics-bert"
backup_path = "./model_cache/sourcebuddy-electronics-bert-finetuned"

print("Checking model dimension structures:")
print("=" * 60)

try:
    print(f"Loading Active Custom Model from {model_path}...")
    model = SentenceTransformer(model_path, device="cpu")
    vec = model.encode("test query")
    print(f"  Active Model Vector Shape: {vec.shape} (Dimensions: {len(vec)})")
except Exception as e:
    print(f"  ❌ Failed to load active model: {e}")

print("-" * 60)

try:
    print(f"Loading Backup Model from {backup_path}...")
    backup_model = SentenceTransformer(backup_path, device="cpu")
    backup_vec = backup_model.encode("test query")
    print(f"  Backup Model Vector Shape: {backup_vec.shape} (Dimensions: {len(backup_vec)})")
except Exception as e:
    print(f"  ❌ Failed to load backup model: {e}")

print("=" * 60)
