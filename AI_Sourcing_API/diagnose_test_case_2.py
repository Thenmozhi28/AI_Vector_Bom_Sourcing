import sys
import os
import pandas as pd
from utils import parse_bom_to_df, identify_columns
from qdrant_manager import qdrant_db

def main():
    file_path = "/app/uploads/Test_Input2.xlsx"
    if not os.path.exists(file_path):
        file_path = "uploads/Test_Input2.xlsx"
        
    print(f"Reading file: {file_path}")
    with open(file_path, "rb") as f:
        content = f.read()
        
    df, original_headers = parse_bom_to_df(content, "Test_Input2.xlsx")
    print("DataFrame columns:", list(df.columns))
    print("Original headers:", original_headers)
    
    mapping = identify_columns(df, vdb_search_func=qdrant_db.search)
    print("\nMapping results:")
    print(mapping)
    
    print("\nFirst 3 rows:")
    for idx, row in df.head(3).iterrows():
        mpn = str(row.iloc[mapping.get('mpn', -1)]) if mapping.get('mpn', -1) >= 0 else ""
        mfr = str(row.iloc[mapping.get('manufacturer', -1)]) if mapping.get('manufacturer', -1) >= 0 else ""
        desc = str(row.iloc[mapping.get('description', -1)]) if mapping.get('description', -1) >= 0 else ""
        qty = str(row.iloc[mapping.get('qty', -1)]) if mapping.get('qty', -1) >= 0 else "1"
        print(f"Row {idx+1} -> MPN: '{mpn}', Manufacturer: '{mfr}', Description: '{desc}', Qty: '{qty}'")

if __name__ == "__main__":
    main()
