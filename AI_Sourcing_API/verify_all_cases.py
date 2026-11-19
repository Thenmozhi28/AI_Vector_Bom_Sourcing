import os
import sys
import pandas as pd
sys.path.append(".")
from utils import parse_bom_to_df, identify_columns
from qdrant_manager import qdrant_db

def main():
    test_case_dir = "/app/uploads/Test_Case"
    if not os.path.exists(test_case_dir):
        test_case_dir = "uploads/Test_Case"
        
    print(f"Checking test cases in: {test_case_dir}")
    
    # List of all test files (sorted by index)
    test_files = []
    for i in range(1, 13):
        # Could be .xlsx or .pdf
        xlsx_path = os.path.join(test_case_dir, f"Test_Input{i}.xlsx")
        pdf_path = os.path.join(test_case_dir, f"Test_Input{i}.pdf")
        if os.path.exists(xlsx_path):
            test_files.append((i, xlsx_path))
        elif os.path.exists(pdf_path):
            test_files.append((i, pdf_path))
            
    print(f"Found {len(test_files)} test files to verify.\n")
    
    success_count = 0
    failure_count = 0
    
    for idx, path in test_files:
        print("=" * 80)
        print(f"Test Case {idx}: {os.path.basename(path)}")
        print("=" * 80)
        try:
            with open(path, "rb") as f:
                content = f.read()
                
            df, original_headers = parse_bom_to_df(content, os.path.basename(path))
            if df is None:
                print("❌ FAILED: parse_bom_to_df returned None")
                failure_count += 1
                continue
                
            print(f"Columns: {list(df.columns)}")
            mapping = identify_columns(df, vdb_search_func=qdrant_db.search)
            print(f"Mapping: {mapping}")
            
            # Print a quick sample of the mapped columns
            print("\nFirst row sample:")
            for col_type in ['mpn', 'manufacturer', 'description', 'qty', 'customer_part', 'internal_part']:
                col_idx = mapping.get(col_type, -1)
                if col_idx >= 0 and col_idx < len(df.columns):
                    val = df.iloc[0].iloc[col_idx]
                    print(f"  {col_type.upper()}: {val}")
                else:
                    print(f"  {col_type.upper()}: Not Mapped (-1)")
            
            success_count += 1
            print("✅ SUCCESS")
        except Exception as e:
            print(f"❌ FAILED with error: {e}")
            import traceback
            traceback.print_exc()
            failure_count += 1
        print()
        
    print("=" * 80)
    print(f"Summary: {success_count} Passed, {failure_count} Failed.")
    print("=" * 80)

if __name__ == "__main__":
    main()
