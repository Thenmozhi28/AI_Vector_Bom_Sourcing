import io
import asyncio
from routers import search_bom

class MockFile:
    filename = "Test_Input1.xlsx"
    def __init__(self, content):
        self.content = content
    async def read(self):
        return self.content

async def main():
    from models import state
    state.vdb_built = True
    with open("/app/uploads/Test_Input1.xlsx", "rb") as f:
        content = f.read()
    mock_file = MockFile(content)
    res = await search_bom(mock_file, top_k=5)
    import json
    compact = []
    for r in res["summary"]:
        compact.append({
            "row_number": r["row_number"],
            "specification": r["specification"],
            "customer_part_no": r["customer_part_no"],
            "confidence_score": r["confidence_score"],
            "qty": r.get("qty"),
            "project_qty": r.get("project_qty"),
            "matched_mpn": r["chunk"].get("mpn") if r.get("chunk") else None,
            "matched_mfr": r["chunk"].get("manufacturer") if r.get("chunk") else None,
            "matches_count": len(r.get("matches", []))
        })
    print("\n==========================================")
    print("🎯 COMPACT RESULTS")
    print("==========================================")
    print(json.dumps(compact, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
