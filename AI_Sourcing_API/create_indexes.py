from qdrant_client import QdrantClient
from qdrant_client.models import PayloadSchemaType, TextIndexParams, TokenizerType
import os

qdrant_host = os.getenv("QDRANT_HOST", "sourcebuddy-qdrant")
client = QdrantClient(host=qdrant_host, port=6333)

print("Creating payload indexes...")
try:
    client.create_payload_index("products", "mpn", field_schema=PayloadSchemaType.KEYWORD)
    print("MPN Keyword index created.")
except Exception as e:
    print(f"MPN index error: {e}")

try:
    client.create_payload_index("products", "description", field_schema=TextIndexParams(
        type="text",
        tokenizer=TokenizerType.WORD,
        min_token_len=2,
        max_token_len=20,
        lowercase=True,
    ))
    print("Description Text index created.")
except Exception as e:
    print(f"Description index error: {e}")

try:
    client.create_payload_index("products", "manufacturer", field_schema=PayloadSchemaType.KEYWORD)
    print("Manufacturer Keyword index created.")
except Exception as e:
    print(f"Manufacturer index error: {e}")

print("Indexes created successfully!")
