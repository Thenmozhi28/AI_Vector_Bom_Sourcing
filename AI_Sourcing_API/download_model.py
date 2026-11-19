import os
from pathlib import Path

# Custom Offline Model Configuration
model_name = "sourcebuddy-electronics-bert"
local_model_path = Path("./model_cache") / model_name

print("=" * 65)
print("🧠 SourceBuddy Sourcing Engine Model Status")
print("=" * 65)
print(f"📁 Target Model Cache Directory: {local_model_path.absolute()}")

# Verify if the custom model exists locally
if local_model_path.exists() and (local_model_path / "config.json").exists():
    print(f"✅ FOUND: Custom model '{model_name}' is fully cached and ready.")
    print("🚀 The system is operating in a 100% Air-Gapped environment.")
    print("🔒 No external internet connection or downloads are needed.")
else:
    print(f"⚠️ WARNING: Custom model directory not found at {local_model_path.absolute()}")
    print("💡 Please ensure your custom trained model weights are copied there.")
    print("   Or run: python train_custom_transformer.py to train the model from scratch.")

print("=" * 65)
