import pandas as pd
import numpy as np
import logging
import time
import os
import json
from qdrant_manager import qdrant_db
from hashlib import md5

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ResilientIngestor")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHECKPOINT_FILE = os.path.join(BASE_DIR, "ingestion_checkpoint.json")

def save_checkpoint(count, file_path):
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump({"last_index": count, "file": file_path, "timestamp": time.time()}, f)

def get_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, "r") as f:
                return json.load(f)
        except: return None
    return None

def ingest_products(file_path: str, batch_size: int = 1000, progress_callback=None):
    """
    Resiliently ingest products with Auto-Resume support.
    Handles both directory of RS stock files and regular flat files.
    """
    logger.info(f"📂 Analyzing source path: {file_path}")
    db = qdrant_db

    # --- DIRECTORY / RS STOCK CONSOLIDATED INGESTION ---
    if os.path.isdir(file_path):
        logger.info(f"📁 Directory detected. Starting RS Stock ingestion from: {file_path}")
        
        # 1. Wipe and recreate collection
        logger.info("Recreating collection 'products'...")
        db.recreate_collection()
        
        # 2. Load UOM_MOQ mapping
        uom_moq_path = os.path.join(file_path, "UOM_MOQ.xlsb")
        uom_moq_map = {}
        if os.path.exists(uom_moq_path):
            logger.info(f"Loading UOM & MOQ mapping from {uom_moq_path}...")
            try:
                xls = pd.ExcelFile(uom_moq_path, engine='pyxlsb')
                for sheet_name in xls.sheet_names:
                    logger.info(f"Reading sheet {sheet_name} from UOM_MOQ.xlsb...")
                    df_sheet = pd.read_excel(xls, sheet_name=sheet_name, dtype=str)
                    for _, row in df_sheet.iterrows():
                        item_num = str(row.get('Item_Number', '')).strip()
                        if item_num.endswith('.0'):
                            item_num = item_num[:-2]
                        if not item_num:
                            continue
                        uom = str(row.get('Base_Unit_of_Measure', '')).strip()
                        moq = str(row.get('Minimum_Order_Quantity', '')).strip()
                        if moq.endswith('.0'):
                            moq = moq[:-2]
                        uom_moq_map[item_num] = (uom, moq)
                logger.info(f"Loaded {len(uom_moq_map)} UOM/MOQ mapping entries.")
            except Exception as e:
                logger.error(f"Failed to load UOM/MOQ file: {e}")
        else:
            logger.warning(f"UOM_MOQ file not found at: {uom_moq_path}")
            
        # 3. Process stock files
        stock_files = [
            ("Part1_Rs Stock No With Zero.xlsb", "pyxlsb"),
            ("Part2_Rs Stock No With Zero.xlsb", "pyxlsb"),
            ("Part3_Rs Stock No With Zero.xlsx", None)
        ]
        
        total_ingested = 0
        total_expected = 1850000
        
        for fname, engine in stock_files:
            fpath = os.path.join(file_path, fname)
            if not os.path.exists(fpath):
                logger.warning(f"Stock file not found: {fpath}, skipping.")
                continue
                
            logger.info(f"Processing stock file: {fpath}...")
            use_cols = ['Item_Number', 'Web_Book', 'Web_Super_Section', 'Web_Section', 'Web_Family', 'MPN', 'Brand', 'Product_Name_Tittle']
            try:
                if engine:
                    df = pd.read_excel(fpath, engine=engine, dtype=str, usecols=use_cols)
                else:
                    df = pd.read_excel(fpath, dtype=str, usecols=use_cols)
            except Exception as e:
                logger.error(f"Failed to read {fpath} with usecols: {e}. Reading entire file.")
                try:
                    if engine:
                        df = pd.read_excel(fpath, engine=engine, dtype=str)
                    else:
                        df = pd.read_excel(fpath, dtype=str)
                except Exception as e2:
                    logger.error(f"Critical failed to read stock file {fpath}: {e2}")
                    continue
            
            logger.info(f"Loaded {len(df)} rows from {fname}. Processing in batches...")
            
            for i in range(0, len(df), batch_size):
                chunk = df.iloc[i:i+batch_size]
                batch_texts = []
                batch_metadata = []
                
                for _, row in chunk.iterrows():
                    item_num = str(row.get('Item_Number', '')).strip()
                    if item_num.endswith('.0'):
                        item_num = item_num[:-2]
                    
                    mpn = str(row.get('MPN', '')).strip()
                    brand = str(row.get('Brand', '')).strip()
                    desc = str(row.get('Product_Name_Tittle', '')).strip()
                    
                    if not mpn and not desc:
                        continue
                        
                    uom, moq = uom_moq_map.get(item_num, ("", ""))
                    
                    raw_data_parts = [
                        item_num,
                        str(row.get('Web_Book', '')).strip(),
                        str(row.get('Web_Super_Section', '')).strip(),
                        str(row.get('Web_Section', '')).strip(),
                        str(row.get('Web_Section', '')).strip(),
                        str(row.get('Web_Family', '')).strip(),
                        str(row.get('Web_Family', '')).strip(),
                        mpn,
                        str(row.get('Web_Book', '')).strip(),
                        mpn,
                        brand,
                        desc
                    ]
                    raw_data_str = "|".join(raw_data_parts)
                    
                    unique_key = f"{total_ingested + _}|{item_num.upper()}|{brand.upper()}|{mpn.upper()}"
                    point_id = md5(unique_key.encode()).hexdigest()
                    
                    search_text = f"{item_num} {mpn} {brand} {desc} {uom} {moq}".strip()
                    
                    batch_texts.append(search_text)
                    batch_metadata.append({
                        "id": point_id,
                        "payload": {
                            "product_id": item_num,
                            "mpn": mpn,
                            "manufacturer": brand,
                            "description": desc,
                            "uom": uom,
                            "moq": moq,
                            "raw_data": raw_data_str
                        }
                    })
                
                if batch_texts:
                    try:
                        vectors = db.get_embeddings(batch_texts)
                        if not vectors:
                            raise ValueError("No embeddings generated.")
                        
                        points = []
                        for idx_meta, meta in enumerate(batch_metadata):
                            points.append({
                                "id": meta["id"],
                                "vector": vectors[idx_meta],
                                "payload": meta["payload"]
                            })
                        
                        db.upsert_batch(points)
                        total_ingested += len(batch_texts)
                        
                        if progress_callback:
                            progress_callback(total_ingested, total_expected)
                    except Exception as e:
                        logger.error(f"Failed to process batch in {fname}: {e}")
                        continue
                        
            logger.info(f"Finished processing {fname}. Ingested total: {total_ingested} records.")
            import gc
            del df
            gc.collect()
            
        logger.info(f"✨ RS Stock Ingestion Complete! Total: {total_ingested} records.")
        return

    # --- STANDARD SINGLE FLAT FILE INGESTION FALLBACK ---
    ext = os.path.splitext(file_path)[1].lower()
    
    # Check for existing progress
    checkpoint = get_checkpoint()
    skip_until = 0
    if checkpoint and checkpoint["file"] == file_path:
        skip_until = checkpoint["last_index"]
        logger.info(f"🔄 Resuming from record {skip_until}...")

    # Use the global qdrant_db instance
    db = qdrant_db
    total_ingested = 0
    start_time = time.time()
    
    logger.info("🚀 Starting ingestion...")

    # --- PRE-SCAN FOR TOTAL COUNT (OPTIMIZED) ---
    file_size = os.path.getsize(file_path)
    total_expected = 1845210 # Fallback
    
    try:
        if file_size < 50 * 1024 * 1024: # Only pre-scan if small (< 50MB)
            if ext in ['.xlsx', '.xls']:
                total_expected = len(pd.read_excel(file_path, usecols=[0]))
            else:
                with open(file_path, 'rb') as f:
                    total_expected = sum(1 for _ in f) - 1
            logger.info(f"📊 Expected total records: {total_expected}")
        else:
            # For large files, estimate or use fallback to avoid minutes of blocking
            logger.info(f"📂 Large file detected ({file_size / 1024 / 1024:.1f} MB). Skipping line count pre-scan for speed.")
            total_expected = int(file_size / 500) # Rough estimate: 500 bytes per record
    except Exception as e:
        logger.warning(f"Could not calculate total lines: {e}")

    try:
        # --- 1. SMART READER SELECTION ---
        if ext in ['.xlsx', '.xls']:
            df = pd.read_excel(file_path, dtype=str)
            chunks = [df[i:i + batch_size] for i in range(0, df.shape[0], batch_size)]
        elif ext == '.csv':
            chunks = pd.read_csv(file_path, chunksize=batch_size, dtype=str, encoding='utf-8', on_bad_lines='skip')
        else:
            encodings = ['utf-8', 'utf-16', 'iso-8859-1', 'cp1252']
            chunks = None
            last_error = None
            
            for enc in encodings:
                try:
                    logger.info(f"Attempting to read file with encoding: {enc}")
                    test_df = pd.read_csv(file_path, sep='^', nrows=1, encoding=enc)
                    chunks = pd.read_csv(
                        file_path, 
                        sep='^', 
                        chunksize=batch_size, 
                        dtype=str, 
                        encoding=enc, 
                        on_bad_lines='skip',
                        quotechar='"',
                        doublequote=True
                    )
                    logger.info(f"✅ Successfully matched encoding: {enc}")
                    break
                except Exception as e:
                    last_error = e
                    continue
            
            if chunks is None:
                raise ValueError(f"Could not read file with any supported encoding. Last error: {str(last_error)}")

        # --- 2. STANDARDIZED PROCESSING ---
        for chunk in chunks:
            if total_ingested + len(chunk) <= skip_until:
                total_ingested += len(chunk)
                continue
            
            cols = [str(c).replace('"', '').lower().strip() for c in chunk.columns]
            
            idx_mpn = next((i for i, c in enumerate(cols) if 'mpn' == c or 'part number' in c or 'mfr part' in c), -1)
            if idx_mpn == -1: idx_mpn = next((i for i, c in enumerate(cols) if 'part' in c or 'mpn' in c), 9 if len(cols) > 9 else 0)
            
            idx_mfr = next((i for i, c in enumerate(cols) if 'mfr' == c or 'brand' == c or 'manufacturer' == c), -1)
            if idx_mfr == -1: idx_mfr = next((i for i, c in enumerate(cols) if 'mfr' in c or 'brand' in c or 'manufacturer' in c), 10 if len(cols) > 10 else 1)
            
            idx_desc = next((i for i, c in enumerate(cols) if 'description' == c or 'shortdescription' == c), -1)
            if idx_desc == -1: idx_desc = next((i for i, c in enumerate(cols) if 'desc' in c or 'short' in c), 11 if len(cols) > 11 else 2)
            
            idx_id = next((i for i, c in enumerate(cols) if 'article number' == c or 'article' == c), -1)
            if idx_id == -1: idx_id = next((i for i, c in enumerate(cols) if 'article' in c or 'sku' in c), 0)
            
            batch_texts = []
            batch_metadata = []
            
            for _, row in chunk.iterrows():
                row_list = [str(x).strip() for x in row.tolist()]
                
                if not row_list or all(not x or x.lower() in ['nan', 'none'] for x in row_list):
                    total_ingested += 1
                    continue
                
                if total_ingested < skip_until:
                    total_ingested += 1
                    continue

                # Clean quotes from data values
                row_list = [x.replace('"', '') for x in row_list]
                
                product_id = row_list[idx_id] if 0 <= idx_id < len(row_list) else "UNK"
                mpn = row_list[idx_mpn] if 0 <= idx_mpn < len(row_list) else ""
                manufacturer = row_list[idx_mfr] if 0 <= idx_mfr < len(row_list) else ""
                description = row_list[idx_desc] if 0 <= idx_desc < len(row_list) else ""
                
                if not mpn and not description: 
                    total_ingested += 1
                    continue

                # Unique Fingerprint: Include row index (total_ingested) to guarantee no deduplication
                clean_mpn = str(mpn).strip().upper()
                clean_mfr = str(manufacturer).strip().upper()
                clean_id = str(product_id).strip().upper()
                unique_key = f"{total_ingested}|{clean_id}|{clean_mfr}|{clean_mpn}"
                point_id = md5(unique_key.encode()).hexdigest()
                
                search_text = " ".join([str(x) for x in row_list if str(x) and str(x).lower() not in ['nan', 'none']])
                
                batch_texts.append(search_text)
                batch_metadata.append({
                    "id": point_id,
                    "payload": {
                        "product_id": product_id,
                        "mpn": mpn,
                        "manufacturer": manufacturer,
                        "description": description,
                        "raw_data": "|".join([str(x) for x in row_list])
                    }
                })
                total_ingested += 1
            
            if batch_texts:
                try:
                    vectors = db.get_embeddings(batch_texts)
                    if not vectors:
                        raise ValueError("No embeddings generated. Is the AI model loaded?")
                        
                    if len(vectors) != len(batch_metadata):
                        raise ValueError(f"Mismatch: Got {len(vectors)} vectors for {len(batch_metadata)} items")

                    points = []
                    for i, meta in enumerate(batch_metadata):
                        points.append({
                            "id": meta["id"], 
                            "vector": vectors[i], 
                            "payload": meta["payload"]
                        })
                    
                    db.upsert_batch(points)
                    save_checkpoint(total_ingested, file_path)
                    
                    if progress_callback:
                        progress_callback(total_ingested, total_expected)
                except Exception as e:
                    logger.error(f"Failed to process batch: {e}")
                    continue

        if os.path.exists(CHECKPOINT_FILE):
            os.remove(CHECKPOINT_FILE)

    except Exception as e:
        logger.error(f"❌ Ingestion task failed: {e}")
        if progress_callback: progress_callback(-1, -1)
        raise e

    logger.info(f"✨ Genuine Index Built! Total: {total_ingested} records")

if __name__ == "__main__":
    test_file = os.getenv("DATA_FILE_PATH", "uploads/IN_products.txt")
    if os.path.exists(test_file):
        ingest_products(test_file)
