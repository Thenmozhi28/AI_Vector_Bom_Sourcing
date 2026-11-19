import pandas as pd
from utils import parse_bom_to_df, identify_columns
from qdrant_manager import qdrant_db

def main():
    with open("/app/uploads/Test_Input1.xlsx", "rb") as f:
        content = f.read()
    df, original_headers = parse_bom_to_df(content, "Test_Input1.xlsx")
    print("DataFrame shape:", df.shape)
    print("Original headers:", original_headers)
    print("DataFrame columns:", list(df.columns))
    print("DataFrame head:")
    print(df.head())
    
    mapping = identify_columns(df, vdb_search_func=qdrant_db.search)
    print("\nMapping results:")
    print(mapping)

if __name__ == "__main__":
    main()
