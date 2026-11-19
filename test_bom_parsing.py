import pandas as pd
import json
import sys
import os

sys.path.append("AI_Sourcing_API")
from utils import parse_bom_to_df, identify_columns

bom_path = r"uploads/human_messy_bom.xlsx"
if not os.path.exists(bom_path):
    print(f"File not found: {bom_path}")
    sys.exit(1)

with open(bom_path, 'rb') as f:
    content = f.read()

df, original_headers = parse_bom_to_df(content, "human_messy_bom.xlsx")
print("Headers:", original_headers)
print("DF columns:", list(df.columns))
print("First row values:")
print(df.iloc[0].to_dict())

mapping = identify_columns(df)
print("Column Mapping:", mapping)

for idx, row in df.iterrows():
    mpn = str(row.iloc[mapping.get('mpn', -1)]) if mapping.get('mpn', -1) >= 0 else ""
    desc = str(row.iloc[mapping.get('description', -1)]) if mapping.get('description', -1) >= 0 else ""
    qty = str(row.iloc[mapping.get('qty', -1)]) if mapping.get('qty', -1) >= 0 else "1"
    
    mpn_clean = mpn.strip()
    is_valid_mpn = False
    if mpn_clean and mpn_clean.lower() not in ("nan", "none", "-", "n/a"):
        has_spaces = ' ' in mpn_clean
        has_digits = any(c.isdigit() for c in mpn_clean)
        if not has_spaces and has_digits:
            is_valid_mpn = True
            
    print(f"\nRow {idx+1}:")
    print(f"  Extracted MPN: '{mpn}' (valid: {is_valid_mpn})")
    print(f"  Extracted Desc: '{desc}'")
    
    if not is_valid_mpn and mpn_clean:
        if mpn_clean not in desc:
            desc = f"{desc} {mpn_clean}".strip()
        mpn = ""
        print(f"  [After reset] MPN: '{mpn}', Desc: '{desc}'")
        
    import re
    if not mpn:
        # Pass 1
        for val in row.values:
            val_str = str(val).strip()
            if not val_str or val_str in ("nan", "None", ""): continue
            if '|' in val_str or '/' in val_str:
                delims = ['|', '/']
                for d in delims:
                    if d in val_str:
                        parts = [p.strip() for p in val_str.split(d) if p.strip()]
                        for p in parts:
                            if not mpn and re.match(r'^[A-Z0-9\-\./:]{5,}$', p.upper().replace(' ', '')) and any(c.isdigit() for c in p):
                                mpn = p
                                print(f"  -> Found in Pass 1: '{mpn}'")
                            elif p != mpn:
                                desc = (desc + " " + p).strip() if desc else p
                        break
            if mpn: break
            
        # Pass 2
        if not mpn:
            for val in row.values:
                val_str = str(val).strip()
                if not val_str or val_str in ("nan", "None", ""): continue
                tokens = val_str.split()
                for token in tokens:
                    if re.match(r'^[A-Z0-9\-\./:]{5,}$', token.upper()) and any(c.isdigit() for c in token):
                        if not re.match(r'^(PART|ITEM|CODE|SEQ)\d*$', token.upper()):
                            mpn = token
                            print(f"  -> Found in Pass 2: '{mpn}'")
                            break
                if mpn:
                    break
