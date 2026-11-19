
import os
import json
import hashlib
import pickle
import io
import time
import csv
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from enum import Enum
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity

from fastapi import FastAPI, File, UploadFile, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

# Image processing for OCR
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("⚠️  PIL not installed. Install with: pip install pillow")

try:
    import pytesseract
    PYTESSERACT_AVAILABLE = True
except ImportError:
    PYTESSERACT_AVAILABLE = False
    print("⚠️  pytesseract not installed. Install with: pip install pytesseract")
    print("     Also install Tesseract-OCR from: https://github.com/UB-Mannheim/tesseract/wiki")

# PDF and Word Document Parsing
try:
    import pdfplumber
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False
    print("⚠️  pdfplumber not installed. Install with: pip install pdfplumber")

try:
    from docx import Document
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False
    print("⚠️  python-docx not installed. Install with: pip install python-docx")

# ─────────────────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────
def normalize_text(text: str) -> str:
    """Normalize text for comparison."""
    if not text:
        return ""
    return str(text).lower().strip()

def is_quantity_like(text: str) -> bool:
    """Check if text is likely a quantity field."""
    text = normalize_text(text)
    if not text:
        return False
    if text.isdigit() or len(text) <= 3:
        return True
    return False

def clean_search_query(query: str) -> str:
    """
    Clean a search query, especially if it's a pasted row from Excel/BOM.
    Removes SNo, Qty, and other unrelated fragments.
    """
    if not query: return ""
    
    # Check for common delimiters if it looks like a pasted row
    delimiters = ['\t', '|']
    if any(d in query for d in delimiters):
        parts = []
        for d in delimiters:
            if d in query:
                parts = [p.strip() for p in query.split(d) if p.strip()]
                break
        
        if parts:
            cleaned_parts = []
            for p in parts:
                # Skip small numbers (likely SNo or Qty)
                if p.isdigit() and len(p) <= 3: continue
                # Skip common unrelated words
                if p.lower() in ['qty', 'quantity', 'project', 'project_qty', 'uom', 'ea', 'nos', 'sno', 'sl no']: continue
                cleaned_parts.append(p)
            
            if cleaned_parts:
                return " ".join(cleaned_parts).replace(" & ", " and ")
                
    return query.strip().replace(" & ", " and ")


def standardize_wire_row(row_series, block_mapping, columns) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Detects if a row contains automotive wire/cable specifications,
    and reconstructs the correct MPN, Description, and Quantity to prevent column swapping.
    Strictly yields to standard parts with valid non-gauge alphanumeric part numbers.
    Preserves and formats: Spec Standard, Wire Gauge, Gauge Units, Insulation Type, Wire Class, and Wire Color.
    Formats description exactly as: Wire, {Standard}, {Gauge}{Unit}, {Insulation}, Class {Class}, Color {Color}
    """
    import re
    
    # Extract values from row series
    row_vals = []
    if hasattr(row_series, 'values'):
        row_vals = [str(val).strip() for val in row_series.values]
    else:
        row_vals = [str(val).strip() for val in row_series]
        
    # Safety Check: If the row has a standard alphanumeric part number (non-gauge) in the mapped MPN column,
    # yield completely to ensure zero impact on existing functionality.
    if block_mapping:
        mpn_idx = block_mapping.get("mpn", -1)
        if mpn_idx >= 0 and mpn_idx < len(row_vals):
            # Check if this column is actually a wire spec/standard column rather than a real part number
            is_wire_spec_col = False
            if columns is not None and mpn_idx < len(columns):
                col_name = str(columns[mpn_idx]).lower()
                if "wire" in col_name or "spec" in col_name or "standard" in col_name:
                    is_wire_spec_col = True
            
            if not is_wire_spec_col:
                mpn_val = row_vals[mpn_idx].strip()
                # Standard parts have length >= 5, alphanumeric, and not purely numeric/gauge
                if len(mpn_val) >= 5 and any(c.isalpha() for c in mpn_val) and any(c.isdigit() for c in mpn_val):
                    # Ensure it is not a wire type itself or common wire standard names
                    if not any(wk in mpn_val.upper() for wk in ["FLRXX", "FLRY", "TXL", "GXL", "SXL", "ISO", "SAE", "JASO", "DIN", "FLY"]):
                        return None, None, None
        
    # 1. Identify wire standard (e.g. ISO 6722)
    wire_standard = None
    wire_standard_idx = -1
    for i, val in enumerate(row_vals):
        val_upper = val.upper().strip()
        if any(std in val_upper for std in ["ISO", "SAE", "JASO", "DIN", "EN", "UL"]):
            wire_standard = val.strip()
            wire_standard_idx = i
            break
            
    # 2. Identify wire type (insulation type)
    wire_type = None
    wire_type_idx = -1
    wire_keywords = ["FLRXX", "FLRY", "TXL", "GXL", "SXL", "FLY", "FLR", "FLR51Y"]
    for i, val in enumerate(row_vals):
        val_upper = val.upper().strip()
        if any(wk in val_upper for wk in wire_keywords):
            for token in val_upper.split():
                if any(wk in token for wk in wire_keywords):
                    wire_type = token
                    wire_type_idx = i
                    break
            if wire_type:
                break
                
    # 3. Identify wire gauge units
    wire_unit = "mm²"
    wire_unit_idx = -1
    for i, val in enumerate(row_vals):
        val_upper = val.upper().strip()
        if val_upper in ["MM²", "MM2", "AWG"] or "MM" in val_upper:
            wire_unit = val.strip().lower()
            if "mm" in wire_unit:
                wire_unit = "mm²"
            wire_unit_idx = i
            break

    # 4. Identify wire color
    wire_color = None
    wire_color_idx = -1
    color_codes = {"RD", "BK", "BU", "BN", "GN", "YE", "GY", "WH", "VT", "OG", "PK", "GD", "SR", "BE", "BR", "OR", "VI", "GR"}
    color_names = {"RED", "BLACK", "BLUE", "BROWN", "GREEN", "YELLOW", "GREY", "GRAY", "WHITE", "VIOLET", "ORANGE", "PINK"}
    color_pattern = re.compile(r'^[A-Z]{2,4}([-/][A-Z]{2,4})?$', re.IGNORECASE)
    
    for i, val in enumerate(row_vals):
        if i in [wire_standard_idx, wire_type_idx, wire_unit_idx]:
            continue
        val_upper = val.upper().strip()
        
        # Check standard names or standard codes
        if val_upper in color_names or val_upper in color_codes:
            wire_color = val.strip()
            wire_color_idx = i
            break
            
        # Check composite codes (like BE-BK)
        if color_pattern.match(val_upper):
            parts = re.split(r'[-/]', val_upper)
            if all(p in color_codes or len(p) <= 3 for p in parts):
                wire_color = val.strip()
                wire_color_idx = i
                break

    # 5. Identify wire class
    wire_class = None
    wire_class_idx = -1
    for i, val in enumerate(row_vals):
        if i in [wire_standard_idx, wire_type_idx, wire_unit_idx, wire_color_idx]:
            continue
        val_clean = val.strip()
        if len(val_clean) == 1 and val_clean.isalpha() and val_clean.isupper():
            is_class_col = False
            if columns is not None and i < len(columns):
                col_name = str(columns[i]).lower()
                if "class" in col_name:
                    is_class_col = True
            if is_class_col or val_clean in ["A", "B", "C", "D", "E", "F", "G", "H"]:
                wire_class = val_clean
                wire_class_idx = i
                break

    # 6. Identify wire gauge (cross-section size)
    wire_size = None
    wire_size_idx = -1
    common_wire_sizes = {0.13, 0.14, 0.22, 0.25, 0.35, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 16.0, 25.0, 35.0, 50.0, 70.0, 95.0, 120.0}
    
    for i, val in enumerate(row_vals):
        if i in [wire_standard_idx, wire_type_idx, wire_unit_idx, wire_color_idx, wire_class_idx]:
            continue
            
        # Safety Guard: Skip quantity columns to prevent matching Qty (like 50) as wire size
        if columns is not None and i < len(columns):
            col_name = str(columns[i]).lower().strip()
            qty_kws = ["qty", "quantity", "qnt", "count", "amount", "total", "needed", "moq", "project"]
            if any(k in col_name for k in qty_kws):
                continue
                
        val_clean = val.replace(',', '').strip()
        if re.match(r'^\d+(\.\d+)?$', val_clean) and val_clean:
            try:
                f_val = float(val_clean)
                if f_val in common_wire_sizes or (f_val > 0.1 and f_val < 15.0 and (f_val * 100) % 25 == 0):
                    wire_size = val
                    wire_size_idx = i
                    break
            except ValueError:
                continue
                
    if wire_size and wire_type:
        # Reconstruct MPN
        if wire_color:
            mpn = f"{wire_type} {wire_size} {wire_color}"
        else:
            mpn = f"{wire_type} {wire_size}"
            
        # Reconstruct Description exactly as: Wire, {Standard}, {Gauge}{Unit}, {Insulation}, Class {Class}, Color {Color}
        desc_parts = ["Wire"]
        if wire_standard:
            desc_parts.append(wire_standard)
            
        unit_label = wire_unit if wire_unit else "mm²"
        desc_parts.append(f"{wire_size}{unit_label}")
        desc_parts.append(wire_type)
        
        if wire_class:
            desc_parts.append(f"Class {wire_class}")
        if wire_color:
            desc_parts.append(f"Color {wire_color}")
            
        desc = ", ".join(desc_parts)
        
        # 7. Find quantity in the remaining cells
        qty = "1"
        for i, val in enumerate(row_vals):
            if i in [wire_size_idx, wire_type_idx, wire_color_idx, wire_standard_idx, wire_class_idx, wire_unit_idx]:
                continue
            val_clean = val.replace(',', '').strip()
            if re.match(r'^\d+(\.\d+)?$', val_clean) and val_clean:
                try:
                    f_val = float(val_clean)
                    if f_val > 0 and i != 0: 
                        qty = val
                        break
                except ValueError:
                    continue
                    
        return mpn, desc, qty
        
    return None, None, None



def extract_qty_and_uom(qty_str: str) -> Tuple[float, str]:
    """
    Splits a quantity string like '50mtr', '100m', '10pcs' into numeric quantity and standardized UOM.
    Returns (qty_numeric, standardized_uom).
    """
    import re
    qty_str = str(qty_str).strip().lower()
    if not qty_str or qty_str == "nan":
        return 1.0, "pcs"
        
    # Match numeric part and unit part
    match = re.match(r'^([\d\.]+)\s*([a-zA-Z\s\./]+)?$', qty_str)
    if match:
        num_str = match.group(1)
        unit_str = match.group(2) if match.group(2) else ""
        unit_str = unit_str.strip()
        
        try:
            qty_val = float(num_str)
        except ValueError:
            qty_val = 1.0
            
        # Standardize UOM
        uom = "pcs"
        if unit_str:
            if any(u in unit_str for u in ["mtr", "meter", "meters", "metre"]) or unit_str == "m":
                uom = "mtr"
            elif any(u in unit_str for u in ["pc", "pcs", "piece", "pieces", "each", "nos"]) or unit_str == "no":
                uom = "pcs"
            elif unit_str in ["l", "ltr", "ltrs", "liter", "liters", "litre", "litres"]:
                uom = "ltr"
            elif unit_str in ["kg", "kgs", "kilogram", "kilograms"]:
                uom = "kg"
            elif unit_str in ["g", "gm", "gms", "gram", "grams"]:
                uom = "g"
            elif unit_str in ["ft", "feet"]:
                uom = "ft"
            elif unit_str in ["in", "inch", "inches"]:
                uom = "in"
            elif unit_str in ["reel", "reels"]:
                uom = "reel"
            elif unit_str in ["roll", "rolls"]:
                uom = "roll"
            elif unit_str in ["pack", "packs", "pkg", "pkgs", "package", "packages"]:
                uom = "pack"
            elif unit_str in ["box", "boxes"]:
                uom = "box"
            elif unit_str in ["bag", "bags"]:
                uom = "bag"
            elif unit_str in ["set", "sets"]:
                uom = "set"
            else:
                uom = unit_str
        return qty_val, uom
        
    # Fallback if no match
    try:
        clean_num = re.sub(r'[^\d\.]', '', qty_str)
        qty_val = float(clean_num) if clean_num else 1.0
    except ValueError:
        qty_val = 1.0
    return qty_val, "pcs"


def check_if_sheet_source_in_excel(file_bytes: bytes, filename: str) -> bool:
    """
    Checks if the raw uploaded excel/csv spreadsheet explicitly contained a column named "Sheet Source".
    """
    ext = filename.lower().split('.')[-1]
    if ext not in ['xlsx', 'xls']:
        return False
    try:
        import io
        import pandas as pd
        xls_dict = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None, header=None, dtype=str)
        for _, df_sheet in xls_dict.items():
            if df_sheet.empty:
                continue
            # Look at first 20 rows for header keywords
            for _, row in df_sheet.head(20).iterrows():
                for val in row.values:
                    val_str = str(val).lower().strip() if pd.notna(val) else ""
                    if val_str == "sheet source":
                        return True
    except Exception:
        pass
    return False


def standardize_dataframe(df: pd.DataFrame, original_had_sheet_source: bool = False) -> pd.DataFrame:
    """
    Standardizes any parsed DataFrame to a clean, production-grade flat schema:
    - Part Number (MPN)
    - Manufacturer
    - Description
    - Quantity
    - UOM (new standardized Unit of Measure column)
    - Customer Part No.
    - Internal Part Number
    - Category
    Eliminates "Sheet Source" unless explicitly requested or provided in the original Excel file.
    """
    import pandas as pd
    if df is None or df.empty:
        return df
        
    # Run column identification on the input DataFrame
    mapping = identify_columns(df)
    
    # Extract UOM from quantity column header if specified
    qty_col_idx = mapping.get('qty', -1)
    header_uom = None
    if qty_col_idx >= 0 and qty_col_idx < len(df.columns):
        qty_col_name = str(df.columns[qty_col_idx]).lower().strip()
        import re
        # Match standard UOM words first
        match_uom = re.search(r'\b(mtrs?|meters?|metres?|pcs|pieces?|each|nos|feet|ft|inches?|kgs?|kilograms?|ltrs?|liters?|litres?|reels?|rolls?|packs?|pkgs?|boxes?|bags?|sets?)\b', qty_col_name)
        if match_uom:
            uom_word = match_uom.group(1)
            if any(u in uom_word for u in ["mtr", "meter", "metre"]):
                header_uom = "mtr"
            elif any(u in uom_word for u in ["pc", "piece", "each", "nos"]):
                header_uom = "pcs"
            elif uom_word in ["ft", "feet"]:
                header_uom = "ft"
            elif "inch" in uom_word:
                header_uom = "in"
            elif any(u in uom_word for u in ["kg", "kilogram"]):
                header_uom = "kg"
            elif any(u in uom_word for u in ["ltr", "liter", "litre"]):
                header_uom = "ltr"
            elif any(u in uom_word for u in ["reel"]):
                header_uom = "reel"
            elif any(u in uom_word for u in ["roll"]):
                header_uom = "roll"
            elif any(u in uom_word for u in ["pack", "pkg"]):
                header_uom = "pack"
            elif any(u in uom_word for u in ["box"]):
                header_uom = "box"
            elif any(u in uom_word for u in ["bag"]):
                header_uom = "bag"
            elif any(u in uom_word for u in ["set"]):
                header_uom = "set"
        
        # Check specifically for a lone 'm' (indicating meter), e.g. "qty (m)" or "qty in m"
        if not header_uom:
            if re.search(r'\b(m)\b', qty_col_name):
                header_uom = "mtr"
                
        # Check specifically for a lone 'l' (indicating liter), e.g. "qty (l)" or "qty in l"
        if not header_uom:
            if re.search(r'\b(l)\b', qty_col_name):
                header_uom = "ltr"
                
        # Check specifically for "in" as a UOM (inches) e.g., "qty (in)", "qty in inches"
        if not header_uom:
            if re.search(r'\((in)\)|\[(in)\]|\binches?\b', qty_col_name):
                header_uom = "in"
                
    standardized_records = []
    
    for idx, row in df.iterrows():
        # Check if it is a wire row first
        wire_mpn, wire_desc, wire_qty = standardize_wire_row(row, mapping, df.columns)
        
        if wire_mpn:
            mpn = wire_mpn
            desc = wire_desc
            qty = wire_qty
            mfg = ""
            cust_part = ""
            intl_part = ""
            category = "Wires"
        else:
            mpn = str(row.iloc[mapping.get('mpn', -1)]) if mapping.get('mpn', -1) >= 0 else ""
            mfg = str(row.iloc[mapping.get('manufacturer', -1)]) if mapping.get('manufacturer', -1) >= 0 else ""
            desc = str(row.iloc[mapping.get('description', -1)]) if mapping.get('description', -1) >= 0 else ""
            qty = str(row.iloc[mapping.get('qty', -1)]) if mapping.get('qty', -1) >= 0 else "1"
            cust_part = str(row.iloc[mapping.get('customer_part', -1)]) if mapping.get('customer_part', -1) >= 0 else ""
            intl_part = str(row.iloc[mapping.get('internal_part', -1)]) if mapping.get('internal_part', -1) >= 0 else ""
            category = str(row.iloc[mapping.get('commodity', -1)]) if mapping.get('commodity', -1) >= 0 else "General"
            
        # Clean mfg names
        mfg_clean = mfg.strip()
        if mfg_clean.lower() in ("nan", "none", "null"):
            mfg_clean = ""
            
        # Clean mpn
        mpn_clean = mpn.strip()
        if mpn_clean.lower() in ("nan", "none", "null"):
            mpn_clean = ""
            
        # Process Quantity & UOM
        qty_num, uom_val = extract_qty_and_uom(qty)
        
        # Decide if the item is a wire based on description or MPN keywords present in the Excel row
        is_wire = False
        desc_lower = desc.lower()
        # Avoid matching prepended section titles like "Wire Table - " or "Wires - "
        has_wire_keyword = "wire" in desc_lower and not any(header in desc_lower for header in ["wire table -", "wire list -", "cable table -", "wires -"])
        if has_wire_keyword or "automotive wire" in desc_lower:
            # Safety check: Ensure it's not a mechanical clip or other non-wire components
            non_wire_words = ["clip", "connector", "terminal", "wedge", "seal", "plug", "bracket", "clamp", "tape", "sleeve", "tube", "tie", "mount", "cap", "housing", "contact", "pin", "socket"]
            if not any(w in desc_lower for w in non_wire_words):
                is_wire = True
        if any(wk in mpn_clean.upper() for wk in ["FLRXX", "FLRY", "TXL", "GXL", "SXL"]):
            is_wire = True
            
        if is_wire:
            # Force wire rows to be categorized under Wires
            category = "Wires"
            # Get UOM based on the quantity header unit or default to mtr
            if header_uom:
                uom_val = header_uom
            else:
                uom_val = "mtr"
        else:
            # If uom_val is "pcs" (the default), check row-specific UOM column or Quantity column header UOM
            if uom_val == "pcs":
                if mapping.get('uom', -1) >= 0:
                    row_uom = str(row.iloc[mapping.get('uom', -1)]).strip()
                    if row_uom and row_uom.lower() not in ("nan", "none", "null", ""):
                        # Parse row_uom using our extract function
                        _, parsed_row_uom = extract_qty_and_uom(f"1 {row_uom}")
                        if parsed_row_uom and parsed_row_uom != "pcs":
                            uom_val = parsed_row_uom
                
                # If still "pcs", fallback to header UOM if found
                if uom_val == "pcs" and header_uom:
                    uom_val = header_uom
        
        record = {
            "Part Number (MPN)": mpn_clean,
            "Manufacturer": mfg_clean,
            "Description": desc.strip(),
            "Quantity": qty_num,
            "UOM": uom_val,
            "Customer Part No.": cust_part.strip(),
            "Internal Part Number": intl_part.strip(),
            "Category": category.strip()
        }
        
        if "Sheet Source" in df.columns and original_had_sheet_source:
            record["Sheet Source"] = str(row.get("Sheet Source", ""))
            
        standardized_records.append(record)
        
    return pd.DataFrame(standardized_records)


# ─────────────────────────────────────────────────────────────────────────────
# COLUMN IDENTIFICATION
# ─────────────────────────────────────────────────────────────────────────────
def identify_columns(columns_or_df, data_sample: List[List[str]] = None, vdb_search_func = None) -> Dict[str, int]:
    """
    Heuristically identify column indices for MPN, Manufacturer, Description, Qty, etc.
    Uses Strong Exact Matches (Stage 0), Header keywords (Stage 1), Data Content analysis (Stage 2),
    and Vector DB feedback (Stage 3) for highly accurate mapping.
    """
    if hasattr(columns_or_df, 'columns'):
        # It's a DataFrame
        columns = list(columns_or_df.columns)
        data_sample = columns_or_df.head(5).values.tolist()
        if len(columns) == 1:
            return {
                "mpn": -1, "manufacturer": -1, "description": -1, "qty": -1,
                "internal_part": -1, "customer_part": -1, "commodity": -1, "uom": -1,
                "unrelated_indices": []
            }
    else:
        columns = columns_or_df
    mapping = {
        "mpn": -1,
        "manufacturer": -1,
        "description": -1,
        "qty": -1,
        "internal_part": -1,
        "customer_part": -1,
        "commodity": -1,
        "uom": -1,
        "unrelated_indices": []
    }
    strong_mapped_indices = set()
    def is_col_empty(col_idx: int) -> bool:
        if not data_sample or col_idx < 0:
            return True
        for row in data_sample:
            if col_idx < len(row):
                val = str(row[col_idx]).strip()
                if val and val.lower() not in ("nan", "none", "null", ""):
                    return False
        return True
    
    # Keyword sets for each field
    sno_keywords = ['sno', 's.no', 'sl no', 'sl.no', 'index', 'idx', 'item #', '#', 'line', 'item no', 'item_no']
    internal_keywords = ['input - rs stock', 'rs stock', 'rs stock number', 'internal', 'article', 'our pn', 'our_pn', 'rs stock no', 'rs part no', 'material number', 'material_number', 'material no', 'material_no', 'material code', 'material_code', 'internal part', 'internal_part', 'material', 'part id', 'part_id', 'item id', 'item_id', 'article code', 'article_code', 'article no', 'article_no', 'article number', 'article_number']
    mpn_keywords = ['mpn', 'mfr part', 'mfg part', 'manufacturer part', 'supplier part', 'spec', 'specification', 'specifications', 'part number', 'part_number', 'mfr. code', 'mfr code', 'mfr_code', 'mfr.ref', 'mfr ref', 'mfr_ref', 'mfr. part no.', 'mfr. part no', 'mfr part no', 'mfg part no', 'manufacturer part number', 'mfg.ref', 'mfg. code', 'mfg code', 'mfg_code', 'device', 'material number', 'material_number', 'material no', 'material_no', 'material', 'material code', 'material_code', 'part id', 'part_id', 'item id', 'item_id', 'article code', 'article_code', 'article', 'article number', 'article_number']
    mfg_keywords = ['manufacturer', 'manufactuer', 'mfr', 'brand', 'mfg', 'make', 'maker', 'mfr name', 'mfg name', 'mfr.name', 'mfg.name']
    desc_keywords = ['description', 'desc', 'item', 'part name', 'details', 'item description', 'item_description', 'application', 'appl']
    qty_keywords = ['qty', 'quantity', 'qnt', 'project qty', 'project_qty', 'total needed', 'project q', 'project_q', 'moq', 'minimum order quantity']
    customer_keywords = ['customer pn', 'customer_pn', 'customer part', 'cust part', 'customer_part_no', 'cust pn', 'customer part no', 'customer part no.']
    commodity_keywords = ['commodity', 'category', 'family', 'group', 'type', 'section', 'class', 'subsection', 'supersection']
    uom_keywords = ['uom', 'unit', 'measure', 'um']

    # Stage -1: Prioritize columns containing "input" to handle consolidated reports
    has_input_cols = any('input' in str(c).lower() for c in columns)
    if has_input_cols:
        for i, col in enumerate(columns):
            if not col: continue
            col_norm = str(col).lower().strip()
            
            # Check for input columns first
            if 'input' in col_norm:
                # Map to internal_part
                if any(kw in col_norm for kw in internal_keywords) and mapping["internal_part"] == -1:
                    mapping["internal_part"] = i
                    continue
                # Map to customer_part
                if any(kw in col_norm for kw in customer_keywords) and mapping["customer_part"] == -1:
                    mapping["customer_part"] = i
                    continue
                # Map to mpn
                if any(kw in col_norm for kw in mpn_keywords) and mapping["mpn"] == -1:
                    mapping["mpn"] = i
                    continue
                # Map to manufacturer
                if any(kw in col_norm for kw in mfg_keywords) and mapping["manufacturer"] == -1:
                    mapping["manufacturer"] = i
                    continue
                # Map to description
                if any(kw in col_norm for kw in desc_keywords) and mapping["description"] == -1:
                    mapping["description"] = i
                    continue
                # Map to qty
                if any(kw in col_norm for kw in qty_keywords) and mapping["qty"] == -1:
                    mapping["qty"] = i
                    continue
                # Map to commodity
                if any(kw in col_norm for kw in commodity_keywords) and mapping["commodity"] == -1:
                    mapping["commodity"] = i
                    continue
                # Map to uom
                if any(kw in col_norm for kw in uom_keywords) and mapping["uom"] == -1:
                    mapping["uom"] = i
                    continue

    # Stage 0: Strong Exact Matches (Highest Priority)
    for i, col in enumerate(columns):
        if not col: continue
        col_norm = str(col).lower().strip()
        
        # Exact MPN Match
        if col_norm in ['mpn', 'mfr p/n', 'mfr. p/n', 'mfr. p/n.', 'mfg p/n', 'mfg p/n.', 'mfr part no', 'mfr part no.', 'mfr. part no.', 'part number', 'part_number', 'part no', 'part_no', 'specification', 'specifications', 'spec', 'mfr.ref', 'mfr ref', 'mfr_ref', 'mfr. code', 'mfr code', 'mfr_code', 'code', 'value']:
            if mapping["mpn"] == -1:
                mapping["mpn"] = i
                strong_mapped_indices.add(i)
                continue
                
        if col_norm in ['manufacturer', 'manufactuer', 'mfr.name', 'mfr name', 'mfg name', 'mfg.name', 'brand', 'maker', 'make', 'mfr. name']:
            if mapping["manufacturer"] == -1 or (is_col_empty(mapping["manufacturer"]) and not is_col_empty(i)):
                mapping["manufacturer"] = i
                strong_mapped_indices.add(i)
                continue
                
        # Exact Description Match
        if col_norm in ['description', 'desc', 'item description', 'item_description', 'material desc.', 'material desc', 'details', 'application', 'appl']:
            if mapping["description"] == -1:
                mapping["description"] = i
                strong_mapped_indices.add(i)
                continue

        # Exact Quantity Match
        if col_norm in ['qty', 'quantity', 'project qty', 'project_qty', 'moq', 'minimum order quantity', 'count']:
            if mapping["qty"] == -1:
                mapping["qty"] = i
                strong_mapped_indices.add(i)
                continue

        # Exact Customer Part Match
        if col_norm in ['customer pn', 'customer_pn', 'customer part', 'cust part', 'customer_part_no', 'cust pn', 'customer part no', 'customer part no.']:
            if mapping["customer_part"] == -1:
                mapping["customer_part"] = i
                strong_mapped_indices.add(i)
                continue

        # Exact Internal Part Match
        if col_norm in ['input - rs stock', 'rs stock', 'rs stock number', 'internal', 'article', 'our pn', 'our_pn', 'rs stock no', 'rs part no', 'material number', 'material_number', 'material no', 'material_no', 'material code', 'material_code', 'internal part', 'internal_part', 'material', 'part id', 'part_id', 'item id', 'item_id', 'article code', 'article_code', 'article no', 'article_no', 'article number', 'article_number']:
            if mapping["internal_part"] == -1:
                mapping["internal_part"] = i
                strong_mapped_indices.add(i)
                continue
    
    # Stage 1: Header Keyword Matching (Fallback)
    for i, col in enumerate(columns):
        if not col: continue
        col_norm = str(col).lower().strip()
        
        # Skip columns already mapped in Stage 0
        if i in (mapping["mpn"], mapping["manufacturer"], mapping["description"], mapping["qty"], mapping["internal_part"], mapping["customer_part"], mapping["commodity"], mapping["uom"]):
            continue
        
        # Priority for internal part
        if any(kw == col_norm for kw in internal_keywords) and mapping["internal_part"] == -1:
            mapping["internal_part"] = i
            continue

        if any(kw in col_norm for kw in customer_keywords) and mapping["customer_part"] == -1:
            mapping["customer_part"] = i
            continue

        if any(kw in col_norm for kw in mpn_keywords) and mapping["mpn"] == -1:
            mapping["mpn"] = i
        elif any(kw in col_norm for kw in mfg_keywords) and (mapping["manufacturer"] == -1 or (is_col_empty(mapping["manufacturer"]) and not is_col_empty(i))):
            mapping["manufacturer"] = i
        elif any(kw in col_norm for kw in desc_keywords) and mapping["description"] == -1:
            mapping["description"] = i
        elif any(kw in col_norm for kw in qty_keywords) and mapping["qty"] == -1:
            mapping["qty"] = i
        elif any(kw in col_norm for kw in commodity_keywords) and mapping["commodity"] == -1:
            mapping["commodity"] = i
        elif any(kw in col_norm for kw in uom_keywords) and mapping["uom"] == -1:
            mapping["uom"] = i
            
        # Track unrelated columns for query filtering
        if any(kw == col_norm or kw + " #" == col_norm for kw in sno_keywords):
            mapping["unrelated_indices"].append(i)
        elif any(kw in col_norm for kw in qty_keywords):
            mapping["unrelated_indices"].append(i)
        elif any(kw in col_norm for kw in uom_keywords):
            mapping["unrelated_indices"].append(i)
        elif any(kw in col_norm for kw in ['price', 'cost', 'total', 'amount', 'ext price', 'unit price']):
            mapping["unrelated_indices"].append(i)

    # Stage 2: Data-Aware Heuristics (Refining based on values)
    if data_sample and len(data_sample) > 0:
        import re
        for i in range(len(columns)):
            # Skip if already mapped
            if i in (mapping["mpn"], mapping["manufacturer"], mapping["description"], mapping["qty"], mapping["internal_part"], mapping["customer_part"], mapping["commodity"], mapping["uom"]):
                continue
                
            # Sample values from this column
            sample_values = [str(row[i]) for row in data_sample if i < len(row) and row[i]]
            if not sample_values: continue
            
            # 1. Check if it looks like Quantity (Mostly numeric)
            numeric_hits = sum(1 for v in sample_values if re.match(r'^\d+(\.\d+)?$', v.replace(',', '')))
            is_numeric = (numeric_hits / len(sample_values)) > 0.7 if sample_values else False
            
            col_norm = str(columns[i]).lower().strip()
            forbidden_qty_headers = ['id', 'code', 'number', 'sno', 'sl.no', 'price', 'cost', 'index', 'part_id', 'product_id', 'seq', 'serial', 'part no', 'partno']
            is_forbidden_qty = any(h == col_norm or h in col_norm for h in forbidden_qty_headers)
            
            # Additional safety: typical BOM quantities are small integers (mostly < 5000)
            try:
                avg_val = sum(float(v.replace(',', '')) for v in sample_values) / len(sample_values)
                is_small_avg = avg_val < 5000
            except Exception:
                is_small_avg = True
            
            # CRITICAL FIX: If this column is already an 'unrelated_index' or already mapped, NEVER use it for Qty
            if is_numeric and mapping["qty"] == -1 and i not in mapping["unrelated_indices"] and not is_forbidden_qty and is_small_avg:
                mapping["qty"] = i
                continue # If it's qty, it's unlikely to be anything else
            
            avg_len = sum(len(v) for v in sample_values) / len(sample_values)
            
            # 2. Check if it looks like an MPN (Alphanumeric, complex, no spaces)
            mpn_hits = sum(1 for v in sample_values if re.match(r'^[A-Z0-9\-\./:]{5,}$', v.upper().replace(' ', '')) and any(c.isdigit() for c in v))
            is_mpn_like = (mpn_hits / len(sample_values)) > 0.5 if sample_values else False
            
            if is_mpn_like and mapping["mpn"] == -1 and avg_len < 30:
                mapping["mpn"] = i

            # 3. Check if it's a long description
            if avg_len >= 30 and mapping["description"] == -1:
                mapping["description"] = i

    # Stage 3: Vector DB Feedback (The "AI" way)
    # If we are still missing MPN or Description, or we want to be sure, test a few samples
    if vdb_search_func and data_sample and len(data_sample) > 0:
        for i in range(len(columns)):
            # Skip columns already identified
            if i in (mapping["mpn"], mapping["manufacturer"], mapping["description"], mapping["qty"], mapping["internal_part"], mapping["customer_part"], mapping["commodity"], mapping["uom"]):
                continue
                
            # Sample first 3 values
            samples = [str(row[i]) for row in data_sample if i < len(row) and row[i]][:3]
            if not samples: continue
            
            mpn_matches = 0
            desc_matches = 0
            
            for val in samples:
                if len(val) < 4: continue
                # Search this value in VDB
                results = vdb_search_func(val, limit=3)
                for res in results:
                    payload = res.get('payload', {})
                    # If this value appears in 'mpn' field of VDB
                    if val.lower() in str(payload.get('mpn', '')).lower():
                        mpn_matches += 1
                    # If this value appears in 'description' field of VDB
                    if val.lower() in str(payload.get('description', '')).lower():
                        desc_matches += 1
            
            # Vote based on matches
            if mpn_matches > desc_matches and mapping["mpn"] == -1:
                mapping["mpn"] = i
                print(f"  🤖 AI Mapping: Column {i} ('{columns[i]}') mapped to MPN via Vector DB feedback")
            elif desc_matches > mpn_matches and mapping["description"] == -1:
                mapping["description"] = i
                print(f"  🤖 AI Mapping: Column {i} ('{columns[i]}') mapped to Description via Vector DB feedback")

    # Stage 4: Content-Aware Guard and Alignment Verification (Prevent Wire swaps)
    if data_sample and len(data_sample) > 0:
        # Check mapped MPN column
        mpn_idx = mapping.get("mpn", -1)
        if mpn_idx >= 0:
            sample_vals = [str(row[mpn_idx]).strip() for row in data_sample if mpn_idx < len(row) and row[mpn_idx]]
            import re
            is_wire_size = False
            if sample_vals:
                wire_size_hits = sum(1 for v in sample_vals if re.match(r'^\d+(\.\d+)?$', v.replace(',', '')) and float(v.replace(',', '')) < 150)
                if wire_size_hits / len(sample_vals) > 0.7:
                    is_wire_size = True
            
            if is_wire_size and mpn_idx not in strong_mapped_indices:
                # Demote wire size from MPN
                mapping["mpn"] = -1
                
        # Check mapped Quantity column
        qty_idx = mapping.get("qty", -1)
        if qty_idx >= 0:
            sample_vals = [str(row[qty_idx]).strip().upper() for row in data_sample if qty_idx < len(row) and row[qty_idx]]
            is_wire_type = False
            wire_keywords = ["FLRXX", "FLRY", "TXL", "GXL", "SXL", "FLY", "FLR"]
            if sample_vals:
                wire_type_hits = sum(1 for v in sample_vals if any(wk in v for wk in wire_keywords))
                if wire_type_hits / len(sample_vals) > 0.5:
                    is_wire_type = True
            
            if is_wire_type and qty_idx not in strong_mapped_indices:
                # Demote wire type from Quantity
                mapping["qty"] = -1
                if mapping.get("commodity", -1) == -1:
                    mapping["commodity"] = qty_idx

    # Fallback: if MPN column is not found but an internal part / material column is found,
    # use it as MPN so that we have an active part number for queries and matching.
    if mapping["mpn"] == -1 and mapping["internal_part"] != -1:
        mapping["mpn"] = mapping["internal_part"]

    return mapping

def parse_bom_to_df(file_bytes: bytes, filename: str) -> Tuple[Optional[pd.DataFrame], List[str]]:
    """Parse BOM bytes into a pandas DataFrame and return original headers."""
    ext = filename.lower().split('.')[-1]
    try:
        if ext in ['xlsx', 'xls']:
            xls_dict = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None, header=None, dtype=str)
            if not xls_dict:
                return None, []
            
            header_keywords = ['sno', 'sl no', 'part number', 'mpn', 'manufacturer', 'mfr', 'brand', 'mfg', 'vendor', 'make', 'maker', 'description', 'desc', 'qty', 'quantity', 'details', 'uom', 'part', 'item', 'code', 'value']
            valid_dfs = []
            
            for sheet_name, df_sheet in xls_dict.items():
                if df_sheet.empty or len(df_sheet) < 1:
                    continue
                
                # --- NEW: GROUPED / SECTIONED BOM PARSING SUPPORT ---
                # Scan all rows of this sheet to detect if it has grouped/sectioned blocks
                header_rows = []
                section_rows = {} # maps header_row_idx -> section_title
                current_section_title = ""
                
                for idx, row in df_sheet.iterrows():
                    # 1. Test if it's a Header Row
                    # Must match at least 3 unique header keywords to avoid false positives on data rows.
                    # It must also not contain raw numeric data like serial numbers or quantities (e.g. "1", "10") which are absent in headers.
                    score = 0
                    non_null_count = 0
                    matched_kws = set()
                    has_numeric_data = False
                    
                    for val in row.values:
                        if not pd.notna(val):
                            continue
                        val_str = str(val).strip()
                        val_lower = val_str.lower()
                        if val_lower and val_lower not in ("nan", "none", ""):
                            non_null_count += 1
                            
                            # Standard data rows contain pure numeric quantities or serial numbers
                            if val_str.isdigit() and len(val_str) <= 4:
                                has_numeric_data = True
                                
                            for kw in header_keywords:
                                if kw == val_lower or (len(kw) >= 4 and kw in val_lower):
                                    if kw not in matched_kws:
                                        matched_kws.add(kw)
                                        score += 1
                                        break
                    
                    is_header = (score >= 3) and not has_numeric_data
                    if is_header:
                        header_rows.append(idx)
                        if current_section_title:
                            section_rows[idx] = current_section_title
                            current_section_title = ""
                        continue
                        
                    # 2. Test if it's a Section Title Row (exactly one non-empty string cell, no digits/numbers, len >= 3)
                    non_empty_vals = [str(val).strip() for val in row.values if pd.notna(val) and str(val).strip() not in ("nan", "none", "")]
                    if len(non_empty_vals) == 1:
                        val_str = non_empty_vals[0]
                        if len(val_str) >= 3 and any(c.isalpha() for c in val_str) and not any(c.isdigit() for c in val_str):
                            current_section_title = val_str

                # If we detected multiple separate header blocks, process as grouped BOM
                if len(header_rows) >= 2:
                    print(f"ℹ️ Detected Grouped/Sectioned BOM on sheet '{sheet_name}' with {len(header_rows)} sections.")
                    grouped_records = []
                    
                    for i, h_idx in enumerate(header_rows):
                        h_row = df_sheet.iloc[h_idx].fillna("").astype(str).tolist()
                        
                        # Build unique columns for mapping
                        unique_headers = []
                        for col_idx, col in enumerate(h_row):
                            col_name = col.strip() if col.strip() and col.strip() not in ("nan", "None") else f"Unnamed_{col_idx}"
                            original_col_name = col_name
                            counter = 1
                            while col_name in unique_headers:
                                col_name = f"{original_col_name}_{counter}"
                                counter += 1
                            unique_headers.append(col_name)
                            
                        # Find the boundary for this section's data rows
                        start_data_idx = h_idx + 1
                        end_data_idx = len(df_sheet)
                        
                        next_indices = [idx for idx in header_rows if idx > h_idx]
                        for next_h in next_indices:
                            # Section title is usually in the row immediately preceding the next header
                            potential_sec_idx = next_h - 1
                            if potential_sec_idx > h_idx:
                                end_data_idx = min(end_data_idx, potential_sec_idx)
                            else:
                                end_data_idx = min(end_data_idx, next_h)
                                
                        block_df = df_sheet.iloc[start_data_idx:end_data_idx].copy()
                        block_df.columns = unique_headers
                        block_df = block_df.dropna(how='all').reset_index(drop=True)
                        block_df = block_df.fillna("")
                        
                        if block_df.empty:
                            continue
                            
                        block_mapping = identify_columns(block_df)
                        if block_mapping["mpn"] < 0 and block_mapping["description"] < 0 and block_mapping["manufacturer"] < 0:
                            continue
                            
                        section_name = section_rows.get(h_idx, "")
                        
                        for _, row in block_df.iterrows():
                            row_vals = [str(v).strip() for v in row.values if pd.notna(v) and str(v).strip() not in ("nan", "none", "")]
                            if not row_vals or (len(row_vals) == 1 and row_vals[0] == section_name):
                                continue
                                
                            # Check if it's a wire row first!
                            wire_mpn, wire_desc, wire_qty = standardize_wire_row(row, block_mapping, block_df.columns)
                            if wire_mpn:
                                mpn = wire_mpn
                                desc = wire_desc
                                qty = wire_qty
                                mfg = ""
                                cust_part = ""
                                intl_part = ""
                            else:
                                mpn = str(row.iloc[block_mapping.get('mpn', -1)]) if block_mapping.get('mpn', -1) >= 0 else ""
                                desc = str(row.iloc[block_mapping.get('description', -1)]) if block_mapping.get('description', -1) >= 0 else ""
                                qty = str(row.iloc[block_mapping.get('qty', -1)]) if block_mapping.get('qty', -1) >= 0 else "1"
                                mfg = str(row.iloc[block_mapping.get('manufacturer', -1)]) if block_mapping.get('manufacturer', -1) >= 0 else ""
                                cust_part = str(row.iloc[block_mapping.get('customer_part', -1)]) if block_mapping.get('customer_part', -1) >= 0 else ""
                                intl_part = str(row.iloc[block_mapping.get('internal_part', -1)]) if block_mapping.get('internal_part', -1) >= 0 else ""
                            
                            if not mpn.strip() and not desc.strip() and not mfg.strip():
                                continue
                                
                            if section_name and not wire_mpn: # Skip prepending for wires as they are already descriptive
                                desc_clean = desc.strip()
                                if desc_clean:
                                    desc = f"{section_name} - {desc_clean}"
                                else:
                                    desc = section_name
                                    
                            grouped_records.append({
                                "Part Number (MPN)": mpn,
                                "Manufacturer": mfg,
                                "Description": desc,
                                "Quantity": qty,
                                "Customer Part No.": cust_part,
                                "Internal Part Number": intl_part,
                                "Category": section_name,
                                "Sheet Source": sheet_name
                            })
                            
                    if grouped_records:
                        df_sheet_flat = pd.DataFrame(grouped_records)
                        df_sheet_flat = df_sheet_flat.fillna("")
                        
                        if len(xls_dict) == 1:
                            return df_sheet_flat, list(df_sheet_flat.columns)
                            
                        valid_dfs.append(df_sheet_flat)
                        continue

                # Find best header row for this sheet
                best_row_idx = 0
                max_score = -1
                for idx, row in df_sheet.head(20).iterrows():
                    score = 0
                    non_null_count = 0
                    for val in row.values:
                        val_str = str(val).lower().strip() if pd.notna(val) else ""
                        if val_str and val_str not in ("nan", "none", ""):
                            non_null_count += 1
                            if any(kw == val_str for kw in header_keywords) or any(kw in val_str for kw in header_keywords if len(kw) >= 4):
                                score += 1
                    
                    total_score = score * 3 + non_null_count
                    if total_score > max_score:
                        max_score = total_score
                        best_row_idx = idx
                
                # Set the header
                new_header = df_sheet.iloc[best_row_idx].fillna("").astype(str).tolist()
                unique_headers = []
                for i, col in enumerate(new_header):
                    col_name = col.strip() if col.strip() and col.strip() not in ("nan", "None") else f"Unnamed_{i}"
                    original_col_name = col_name
                    counter = 1
                    while col_name in unique_headers:
                        col_name = f"{original_col_name}_{counter}"
                        counter += 1
                    unique_headers.append(col_name)
                    
                df_sheet.columns = unique_headers
                df_sheet = df_sheet.iloc[best_row_idx + 1:]
                df_sheet = df_sheet.dropna(how='all').reset_index(drop=True)
                df_sheet = df_sheet.fillna("")
                
                if df_sheet.empty:
                    continue
                
                sheet_mapping = identify_columns(df_sheet)
                
                # Valid if we found at least one major key
                if sheet_mapping["mpn"] >= 0 or sheet_mapping["description"] >= 0 or sheet_mapping["manufacturer"] >= 0:
                    if len(xls_dict) == 1:
                        # Single sheet Excel: preserve original column names for beautiful UI
                        return df_sheet, list(df_sheet.columns)
                        
                    # Standardize names for multi-sheet alignment
                    rename_map = {}
                    standard_names = {
                        "mpn": "Part Number (MPN)",
                        "manufacturer": "Manufacturer",
                        "description": "Description",
                        "qty": "Quantity",
                        "customer_part": "Customer Part No.",
                        "internal_part": "Internal Part Number",
                        "commodity": "Category",
                        "uom": "UOM"
                    }
                    for key, std_name in standard_names.items():
                        idx = sheet_mapping.get(key, -1)
                        if idx >= 0:
                            rename_map[df_sheet.columns[idx]] = std_name
                            
                    df_sheet = df_sheet.rename(columns=rename_map)
                    df_sheet["Sheet Source"] = sheet_name
                    valid_dfs.append(df_sheet)
            
            if valid_dfs:
                df = pd.concat(valid_dfs, ignore_index=True)
                df = df.fillna("")
                return df, list(df.columns)
            else:
                # Fallback to first sheet
                first_sheet_name = list(xls_dict.keys())[0]
                df_sheet = xls_dict[first_sheet_name]
                # Default header detection
                best_row_idx = 0
                max_score = -1
                for idx, row in df_sheet.head(20).iterrows():
                    score = 0
                    non_null_count = 0
                    for val in row.values:
                        val_str = str(val).lower().strip() if pd.notna(val) else ""
                        if val_str and val_str not in ("nan", "none", ""):
                            non_null_count += 1
                            if any(kw == val_str for kw in header_keywords) or any(kw in val_str for kw in header_keywords if len(kw) >= 4):
                                score += 1
                    total_score = score * 3 + non_null_count
                    if total_score > max_score:
                        max_score = total_score
                        best_row_idx = idx
                new_header = df_sheet.iloc[best_row_idx].fillna("").astype(str).tolist()
                unique_headers = []
                for i, col in enumerate(new_header):
                    col_name = col.strip() if col.strip() and col.strip() not in ("nan", "None") else f"Unnamed_{i}"
                    original_col_name = col_name
                    counter = 1
                    while col_name in unique_headers:
                        col_name = f"{original_col_name}_{counter}"
                        counter += 1
                    unique_headers.append(col_name)
                df_sheet.columns = unique_headers
                df_sheet = df_sheet.iloc[best_row_idx + 1:]
                df_sheet = df_sheet.dropna(how='all').reset_index(drop=True)
                df_sheet = df_sheet.fillna("")
                return df_sheet, list(df_sheet.columns)
                
        elif ext == 'csv':
            # Try to detect delimiter
            content = file_bytes.decode('utf-8', errors='replace')
            first_line = content.split('\n')[0]
            sep = ','
            if ';' in first_line and first_line.count(';') > first_line.count(','):
                sep = ';'
            elif '\t' in first_line:
                sep = '\t'
            df = pd.read_csv(io.BytesIO(file_bytes), sep=sep, header=None, dtype=str)
        elif ext == 'pdf':
            try:
                import pdfplumber
                rows = []
                with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                    for page in pdf.pages:
                        tables = page.extract_tables()
                        if tables:
                            for table in tables:
                                for row in table:
                                    cols = [str(c).strip() if c else "" for c in row]
                                    if any(cols): rows.append(cols)
                    if not rows:
                        for page in pdf.pages:
                            text = page.extract_text()
                            
                            # Fallback to OCR if text is empty or very short (scanned PDF)
                            if not text or len(text.strip()) < 50:
                                try:
                                    import pytesseract
                                    img = page.to_image(resolution=300).original
                                    ocr_text = pytesseract.image_to_string(img)
                                    if ocr_text:
                                        text = ocr_text
                                except Exception as e:
                                    print(f"OCR failed: {e}")
                                    
                            if text:
                                for line in text.split('\n'):
                                    if line.strip():
                                        import re
                                        rows.append([c.strip() for c in re.split(r'\s{2,}', line)])
                if not rows: return None, []
                df = pd.DataFrame(rows)
            except Exception as e:
                print(f"PDF Parse Error: {e}")
                return None, []
        else:
            # Fallback for txt or others
            content = file_bytes.decode('utf-8', errors='replace')
            lines = [l.strip().split('|') for l in content.split('\n') if l.strip()]
            if not lines: return None, []
            df = pd.DataFrame(lines)

        if df is None or df.empty:
            return None, []

        # Find header row
        header_keywords = ['sno', 'sl no', 'part number', 'mpn', 'manufacturer', 'mfr', 'brand', 'mfg', 'vendor', 'make', 'maker', 'description', 'desc', 'qty', 'quantity', 'details', 'uom', 'part', 'item', 'code', 'value']
        best_row_idx = 0
        max_score = -1
        
        for idx, row in df.head(20).iterrows():
            score = 0
            non_null_count = 0
            for val in row.values:
                val_str = str(val).lower().strip() if pd.notna(val) else ""
                if val_str and val_str not in ("nan", "none", ""):
                    non_null_count += 1
                    if any(kw == val_str for kw in header_keywords) or any(kw in val_str for kw in header_keywords if len(kw) >= 4):
                        score += 1
            
            total_score = score * 3 + non_null_count
            if total_score > max_score:
                max_score = total_score
                best_row_idx = idx

        # Set the header
        new_header = df.iloc[best_row_idx].fillna("").astype(str).tolist()
        # Make column names unique to prevent pandas issues
        unique_headers = []
        for i, col in enumerate(new_header):
            col_name = col.strip() if col.strip() and col.strip() not in ("nan", "None") else f"Unnamed_{i}"
            original_col_name = col_name
            counter = 1
            while col_name in unique_headers:
                col_name = f"{original_col_name}_{counter}"
                counter += 1
            unique_headers.append(col_name)
            
        df.columns = unique_headers
        # Drop rows up to the header
        df = df.iloc[best_row_idx + 1:]
        df = df.dropna(how='all').reset_index(drop=True)
        df = df.fillna("")
        return df, list(df.columns)
    except Exception as e:
        print(f"Error parsing BOM to DF: {e}")
        return None, []

# ─────────────────────────────────────────────────────────────────────────────
# BOM PARSER
# ─────────────────────────────────────────────────────────────────────────────
def parse_bom_bytes(file_bytes: bytes, filename: str = "") -> List[Dict]:
    """Parse multiple file formats: Excel, CSV, PDF, TXT, JSON."""
    file_ext = filename.lower().split(".")[-1] if filename else "xlsx"
    rows = []
    
    try:
        if file_ext in ["xlsx", "xls"]:
            # ONLY READ THE FIRST SHEET to prevent data leakage from other sheets
            df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0, dtype=str)
            rows.extend(_parse_dataframe_raw(df, include_headers=True))
        
        elif file_ext == "csv":
            try:
                df = pd.read_csv(io.BytesIO(file_bytes), dtype=str)
            except:
                df = pd.read_csv(io.BytesIO(file_bytes), dtype=str, delimiter=";")
            rows.extend(_parse_dataframe_raw(df, include_headers=True))
        
        elif file_ext in ["txt", "tsv"]:
            content = file_bytes.decode("utf-8", errors="replace")
            lines = content.strip().split("\n")
            for line in lines:
                if not line.strip():
                    continue
                
                cols = None
                if "|" in line:
                    cols = [c.strip() for c in line.split("|")]
                elif "\t" in line:
                    cols = [c.strip() for c in line.split("\t")]
                elif "," in line:
                    cols = [c.strip() for c in line.split(",")]
                else:
                    import re
                    cols = [c.strip() for c in re.split(r"\s{2,}", line)]
                
                if cols:
                    cols = [c for c in cols if c]
                    if len(cols) >= 2:
                        part = {
                            "raw_row": "|".join(cols),
                            "columns": cols,
                            "num_columns": len(cols),
                        }
                        rows.append(part)
        
        elif file_ext == "pdf":
            try:
                import pdfplumber
                with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                    for page in pdf.pages:
                        tables = page.extract_tables()
                        if tables:
                            for table in tables:
                                if len(table) > 1:
                                    for row in table[1:]:
                                        cols = [str(c).strip() for c in row if c and str(c).strip()]
                                        if len(cols) >= 2:
                                            part = {
                                                "raw_row": "|".join(cols),
                                                "columns": cols,
                                                "num_columns": len(cols),
                                            }
                                            rows.append(part)
                    
                    if not rows:
                        for page in pdf.pages:
                            text = page.extract_text()
                            if text:
                                lines = text.strip().split("\n")
                                for line in lines:
                                    if not line.strip():
                                        continue
                                    
                                    cols = None
                                    if "|" in line:
                                        cols = [c.strip() for c in line.split("|")]
                                    elif "\t" in line:
                                        cols = [c.strip() for c in line.split("\t")]
                                    elif "," in line:
                                        cols = [c.strip() for c in line.split(",")]
                                    else:
                                        import re
                                        cols = [c.strip() for c in re.split(r"\s{2,}", line)]
                                    
                                    if cols and len(cols) >= 2:
                                        non_empty_count = sum(1 for c in cols if c)
                                        if non_empty_count >= 2:
                                            part = {
                                                "raw_row": "|".join(cols),
                                                "columns": cols,
                                                "num_columns": len(cols),
                                            }
                                            rows.append(part)
            except ImportError:
                raise Exception("pdfplumber not installed. Install with: pip install pdfplumber")
        
        elif file_ext == "json":
            try:
                data = json.loads(file_bytes.decode("utf-8"))
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            cols = [str(v).strip() for v in item.values() if v]
                            if len(cols) >= 2:
                                part = {
                                    "raw_row": "|".join(cols),
                                    "columns": cols,
                                    "num_columns": len(cols),
                                }
                                rows.append(part)
            except Exception as e:
                raise Exception(f"Error parsing JSON: {e}")
    
    except Exception as e:
        raise Exception(f"Error parsing file: {e}")
    
    return rows

def _parse_dataframe_raw(df: pd.DataFrame, include_headers: bool = False) -> List[Dict]:
    """Convert DataFrame rows into pipe-separated format."""
    rows = []
    
    if include_headers:
        # Prepend headers as the first row
        header_cols = [str(c).strip() for c in df.columns]
        rows.append({
            "raw_row": "|".join(header_cols),
            "columns": header_cols,
            "num_columns": len(header_cols),
        })
        
    df = df.dropna(how="all").reset_index(drop=True)
    
    for _, row in df.iterrows():
        # Keep empty columns so indices remain consistent
        cols = [str(v).strip() if pd.notna(v) and str(v).strip() not in ("nan", "None") else "" for v in row.values]
        
        # Add all rows that have at least one non-empty value (we'll filter in routers)
        if any(c for c in cols):
            rows.append({
                "raw_row": "|".join(cols),
                "columns": cols,
                "num_columns": len(cols),
            })
    
    return rows

# ─────────────────────────────────────────────────────────────────────────────
# COLUMN-BASED SEARCH
# ─────────────────────────────────────────────────────────────────────────────
def column_search(bom_part: Dict, chunks: List[Dict], top_k: int = 5) -> List[Dict]:
    """Column-based search with exact matching."""
    results = []
    
    bom_columns = bom_part.get("columns", [])
    if not bom_columns:
        raw_row = bom_part.get("raw_row", "")
        bom_columns = [c.strip() for c in raw_row.split("|") if c.strip()]
    
    # Filter out qty-like columns
    search_columns = []
    for col in bom_columns:
        if not is_quantity_like(col):
            search_columns.append(normalize_text(col))
    
    if not search_columns:
        return []
    
    # Search each chunk
    for chunk in chunks:
        matched_columns_list = []
        column_match_count = 0
        
        for search_col in search_columns:
            if not search_col:
                continue
            
            # Check MPN
            chunk_mpn = normalize_text(chunk.get("mpn", ""))
            if chunk_mpn and search_col in chunk_mpn:
                matched_columns_list.append(("BOM_Column", "MPN", search_col, chunk_mpn))
                column_match_count += 1
                continue
            
            # Check Manufacturer
            chunk_mfr = normalize_text(chunk.get("manufacturer", ""))
            if chunk_mfr and search_col in chunk_mfr:
                matched_columns_list.append(("BOM_Column", "Manufacturer", search_col, chunk_mfr))
                column_match_count += 1
                continue
            
            # Check Description
            chunk_desc = normalize_text(chunk.get("description", ""))
            if chunk_desc and search_col in chunk_desc:
                matched_columns_list.append(("BOM_Column", "Description", search_col, chunk_desc))
                column_match_count += 1
                continue
            
            # Check Specs
            spec_vals = chunk.get("spec_values", {})
            found_in_spec = False
            for spec_name, spec_val in spec_vals.items():
                spec_val_norm = normalize_text(spec_val)
                if search_col in spec_val_norm:
                    matched_columns_list.append(("BOM_Column", f"Spec:{spec_name}", search_col, spec_val))
                    column_match_count += 1
                    found_in_spec = True
                    break
            
            if found_in_spec:
                continue
            
            # Check Sellers
            sellers = chunk.get("sellers", [])
            found_in_seller = False
            for seller in sellers:
                seller_norm = normalize_text(seller)
                if search_col in seller_norm:
                    matched_columns_list.append(("BOM_Column", "Seller", search_col, seller))
                    column_match_count += 1
                    found_in_seller = True
                    break
            
            if found_in_seller:
                continue
            
            # Check Similar Parts
            similar_parts = chunk.get("similar_parts", [])
            for similar in similar_parts:
                similar_norm = normalize_text(similar)
                if search_col in similar_norm:
                    matched_columns_list.append(("BOM_Column", "Similar_Part", search_col, similar))
                    column_match_count += 1
                    break
        
        if column_match_count > 0:
            matched_fields = list(set([m[1] for m in matched_columns_list]))
            results.append({
                "score": column_match_count,
                "chunk": chunk,
                "matched_fields": matched_fields,
                "matched_columns": matched_columns_list,
                "columns_matched": column_match_count,
            })
    
    results = sorted(results, key=lambda x: (x["columns_matched"], x["score"]), reverse=True)
    return results[:top_k]

def batch_column_search(bom_parts: List[Dict], chunks: List[Dict], top_k: int = 5) -> List[List[Dict]]:
    """Batch column-based search."""
    all_results = []
    for bom_part in bom_parts:
        results = column_search(bom_part, chunks, top_k)
        all_results.append(results)
    return all_results

# ─────────────────────────────────────────────────────────────────────────────
# IMAGE HANDLING & OCR
# ─────────────────────────────────────────────────────────────────────────────

def extract_ocr_text_from_image(image_bytes: bytes) -> str:
    """Extract text from image using OCR."""
    if not PIL_AVAILABLE or not PYTESSERACT_AVAILABLE:
        return ""
    
    try:
        image = Image.open(io.BytesIO(image_bytes))
        # Extract text using Tesseract OCR
        text = pytesseract.image_to_string(image)
        return text.strip()
    except Exception as e:
        print(f"OCR error: {e}")
        return ""

def extract_images_from_excel(file_bytes: bytes, filename: str = "") -> Dict[int, Tuple[bytes, str, str]]:
    """Extract ALL embedded images from Excel file. Returns {row_number: (image_bytes, filename, ocr_text)}"""
    if not PIL_AVAILABLE:
        return {}
    
    images_by_row = {}
    try:
        from openpyxl import load_workbook
        from openpyxl.drawing.image import Image as XLImage
        import zipfile
        
        # Method 1: Try extracting from worksheet drawings
        try:
            wb = load_workbook(io.BytesIO(file_bytes))
            ws = wb.active
            
            # Access _drawing which contains the drawing objects
            if hasattr(ws, '_drawing') and ws._drawing is not None:
                drawing = ws._drawing
                if hasattr(drawing, 'image') and drawing.image:
                    for idx, img_rel in enumerate(drawing.image):
                        try:
                            # Get image data from relationship
                            image_bytes = img_rel.blob if hasattr(img_rel, 'blob') else img_rel
                            if isinstance(image_bytes, bytes):
                                image_filename = f"drawing_image_{idx}.png"
                                ocr_text = extract_ocr_text_from_image(image_bytes)
                                images_by_row[idx] = (image_bytes, image_filename, ocr_text)
                                print(f"✅ Extracted image from drawing {idx}")
                        except Exception as e:
                            print(f"⚠️  Could not extract drawing image {idx}: {e}")
        except Exception as e:
            print(f"⚠️  Drawing extraction failed: {e}")
        
        # Method 2: Extract from Excel package (ZIP archive)
        try:
            # Excel files are ZIP archives
            with zipfile.ZipFile(io.BytesIO(file_bytes), 'r') as zip_ref:
                # List all files in the archive
                file_list = zip_ref.namelist()
                
                # Find image files
                image_files = [f for f in file_list if f.startswith('xl/media/') or f.startswith('ppt/media/')]
                
                for img_idx, img_file in enumerate(image_files):
                    try:
                        # Read image from archive
                        image_bytes = zip_ref.read(img_file)
                        image_filename = os.path.basename(img_file)
                        ocr_text = extract_ocr_text_from_image(image_bytes)
                        
                        # Assign to row (use index if we don't have row info)
                        images_by_row[img_idx] = (image_bytes, image_filename, ocr_text)
                        print(f"✅ Extracted embedded image: {image_filename}")
                    except Exception as e:
                        print(f"⚠️  Could not extract image from archive {img_file}: {e}")
        except Exception as e:
            print(f"⚠️  Archive extraction failed: {e}")
        
        print(f"📸 Total images extracted: {len(images_by_row)}")
        return images_by_row
    except Exception as e:
        print(f"❌ Image extraction error: {e}")
        return {}

def save_image_and_get_link(image_bytes: bytes, filename: str, row_number: int) -> Tuple[str, str]:
    """Save image to disk and return local path and base64 data URL."""
    if not PIL_AVAILABLE:
        return "", ""
    
    try:
        # Create images directory if it doesn't exist
        images_dir = Path("uploaded_images")
        images_dir.mkdir(exist_ok=True)
        
        # Generate unique filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        image_filename = f"row_{row_number}_{timestamp}_{filename}"
        image_path = images_dir / image_filename
        
        # Save image
        with open(image_path, "wb") as f:
            f.write(image_bytes)
        
        # Generate base64 data URL (data:image/png;base64,...)
        import base64
        base64_str = base64.b64encode(image_bytes).decode('utf-8')
        
        # Detect image format from filename or default to png
        if filename.lower().endswith(('.jpg', '.jpeg')):
            mime_type = "image/jpeg"
        elif filename.lower().endswith('.gif'):
            mime_type = "image/gif"
        elif filename.lower().endswith('.webp'):
            mime_type = "image/webp"
        else:
            mime_type = "image/png"
        
        # Create data URL
        data_url = f"data:{mime_type};base64,{base64_str}"
        
        # Also create a web URL for direct access
        web_url = f"data:{mime_type};base64,{base64_str}"
        
        return str(image_path), web_url
    except Exception as e:
        print(f"Image save error: {e}")
        return "", ""

# ─────────────────────────────────────────────────────────────────────────────
# DOCUMENT PARSING FUNCTIONS - For Excel, PDF, Word
# ─────────────────────────────────────────────────────────────────────────────

def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract text from PDF file."""
    if not PDF_AVAILABLE:
        return ""
    
    try:
        text_content = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_content.append(page_text)
        
        return "\n".join(text_content)
    except Exception as e:
        print(f"Error extracting text from PDF: {str(e)}")
        return ""

def extract_text_from_docx(file_bytes: bytes) -> str:
    """Extract text from Word (.docx) file."""
    if not DOCX_AVAILABLE:
        return ""
    
    try:
        doc = Document(io.BytesIO(file_bytes))
        text_content = []
        
        for para in doc.paragraphs:
            if para.text.strip():
                text_content.append(para.text)
        
        # Also extract text from tables
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join([cell.text for cell in row.cells])
                if row_text.strip():
                    text_content.append(row_text)
        
        return "\n".join(text_content)
    except Exception as e:
        print(f"Error extracting text from DOCX: {str(e)}")
        return ""

def extract_text_from_excel(file_bytes: bytes, filename: str) -> str:
    """Extract text from Excel file."""
    try:
        xl = pd.ExcelFile(io.BytesIO(file_bytes))
        text_content = []
        
        for sheet in xl.sheet_names:
            df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet, dtype=str)
            
            # Convert DataFrame to text
            for idx, row in df.iterrows():
                row_values = [str(v).strip() for v in row if pd.notna(v)]
                if row_values:
                    text_content.append(" | ".join(row_values))
        
        return "\n".join(text_content)
    except Exception as e:
        print(f"Error extracting text from Excel: {str(e)}")
        return ""

def extract_text_from_json(file_bytes: bytes, filename: str) -> str:
    """Extract text from JSON file."""
    try:
        json_str = file_bytes.decode("utf-8")
        json_data = json.loads(json_str)
        
        text_content = []
        
        # Function to recursively convert JSON to readable text
        def json_to_text(obj, depth=0):
            indent = "  " * depth
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if isinstance(value, (dict, list)):
                        text_content.append(f"{indent}{key}:")
                        json_to_text(value, depth + 1)
                    else:
                        text_content.append(f"{indent}{key}: {value}")
            elif isinstance(obj, list):
                for idx, item in enumerate(obj):
                    if isinstance(item, (dict, list)):
                        text_content.append(f"{indent}[{idx}]:")
                        json_to_text(item, depth + 1)
                    else:
                        text_content.append(f"{indent}[{idx}]: {item}")
            else:
                text_content.append(f"{indent}{obj}")
        
        json_to_text(json_data)
        return "\n".join(text_content)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON format in {filename}: {str(e)}")
        return ""
    except Exception as e:
        print(f"Error extracting text from JSON: {str(e)}")
        return ""

def extract_chunks_from_document(doc_text: str, doc_name: str, source: str) -> List[Dict]:
    """Extract chunks from document text (PDF, Word, Excel, JSON)."""
    chunks = []
    
    if not doc_text or not doc_text.strip():
        return chunks
    
    # Split text into sentences/paragraphs for better chunking
    paragraphs = doc_text.split("\n")
    chunk_size = 3  # Group 3 lines per chunk
    
    for i in range(0, len(paragraphs), chunk_size):
        chunk_lines = paragraphs[i:i + chunk_size]
        chunk_text = " ".join([line.strip() for line in chunk_lines if line.strip()])
        
        if len(chunk_text.strip()) < 10:  # Skip very short chunks
            continue
        
        chunk_id = hashlib.md5(chunk_text.encode()).hexdigest()[:12]
        
        chunks.append({
            "chunk_id": chunk_id,
            "source": source,
            "mpn": "",
            "manufacturer": "",
            "description": chunk_text[:200],  # First 200 chars as description
            "availability": 0,
            "lead_days": "",
            "median_price_1000": None,
            "currency": "",
            "spec_values": {},
            "sellers": [],
            "similar_parts": [],
            "text": chunk_text,
            "document_name": doc_name,
        })
    
    return chunks

def extract_chunks_from_nexar(doc: Dict) -> List[Dict]:
    """Extract chunks from Nexar data."""
    chunks = []
    spec_name = doc.get("specification", "")
    doc_mfr = doc.get("manufacturer", "")
    
    for part in doc.get("Parts", []):
        mpn = part.get("mpn", spec_name)
        part_mfr = part.get("manufacturer", {}).get("name", doc_mfr)
        avail = part.get("totalAvail", 0)
        lead = part.get("estimatedFactoryLeadDays")
        mp = part.get("medianPrice1000") or {}
        price_k = mp.get("price")
        currency = mp.get("currency", "USD")
        
        text = f"Part Number: {mpn}. Manufacturer: {part_mfr}. Source database: Nexar_Data. Total availability: {avail} units."
        
        chunks.append({
            "chunk_id": hashlib.md5(text.encode()).hexdigest()[:12],
            "source": "Nexar_Data",
            "mpn": mpn,
            "manufacturer": part_mfr,
            "description": "",
            "availability": avail,
            "lead_days": lead,
            "median_price_1000": price_k,
            "currency": currency,
            "spec_values": {},
            "sellers": [],
            "similar_parts": [],
            "text": text,
        })
    
    return chunks

def extract_chunks_from_vendor(doc: Dict) -> List[Dict]:
    """Extract chunks from Vendor data."""
    chunks = []
    spec_name = doc.get("specification", "")
    doc_mfr = doc.get("manufacturer", "")
    
    for part in doc.get("Parts", []):
        mpn = part.get("mpn", spec_name)
        part_mfr = part.get("manufacturer", {}).get("name", doc_mfr)
        avail = part.get("totalAvail", 0)
        lead = part.get("estimatedFactoryLeadDays")
        others = part.get("others") or {}
        desc = others.get("Description", "")
        
        text = f"Part Number: {mpn}. Manufacturer: {part_mfr}. Source database: Vendor_Data. Description: {desc}."
        
        chunks.append({
            "chunk_id": hashlib.md5(text.encode()).hexdigest()[:12],
            "source": "Vendor_Data",
            "mpn": mpn,
            "manufacturer": part_mfr,
            "description": desc,
            "availability": avail,
            "lead_days": lead,
            "median_price_1000": None,
            "currency": "",
            "spec_values": {},
            "sellers": [],
            "similar_parts": [],
            "text": text,
        })
    
    return chunks

def extract_chunks_from_sample_data(filepath: str) -> List[Dict]:
    """Extract chunks generically from a delimited file like Sample_Data.txt."""
    chunks = []
    if not os.path.exists(filepath):
        return chunks
        
    try:
        import csv
        import os
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f, delimiter='^', quotechar='"')
            
            headers = None
            for row in reader:
                if not row:
                    continue
                if headers is None:
                    headers = [h.strip() for h in row]
                    continue
                    
                row_dict = {headers[i]: row[i] for i in range(min(len(headers), len(row)))}
                
                # Reconstruct the string generically in the requested format: "val1"^"val2"
                text = "^".join([f'"{v}"' for v in row])
                
                # Dynamically extract key fields if they exist
                mpn = ""
                mfr = ""
                desc = ""
                
                for k, v in row_dict.items():
                    k_lower = k.lower()
                    if "mpn" in k_lower or "part number" in k_lower or "partnumber" in k_lower:
                        mpn = v
                    elif "brand" in k_lower or "manufacturer" in k_lower or "mfr" in k_lower:
                        mfr = v
                    elif "description" in k_lower or "desc" in k_lower:
                        desc = v
                
                chunk = {
                    "chunk_id": hashlib.md5(text.encode()).hexdigest()[:12],
                    "source": os.path.basename(filepath),
                    "mpn": mpn,
                    "manufacturer": mfr,
                    "description": desc,
                    "availability": 0,
                    "lead_days": None,
                    "median_price_1000": None,
                    "currency": "",
                    "spec_values": row_dict,
                    "sellers": [],
                    "similar_parts": [],
                    "text": text,
                }
                # Add all properties directly into the chunk as requested
                chunk.update(row_dict)
                chunks.append(chunk)
    except Exception as e:
        print(f"Error parsing sample data: {e}")
        
    return chunks

def extract_chunks_from_delimited_bytes(file_bytes: bytes, filename: str) -> List[Dict]:
    """Extract chunks from a delimited byte stream like an uploaded Sample_Data.txt."""
    chunks = []
    try:
        import csv
        import io
        import hashlib
        text_content = file_bytes.decode('utf-8', errors='replace')
        
        # Use BytesIO and reader with proper newline handling
        f = io.BytesIO(file_bytes)
        
        # Try parsing with CSV reader - handle newlines in quoted fields
        try:
            # Reset to beginning
            f.seek(0)
            text_stream = io.TextIOWrapper(f, encoding='utf-8', errors='replace', newline='')
            reader = csv.reader(text_stream, delimiter='^', quotechar='"', skipinitialspace=True)
            
            headers = None
            for row in reader:
                if not row or all(not field.strip() for field in row):
                    continue
                if headers is None:
                    headers = [h.strip() for h in row]
                    continue
                    
                row_dict = {headers[i]: row[i].strip() if i < len(row) else "" 
                           for i in range(len(headers))}
                text = "^".join([f'"{v}"' for v in row])
                
                mpn = ""
                mfr = ""
                desc = ""
                for k, v in row_dict.items():
                    k_lower = k.lower()
                    if "mpn" in k_lower or "part number" in k_lower or "partnumber" in k_lower:
                        mpn = v
                    elif "brand" in k_lower or "manufacturer" in k_lower or "mfr" in k_lower:
                        mfr = v
                    elif "description" in k_lower or "desc" in k_lower:
                        desc = v
                
                chunk = {
                    "chunk_id": hashlib.md5(text.encode()).hexdigest()[:12],
                    "source": filename,
                    "mpn": mpn,
                    "manufacturer": mfr,
                    "description": desc,
                    "availability": 0,
                    "lead_days": None,
                    "median_price_1000": None,
                    "currency": "",
                    "spec_values": row_dict,
                    "sellers": [],
                    "similar_parts": [],
                    "text": text,
                }
                # Add all properties directly into the chunk as requested
                chunk.update(row_dict)
                chunks.append(chunk)
                
        except csv.Error as csv_err:
            # Fallback: Try simple line-based parsing if CSV parsing fails
            print(f"CSV parsing error, trying fallback parser: {csv_err}")
            lines = text_content.split('\n')
            headers = None
            for line in lines:
                if not line.strip():
                    continue
                fields = [f.strip() for f in line.split('^')]
                
                if headers is None:
                    headers = fields
                    continue
                    
                row_dict = {headers[i]: fields[i] if i < len(fields) else "" 
                           for i in range(len(headers))}
                text = "^".join([f'"{v}"' for v in fields])
                
                mpn = ""
                mfr = ""
                desc = ""
                for k, v in row_dict.items():
                    k_lower = k.lower()
                    if "mpn" in k_lower or "part number" in k_lower or "partnumber" in k_lower:
                        mpn = v
                    elif "brand" in k_lower or "manufacturer" in k_lower or "mfr" in k_lower:
                        mfr = v
                    elif "description" in k_lower or "desc" in k_lower:
                        desc = v
                
                chunk = {
                    "chunk_id": hashlib.md5(text.encode()).hexdigest()[:12],
                    "source": filename,
                    "mpn": mpn,
                    "manufacturer": mfr,
                    "description": desc,
                    "availability": 0,
                    "lead_days": None,
                    "median_price_1000": None,
                    "currency": "",
                    "spec_values": row_dict,
                    "sellers": [],
                    "similar_parts": [],
                    "text": text,
                }
                chunk.update(row_dict)
                chunks.append(chunk)
            
    except Exception as e:
        print(f"Error parsing delimited bytes: {e}")
        import traceback
        traceback.print_exc()
        
    return chunks

# ─────────────────────────────────────────────────────────────────────────────
# EXCEL EXPORT
# ─────────────────────────────────────────────────────────────────────────────
def build_excel_bytes(report: List[Dict]) -> bytes:
    """Build Excel report from search results."""
    wb = Workbook()
    ws = wb.active
    ws.title = "BOM Match Results"
    
    HEADERS = [
        "Row", "Customer Part No.", "Internal Part", "Description (Input)", 
        "Specification (MPN)", "Manufacturer", "Project Qty", "UOM",
        "Confidence %", "Matched MPN", "Matched Brand", "Matched Desc", "Source DB"
    ]
    
    for ci, h in enumerate(HEADERS, 1):
        c = ws.cell(row=1, column=ci, value=h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", start_color="1F4E79")
    
    row_i = 2
    for entry in report:
        matches = entry.get("matches", [])
        
        # Base row data (Strictly original input)
        row_base = [
            entry.get("row_number", ""),
            entry.get("customer_part_no", ""),
            entry.get("internal_part", ""),
            entry.get("description", ""), # ORIGINAL INPUT
            entry.get("specification", ""), # ORIGINAL INPUT
            entry.get("manufacturer_mfg", ""), # ORIGINAL INPUT
            entry.get("project_qty", ""),
            entry.get("uom", "EA"),
            entry.get("confidence_score", 0)
        ]
        
        if not matches:
            vals = row_base + ["—", "—", "—", "—"]
            for ci, v in enumerate(vals, 1):
                ws.cell(row=row_i, column=ci, value=v)
            row_i += 1
        else:
            # We show the top match in the main row
            top = matches[0]
            chunk = top.get("chunk", {})
            vals = row_base + [
                chunk.get("mpn", ""), # MATCHED MPN
                chunk.get("manufacturer", ""), # MATCHED MFR
                chunk.get("description", ""), # MATCHED DESC
                "IN_Products" # SOURCE
            ]
            for ci, v in enumerate(vals, 1):
                ws.cell(row=row_i, column=ci, value=v)
            row_i += 1
    
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

# ─────────────────────────────────────────────────────────────────────────────
# HELPER: BUILD CSV
# ─────────────────────────────────────────────────────────────────────────────
def build_csv_bytes(report: List[Dict]) -> bytes:
    """Build CSV report from search results."""
    output = io.StringIO()
    writer = csv.writer(output)
    
    headers = [
        "Row", "Customer Part No.", "Internal Part", "Description (Input)", 
        "Specification (MPN)", "Manufacturer", "Project Qty", "UOM",
        "Confidence %", "Matched MPN", "Matched Brand", "Matched Desc", "Source DB"
    ]
    writer.writerow(headers)
    
    for entry in report:
        matches = entry.get("matches", [])
        row_base = [
            entry.get("row_number", ""),
            entry.get("customer_part_no", ""),
            entry.get("internal_part", ""),
            entry.get("description", ""), # ORIGINAL INPUT
            entry.get("specification", ""), # ORIGINAL INPUT
            entry.get("manufacturer_mfg", ""), # ORIGINAL INPUT
            entry.get("project_qty", ""),
            entry.get("uom", "EA"),
            entry.get("confidence_score", 0)
        ]
        
        if not matches:
            writer.writerow(row_base + ["—", "—", "—", "—"])
        else:
            top = matches[0]
            chunk = top.get("chunk", {})
            writer.writerow(row_base + [
                chunk.get("mpn", ""), # MATCHED MPN
                chunk.get("manufacturer", ""), # MATCHED MFR
                chunk.get("description", ""), # MATCHED DESC
                "IN_Products" # SOURCE
            ])
            
    return output.getvalue().encode('utf-8')
