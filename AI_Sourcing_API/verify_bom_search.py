import pandas as pd
import requests
import json
import os

def main():
    print("Creating synthetic test BOM Excel file...")
    # Create DataFrame
    df = pd.DataFrame({
        "Part Number / Description": ["TX Instr 10k res 0402"],
        "Quantity": [1]
    })
    
    test_file_path = "uploads/tx_test_bom.xlsx"
    os.makedirs("uploads", exist_ok=True)
    df.to_excel(test_file_path, index=False)
    print(f"BOM Excel created successfully at {test_file_path}")
    
    # Send POST request to sourcebuddy-app API server running on port 8005
    url = "http://localhost:8005/search-bom"
    
    print(f"Sending POST request to {url}...")
    try:
        with open(test_file_path, 'rb') as f:
            files = {'file': (os.path.basename(test_file_path), f, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}
            response = requests.post(url, files=files)
            
        print(f"HTTP Status: {response.status_code}")
        if response.status_code == 200:
            result_data = response.json()
            print("\n==========================================")
            print("SEARCH RESULTS SUMMARY")
            print("==========================================")
            results = result_data.get("results", [])
            for idx, row in enumerate(results):
                cust_part = row.get("customer_part_no") or "N/A"
                intl_part = row.get("internal_part") or "N/A"
                qty = row.get("qty") or "N/A"
                print(f"Row {idx+1} -> Cust Part: '{cust_part}', Internal Part: '{intl_part}', Qty: {qty}")
                
                # Print best match
                chunk = row.get("chunk") or {}
                conf = row.get("confidence_score")
                if chunk:
                    print(f"  🏆 BEST MATCH: MPN: {chunk.get('mpn')} | Mfr: {chunk.get('manufacturer')} | Conf: {conf}%")
                    print(f"     Description: {chunk.get('description')}")
                    
                # Print all 5 candidates in all_sourcelists
                print("\n  📚 ALL CANDIDATES FOUND IN CATALOG:")
                sourcelist = row.get("all_sourcelists", [])
                for s_idx, cand in enumerate(sourcelist):
                    c_conf = cand.get("confidence_score")
                    c_score = cand.get("score")
                    c_chunk = cand.get("chunk") or {}
                    c_mpn = c_chunk.get("mpn") or cand.get("mpn")
                    c_mfr = c_chunk.get("manufacturer") or cand.get("manufacturer")
                    c_desc = c_chunk.get("description") or cand.get("description")
                    print(f"    [{s_idx+1}] MPN: {c_mpn} | Mfr: {c_mfr} | Conf: {c_conf}% (Raw Score: {c_score:.4f})")
                    print(f"        Desc: {c_desc[:90]}...")
            print("==========================================")
        else:
            print("Error details:", response.text)
    except Exception as e:
        print(f"Failed to query endpoint: {e}")

if __name__ == "__main__":
    main()
