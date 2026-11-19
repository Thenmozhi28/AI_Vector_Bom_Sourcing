from qdrant_client import QdrantClient

client = QdrantClient(host="sourcebuddy-qdrant", port=6333)

try:
    collection_info = client.get_collection("products")
    print("Qdrant Collection Status:")
    print("=" * 60)
    print(f"Status: {collection_info.status}")
    print(f"Vectors Count: {collection_info.vectors_count}")
    print(f"Points Count: {collection_info.points_count}")
    print("=" * 60)
    
    # Scroll first 5 points
    results, _ = client.scroll(
        collection_name="products",
        limit=5,
        with_payload=True,
        with_vectors=False
    )
    
    print("Sample Points in Collection:")
    for i, point in enumerate(results):
        print(f"Point #{i+1}: ID={point.id}")
        print(f"  Payload: {point.payload}")
        print("-" * 40)
        
except Exception as e:
    print(f"Error querying collection: {e}")
