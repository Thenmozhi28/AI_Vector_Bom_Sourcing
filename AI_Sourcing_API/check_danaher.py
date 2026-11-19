from qdrant_client import QdrantClient
from qdrant_client.http.models import Filter, FieldCondition, MatchText

client = QdrantClient(host="localhost", port=6333)

res = client.scroll(
    collection_name="products",
    scroll_filter=Filter(
        must=[
            FieldCondition(key="description", match=MatchText(text="Danaher 3025"))
        ]
    ),
    limit=5,
    with_payload=True
)

for p in res[0]:
    print(p.payload)

res2 = client.scroll(
    collection_name="products",
    scroll_filter=Filter(
        must=[
            FieldCondition(key="mpn", match=MatchText(text="3025"))
        ]
    ),
    limit=5,
    with_payload=True
)

for p in res2[0]:
    print("MPN Match:", p.payload)
