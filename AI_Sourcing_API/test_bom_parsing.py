import pandas as pd
import json
import sys
import os

from utils import parse_bom_to_df, identify_columns

def is_valid_mpn_token(token: str) -> bool:
    """Validate if a token is a real part number, excluding common value/unit ratings and SMD packages."""
    import re
    token_upper = token.strip().upper()
    if not token_upper or token_upper in ("NAN", "NONE", "-", "N/A"):
        return False
        
    # An individual token must be single cohesive and cannot contain spaces
    if ' ' in token_upper:
        return False
        
    # Must contain at least one digit OR be purely alphabetic or clean alphabetic/alphanumeric with symbols of length >= 3.
    if not any(c.isdigit() for c in token_upper):
        cleaned = re.sub(r'[\-_/\.:]', '', token_upper)
        if not (cleaned.isalpha() and len(cleaned) >= 3):
            return False
    if not any(c.isalpha() for c in token_upper):
        cleaned = re.sub(r'[\-_/\.:]', '', token_upper)
        if not (cleaned.isdigit() and 3 <= len(cleaned) <= 12):
            return False
        
    # Exclude common technical values and unit ratings (e.g. 0.25W, 100V, 10K, 10UF, 1/4W, 50HZ)
    import re
    if re.match(r'^\d+(\.\d+)?(W|V|A|HZ|R|K|UF|PF|OHM|OHMS|%|DB|DBM)$', token_upper):
        return False
        
    # Exclude technical rating fractions
    if re.match(r'^\d+/\d+(W|V|A|HZ|R|K|UF|PF|OHM)?$', token_upper):
        return False
        
    # Exclude common SMD package sizes
    if token_upper in ('0201', '0402', '0603', '0805', '1206', '1210', '1812', '2010', '2512'):
        return False
        
    # Exclude generic labels
    if re.match(r'^(PART|ITEM|CODE|SEQ|PAGE|LINE|SNO|QTY|QUAN|PRICE|COST)\d*$', token_upper):
        return False
        
    return True

def is_valid_mpn_column_value(val: str) -> bool:
    """Validate if an extracted MPN column value is a real MPN or a description."""
    val_clean = val.strip()
    if not val_clean or val_clean.lower() in ("nan", "none", "-", "n/a"):
        return False
        
    # Must contain at least one digit OR be purely alphabetic or clean alphabetic/alphanumeric with symbols of length >= 3
    import re
    if not any(c.isdigit() for c in val_clean):
        cleaned = re.sub(r'[\-_/\.:]', '', val_clean.upper())
        if not (cleaned.isalpha() and len(cleaned) >= 3):
            return False
        
    # An MPN column value shouldn't have too many words (max 2 words allowed for cases like "MDO34 3-BW-350")
    words = val_clean.split()
    if len(words) > 2:
        return False
    if len(words) == 2:
        # Both words must contain digits to be a split-MPN (like "MDO34 3-BW-350")
        if not any(c.isdigit() for c in words[0]) or not any(c.isdigit() for c in words[1]):
            return False
        
    # Check if it contains common description terms
    desc_terms = {
        'resistor', 'capacitor', 'diode', 'transistor', 'connector', 'cable', 'wire', 
        'sensor', 'switch', 'terminal', 'block', 'relay', 'fuse', 'led', 'plug', 'socket',
        'ohm', 'ohms', 'volt', 'volts', 'watt', 'watts', 'amp', 'amps', 'hertz', 'hz'
    }
    val_lower = val_clean.lower()
    
    # Reject if it is exactly one of the description terms
    if val_lower in desc_terms:
        return False
        
    # If the value contains spaces, check if any description terms are present as whole words
    if ' ' in val_clean:
        import re
        desc_pattern = re.compile(r'\b(?:' + '|'.join(desc_terms) + r')\b', re.IGNORECASE)
        if desc_pattern.search(val_lower):
            return False
            
    return True

def run_test(filename):
    print(f"\n==========================================")
    print(f"TESTING FILE: {filename}")
    print(f"==========================================")
    bom_path = os.path.join("uploads", filename)
    if not os.path.exists(bom_path):
        print(f"File not found: {bom_path}")
        return

    with open(bom_path, 'rb') as f:
        content = f.read()

    df, original_headers = parse_bom_to_df(content, filename)
    print("Headers:", original_headers)
    
    mapping = identify_columns(df)
    print("Column Mapping:", mapping)

    for idx, row in df.iterrows():
        mpn = str(row.iloc[mapping.get('mpn', -1)]) if mapping.get('mpn', -1) >= 0 else ""
        desc = str(row.iloc[mapping.get('description', -1)]) if mapping.get('description', -1) >= 0 else ""
        qty = str(row.iloc[mapping.get('qty', -1)]) if mapping.get('qty', -1) >= 0 else "1"
        
        is_valid_mpn = is_valid_mpn_column_value(mpn)
                
        print(f"\nRow {idx+1}:")
        print(f"  Extracted MPN: '{mpn}' (valid: {is_valid_mpn})")
        print(f"  Extracted Desc: '{desc}'")
        
        if not is_valid_mpn and mpn.strip():
            mpn_clean = mpn.strip()
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
                # Split on '|' always, or '/' only if it has whitespace on at least one side (e.g. ' /' or '/ ')
                # to avoid splitting unified MPNs like '10076/XMP' or 'MC34063A/D'.
                if '|' in val_str:
                    parts = [p.strip() for p in val_str.split('|') if p.strip()]
                elif re.search(r'\s+/\s*|\s*/\s+', val_str):
                    parts = [p.strip() for p in re.split(r'\s+/\s*|\s*/\s+', val_str) if p.strip()]
                else:
                    parts = [val_str]
                    
                for p in parts:
                    if not mpn and re.match(r'^[A-Z0-9\-\./:]{5,}$', p.upper().replace(' ', '')) and is_valid_mpn_token(p):
                        mpn = p
                        print(f"  -> Found in Pass 1: '{mpn}'")
                    elif p != mpn:
                        desc = (desc + " " + p).strip() if desc else p
                if mpn: break
                
            # Pass 2
            if not mpn:
                for val in row.values:
                    val_str = str(val).strip()
                    if not val_str or val_str in ("nan", "None", ""): continue
                    tokens = val_str.split()
                    for token in tokens:
                        if re.match(r'^[A-Z0-9\-\./:]{5,}$', token.upper()) and is_valid_mpn_token(token):
                            mpn = token
                            print(f"  -> Found in Pass 2: '{mpn}'")
                            break
                    if mpn:
                        break

run_test("human_messy_bom.xlsx")
run_test("Sample10_RS.xlsx")
