
from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Depends, APIRouter, Form, Response
from pydantic import BaseModel
from utils import *
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import logging
import os
from pathlib import Path
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.concurrency import run_in_threadpool
from models import *
from qdrant_manager import qdrant_db
from celery_worker import celery_app
from celery.result import AsyncResult
import asyncio
import httpx

def is_manufacturer_aligned(mfg_a: str, mfg_b: str) -> bool:
    if not mfg_a or not mfg_b:
        return True
    
    import re
    a_clean = re.sub(r'[^A-Z0-9\s]', '', str(mfg_a).upper())
    b_clean = re.sub(r'[^A-Z0-9\s]', '', str(mfg_b).upper())
    
    if a_clean == b_clean or a_clean in b_clean or b_clean in a_clean:
        return True
        
    a_words = set(a_clean.split())
    b_words = set(b_clean.split())
    if a_words.intersection(b_words):
        return True
        
    a_flat = a_clean.replace(" ", "")
    b_flat = b_clean.replace(" ", "")
    
    # Predefined semiconductor & electronics M&A alias groups
    alias_groups = [
        {"TI", "TEXASINSTRUMENTS", "TEXASINST"},
        {"ST", "STMICROELECTRONICS", "STM"},
        {"HAR", "HARTING"},
        {"MOLEX", "MOL"},
        {"TE", "TECONNECTIVITY", "AMP"},
        {"STANLEY", "STANLEYELECTRIC"},
        {"NXP", "NXPSEMICONDUCTORS", "NEXPERIA", "PHILIPS"},
        {"FAIRCHILD", "ONSEMICONDUCTOR", "ONSEMI"},
        {"LINEARTECHNOLOGY", "ANALOGDEVICES", "ANALOG", "ADI", "LT", "MAXIM"},
        {"CYPRESS", "INFINEON", "IR", "INTERNATIONALRECTIFIER"},
        {"EPCOS", "TDK"},
        {"WEIDMULLER", "WEIDMLLER", "WEIDMUELLER"},
        {"RS", "RSPRO", "RADIOSPARE", "RADIOSPARES", "RSCOMPONENTS"},
        {"HELLERMANTYTON", "HELLERMANNTYTON"}
    ]
    
    for group in alias_groups:
        has_a = False
        has_b = False
        for item in group:
            if len(item) <= 3:
                if item in a_words or a_flat == item:
                    has_a = True
                if item in b_words or b_flat == item:
                    has_b = True
            else:
                if item in a_flat:
                    has_a = True
                if item in b_flat:
                    has_b = True
        if has_a and has_b:
            return True
            
    return False

def get_edit_distance(s1: str, s2: str) -> int:
    """Compute the Levenshtein distance between two strings."""
    if len(s1) > len(s2):
        s1, s2 = s2, s1
    distances = range(len(s1) + 1)
    for i2, c2 in enumerate(s2):
        distances_ = [i2+1]
        for i1, c1 in enumerate(s1):
            if c1 == c2:
                distances_.append(distances[i1])
            else:
                distances_.append(1 + min((distances[i1], distances[i1+1], distances_[-1])))
        distances = distances_
    return distances[-1]

def calculate_confidence_score(q_raw: str, p_raw: str, pid_raw: str, mfr_match: bool, is_semantic_only: bool = False) -> int:
    """
    Calculate confidence score based on the customer-provided MPN relationship & brand matrix.
    """
    if is_semantic_only or not q_raw:
        return 35 if mfr_match else 20
        
    import re
    q_str = str(q_raw).strip().upper()
    p_str = str(p_raw).strip().upper()
    pid_str = str(pid_raw).strip().upper()
    
    q_clean = re.sub(r'[^A-Z0-9]', '', q_str).lstrip('0')
    p_clean = re.sub(r'[^A-Z0-9]', '', p_str).lstrip('0')
    pid_clean = re.sub(r'[^A-Z0-9]', '', pid_str).lstrip('0')
    
    # Check for Product ID numeric collisions (matches Product ID clean, but not the catalog MPN, and brand is different)
    is_pid_collision = False
    if pid_clean and not mfr_match:
        if q_clean == pid_clean or q_clean.startswith(pid_clean) or pid_clean.startswith(q_clean) or q_clean.endswith(pid_clean) or pid_clean.endswith(q_clean):
            # If the actual MPN does not match the query clean
            if p_clean and q_clean != p_clean and not q_clean.startswith(p_clean) and not p_clean.startswith(q_clean):
                is_pid_collision = True
                
    if is_pid_collision:
        # Product ID collision with a brand mismatch is heavily penalized as a likely wrong selection
        if q_clean == pid_clean:
            return 55  # Exact Product ID match with wrong brand
        else:
            return 30  # Prefix/suffix Product ID match with wrong brand
            
    # 1. Exact match including formatting
    if q_str == p_str or (pid_str and q_str == pid_str):
        return 100 if mfr_match else 80
    
    # If cleaned query is empty, treat as semantic only
    if not q_clean:
        return 35 if mfr_match else 20
        
    # 2. Space / Hyphen / Formatting Only
    if q_clean == p_clean or (pid_clean and q_clean == pid_clean):
        return 98 if mfr_match else 75
        
    # Compute distance against the closest identifier
    d_mpn = get_edit_distance(q_clean, p_clean) if p_clean else 999
    d_pid = get_edit_distance(q_clean, pid_clean) if pid_clean else 999
    d = min(d_mpn, d_pid)
    
    # Check prefix/suffix status against closest clean candidate string
    cand_clean = p_clean if d_mpn <= d_pid else pid_clean
    if not cand_clean:
        return 5
        
    is_prefix_or_suffix = (q_clean.startswith(cand_clean) or cand_clean.startswith(q_clean) or 
                            q_clean.endswith(cand_clean) or cand_clean.endswith(q_clean))
                            
    len_diff = abs(len(q_clean) - len(cand_clean))
    
    # 3. One Suffix / Prefix Difference (minor variant, len_diff <= 2)
    if is_prefix_or_suffix and len_diff <= 2:
        return 90 if mfr_match else 65
        
    # 4. Prefix Only (generic prefix match, len_diff >= 3)
    if (q_clean.startswith(cand_clean) or cand_clean.startswith(q_clean)) and len_diff >= 3:
        return 40 if mfr_match else 30
        
    # 5. One Internal Character Difference
    if d == 1:
        return 80 if mfr_match else 60
        
    # 6. Multiple Character Differences
    q_digits = set(c for c in q_clean if c.isdigit())
    c_digits = set(c for c in cand_clean if c.isdigit())
    if d <= max(len(q_clean), len(cand_clean)) - 2 or q_digits.intersection(c_digits):
        return 65 if mfr_match else 50
        
    # 7. Completely Different
    return 5

router = APIRouter()
logger = logging.getLogger(__name__)

def catch_route_exceptions(func):
    import functools
    import traceback
    from fastapi.responses import JSONResponse
    if asyncio.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                tb = traceback.format_exc()
                logger.error(f"❌ Error in {func.__name__}: {e}\n{tb}")
                return JSONResponse(status_code=500, content={"status": "error", "detail": f"Server Error: {str(e)}\n{tb}"})
        return async_wrapper
    else:
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                tb = traceback.format_exc()
                logger.error(f"❌ Error in {func.__name__}: {e}\n{tb}")
                return JSONResponse(status_code=500, content={"status": "error", "detail": f"Server Error: {str(e)}\n{tb}"})
        return sync_wrapper

# Configurable Ollama model setting: default to qwen2.5:14b or override via environment variables
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")

# ─────────────────────────────────────────────────────────────────────────────
# CORE RS COMPONENT ENDPOINTS — VECTOR DB MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/admin/upload-source")
async def upload_source_data(file: UploadFile = File(...)):
    """Upload the raw product text file to the server for RS indexing."""
    if state.vdb_lock:
        raise HTTPException(status_code=400, detail="System Busy: An ingestion task is already running.")
        
    os.makedirs("uploads", exist_ok=True)
    safe_name = "".join([c for c in file.filename if c.isalnum() or c in ('.', '_', '-')]).strip()
    file_path = f"uploads/{safe_name}"
    
    try:
        with open(file_path, "wb") as buffer:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk: break
                buffer.write(chunk)
        
        file_size = os.path.getsize(file_path)
        return {
            "status": "success", 
            "message": f"Successfully uploaded: {file.filename}",
            "original_name": file.filename,
            "file_path": file_path
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@router.get("/admin/list-uploads")
async def list_uploads():
    """List all available source files for RS indexing."""
    try:
        if not os.path.exists("uploads"): return {"files": []}
        files = []
        for f in os.listdir("uploads"):
            path = os.path.join("uploads", f)
            if os.path.isfile(path):
                stats = os.stat(path)
                files.append({
                    "name": f,
                    "size": stats.st_size,
                    "modified": datetime.fromtimestamp(stats.st_mtime).isoformat()
                })
            elif os.path.isdir(path) and f == "Rs_stock":
                stats = os.stat(path)
                files.append({
                    "name": f,
                    "size": 0,
                    "modified": datetime.fromtimestamp(stats.st_mtime).isoformat()
                })
        files.sort(key=lambda x: x['modified'], reverse=True)
        return {"files": files}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/build-index")
async def build_index(filename: Optional[str] = Query(None)):
    """Trigger the RS Vector DB build process via Celery."""
    if state.vdb_lock:
        return {"status": "busy", "task_id": state.active_task_id}
        
    # Sanitize the input filename using os.path.basename to prevent directory traversal
    safe_filename = os.path.basename(filename) if filename else None
    file_path = f"uploads/{safe_filename}" if safe_filename else None
    
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=400, detail="Source file not found.")
    
    try:
        state.vdb_lock = True
        task = celery_app.send_task("ingest_products_task", args=[file_path])
        state.active_task_id = task.id
        return {"status": "success", "task_id": task.id}
    except Exception as e:
        state.vdb_lock = False
        raise HTTPException(status_code=500, detail=f"Failed to start: {str(e)}")

@router.get("/admin/vdb-stats")
async def get_vdb_stats():
    """Fetch live record count from RS Vector DB."""
    try:
        info = qdrant_db.client.get_collection("products")
        return {"points_count": info.points_count, "status": info.status}
    except Exception as e:
        return {"points_count": 0, "error": str(e)}

@router.get("/admin/index-status")
async def get_index_status():
    """Check the background indexing status."""
    is_actually_running = False
    if state.active_task_id:
        task_res = AsyncResult(state.active_task_id)
        if task_res.state in ['PENDING', 'STARTED', 'PROGRESS', 'RETRY']:
            is_actually_running = True
            # Include progress if available
            if task_res.state == 'PROGRESS':
                return {
                    "locked": True,
                    "status": "active",
                    "progress": task_res.info
                }
        else:
            if task_res.state == 'SUCCESS' and state.vdb_lock:
                state.vdb_built = True
                import asyncio
                asyncio.create_task(run_rebuild_taxonomy_task())
            state.vdb_lock = False
            state.active_task_id = None
            
    source_exists = False
    if os.path.exists("uploads"):
        source_exists = any(os.path.isfile(os.path.join("uploads", f)) for f in os.listdir("uploads"))

    return {
        "locked": is_actually_running, 
        "status": "active" if is_actually_running else "idle",
        "source_file_exists": source_exists
    }

def is_blocklisted(mpn: str, desc: str) -> bool:
    m = str(mpn).upper()
    d = str(desc).upper()
    blocklist_terms = ["PTFIX", "3274056", "3274058", "3274054"]
    for term in blocklist_terms:
        if term in m or term in d:
            return True
    return False

@router.get("/search")
async def search_products(query: str, limit: int = 25):
    """Hybrid Search: Exact MPN match first, then Semantic fallback with premium re-ranking."""
    try:
        # 1. Try Exact MPN match first (Super Fast)
        exact_results = await run_in_threadpool(qdrant_db.exact_mpn_search, query)
        
        # 2. Get Semantic results with high candidate size to preserve legitimate matches
        semantic_results = await run_in_threadpool(qdrant_db.search, query, limit=max(350, limit*2))
        
        # Determine if we should be strict (only return exact matches)
        import re
        cleaned_query = re.sub(r'[\-_/\.:]', '', query.upper())
        is_mpn_query = len(query) >= 3 and ' ' not in query and (any(c.isdigit() for c in query) or cleaned_query.isalpha())
        
        # Strictness for long technical descriptions
        import re
        has_voltage = bool(re.search(r'\b\d+(?:\.\d+)?\s*v\b|\bvolt\b|\bvolts\b', query.lower()))
        has_tech_kw = any(kw in query.lower() for kw in ['uf', 'pf', 'mlcc', 'smd', 'capacitor'])
        is_tech_query = len(query) > 20 and (has_voltage or has_tech_kw)
        
        if exact_results and (is_mpn_query or (is_tech_query and any(r.get('score', 0) == 1.0 for r in exact_results))):
            logger.info(f"🎯 Strict match found for '{query}'. Filtering out semantic noise.")
            candidates = exact_results
        elif is_tech_query and semantic_results:
            # Keep semantic results that score > 0.70 (custom BERT high-confidence threshold)
            candidates = exact_results + [r for r in semantic_results if r.get('score', 0) > 0.70]
        else:
            candidates = exact_results + semantic_results
            
        seen_parts = set()
        final_results = []
        
        for res in candidates:
            p = res.get('payload', res)
            mpn = str(p.get('mpn', '')).upper()
            mfr = str(p.get('manufacturer', '')).upper()
            desc = str(p.get('description', '')).lower()
            query_lower = query.lower()
            
            # --- HUMAN-MINDSET BOOSTING WITH UNIT NORMALIZATION ---
            import re
            def norm_text(text: str) -> str:
                t = str(text).lower()
                t = re.sub(r'(\d+)\s*(w|v|a|ma|uf|pf|hz|db|ohm)\b', r'\1\2', t)
                return t
            
            norm_desc = norm_text(desc)
            norm_mpn = norm_text(mpn)
            norm_mfr = norm_text(mfr)
            norm_query = norm_text(query)
            
            query_tokens = [t.strip(',') for t in norm_query.split() if len(t) > 1]
            query_tokens_set = set(query_tokens)
            desc_tokens_set = set([t.strip(',') for t in norm_desc.split() if len(t) > 1])
            mpn_tokens_set = set([t.strip(',') for t in norm_mpn.split() if len(t) > 1])
            mfr_tokens_set = set([t.strip(',') for t in norm_mfr.split() if len(t) > 1])
            target_tokens = desc_tokens_set | mpn_tokens_set | mfr_tokens_set
            
            intersection = query_tokens_set & target_tokens
            union = query_tokens_set | target_tokens
            
            query_coverage = len(intersection) / len(query_tokens_set) if query_tokens_set else 0
            match_rate = len(intersection) / len(union) if union else 0
            res['match_rate'] = match_rate
            
            is_high_quality = False
            if query_coverage >= 0.8:
                is_high_quality = True
                res['score'] = 1.0 # Force 100% confidence for technical alignment
                res['is_exact'] = True
                logger.info(f"🚀 Human-Boosted: {mpn} (Coverage: {len(intersection)}/{len(query_tokens_set)}, Jaccard: {match_rate:.2f})")

            # --- BRAND & CATEGORY STRICTNESS ---
            brand_mismatch = False
            for token in query_tokens:
                tok_up = token.upper()
                if (len(tok_up) > 3 or tok_up in ["TE", "TI"]) and tok_up in ["MEAN WELL", "ITECH", "PANASONIC", "MOLEX", "TE", "TI"]:
                    mfr_up = mfr.upper()
                    is_match = False
                    if tok_up == mfr_up or mfr_up in tok_up:
                        is_match = True
                    elif tok_up == "TI" and "TEXAS INSTRUMENTS" in mfr_up:
                        is_match = True
                    elif tok_up == "TE" and "TE CONNECTIVITY" in mfr_up:
                        is_match = True
                    
                    if not is_match:
                        brand_mismatch = True
                        break

            if brand_mismatch and res.get('score', 0) < 0.85:
                res['score'] = res.get('score', 0) * 0.6 # Significant penalty for wrong brand
                res['is_exact'] = False
                logger.info(f"⚠️ Brand Mismatch: Query has brand info but {mfr} doesn't match. Penalized.")

            # --- CATEGORY CONFLICT GUARD ---
            instruments = ['power source', 'power supply', 'electronic load', 'instrument', 'meter', 'tester', 'source']
            consumables = ['cable', 'wire', 'connector', 'terminal', 'lug', 'shield']
            
            is_query_instrument = any(kw in query_lower for kw in instruments)
            is_result_consumable = any(kw in desc or kw in str(p.get('category', '')).lower() for kw in consumables)
            
            if is_query_instrument and is_result_consumable:
                res['score'] = res.get('score', 0) * 0.4 # Heavy penalty for category mismatch
                res['is_exact'] = False
                logger.info(f"🚫 Category Conflict: Query is 'Instrument' but result is 'Consumable'. Penalized {mpn}.")

            key = f"{mpn}|{mfr}"
            if key not in seen_parts:
                def clean_leading_zeros(s: str) -> str:
                    s_clean = s.strip().upper()
                    if s_clean.isdigit():
                        return s_clean.lstrip('0')
                    return s_clean
                
                query_clean_numeric = clean_leading_zeros(query)
                mpn_clean_numeric = clean_leading_zeros(mpn)
                
                is_exact_match = (mpn_clean_numeric == query_clean_numeric) or any(clean_leading_zeros(str(e.get('payload', {}).get('mpn', ''))) == query_clean_numeric for e in exact_results)
                res['is_exact'] = res.get('is_exact', False) or is_exact_match
                # Scale up high-confidence semantic matches from fine-tuned model
                current_score = res.get('score', 0)
                if current_score >= 0.74:
                    res['score'] = 1.0
                    res['is_exact'] = True
                elif is_high_quality and current_score < 0.95:
                    res['score'] = 0.95
                final_results.append(res)
                seen_parts.add(key)
        
        # Re-sort based on the new boosted scores and secondary match rate token overlap
        final_results.sort(key=lambda x: (x.get('score', 0.0), x.get('match_rate', 0.0)), reverse=True)
        
        filtered_results = []
        q_clean = query.strip().upper()
        q_words = q_clean.split()
        is_single_token = len(q_words) == 1 and len(q_clean) >= 3
        
        for res in final_results:
            p = res.get('payload', res)
            mpn = str(p.get('mpn', '')).upper()
            mfr = str(p.get('manufacturer', '')).upper()
            desc = str(p.get('description', '')).upper()
            if is_blocklisted(mpn, desc):
                continue
            score = res.get('score', 0.0)
            
            if is_single_token:
                is_exact_match = (score >= 0.95)
                contains_token = (q_clean in mpn or q_clean in desc or q_clean in mfr or mpn in q_clean)
                if is_exact_match or (score >= 0.50 and contains_token):
                    filtered_results.append(res)
            else:
                if score >= 0.50:
                    filtered_results.append(res)
                    
        return filtered_results[:limit]
    except Exception as e:
        logger.error(f"Search error: {e}")
        return []

@router.get("/search-llm")
async def search_products_llm(
    query: str,
    mpn: Optional[str] = None,
    brand: Optional[str] = None,
    description: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 15
):
    """
    Search using Qdrant vector database first, then use local Ollama LLM to compare, 
    score, and justify candidates for alignment reasoning.
    """
    try:
        candidates = await search_products(query, limit=limit)
    except Exception as e:
        logger.error(f"Base search failed inside search-llm: {e}")
        candidates = []
        
    if not candidates:
        return []
        
    target_mpn = mpn or ""
    target_brand = brand or ""
    target_desc = description or query
    target_cat = category or ""
    
    if not target_mpn and query and len(query) > 5 and ' ' not in query:
        target_mpn = query
        
    prompt = (
        "You are an expert component engineering database assistant for RS Components.\n"
        "Your task is to compare a target BOM item specification against a list of database catalog candidates.\n"
        "Evaluate each candidate's suitability and match quality, and assign a confidence score (0 to 100) and a concise technical reasoning justification.\n\n"
        "TARGET ITEM SPECIFICATIONS:\n"
        f"- Target MPN: {target_mpn}\n"
        f"- Target Brand/Manufacturer: {target_brand}\n"
        f"- Target Description: {target_desc}\n"
        f"- Target Category: {target_cat}\n\n"
        "DATABASE CANDIDATES:\n"
    )
    for idx, c in enumerate(candidates):
        p = c.get("payload", c)
        prompt += (
            f"Candidate ID {idx}:\n"
            f"  Stock No: {p.get('product_id', 'N/A')}\n"
            f"  MPN: {p.get('mpn', 'N/A')}\n"
            f"  Brand: {p.get('manufacturer', p.get('brand', 'N/A'))}\n"
            f"  Category: {p.get('category', 'N/A')}\n"
            f"  Description: {p.get('description', 'N/A')}\n\n"
        )
    prompt += (
        "INSTRUCTIONS:\n"
        "1. For each Candidate ID, assess if it is a functional and technical match for the TARGET ITEM.\n"
        "2. Assign a confidence score from 0 to 100:\n"
        "   - 95-100: Exact MPN or identical functional fit.\n"
        "   - 70-94: Strong matching specifications/values but different brand, or slight description variation.\n"
        "   - 40-69: Partial match (e.g. same category/family but different values/MPN).\n"
        "   - 0-39: Mismatch or completely unrelated part.\n"
        "3. Provide a very brief technical reasoning justification (max 12 words), e.g. 'Exact MPN match' or 'Matching capacitance, different brand' or 'Voltage rating mismatch'.\n"
        "4. Return ONLY a valid JSON list of objects containing these keys:\n"
        "   - 'id' (integer corresponding to the Candidate ID)\n"
        "   - 'score' (integer 0 to 100)\n"
        "   - 'reasoning' (string justification)\n\n"
        "Do not include any explanation or markdown formatting outside the JSON list. Return ONLY the raw JSON list."
    )
    
    llm_results = {}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "http://172.17.0.1:8010/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False
                },
                timeout=25.0
            )
            if response.status_code == 200:
                res_json = response.json()
                response_text = res_json.get("response", "").strip()
                
                import json
                if "```json" in response_text:
                    response_text = response_text.split("```json")[1].split("```")[0].strip()
                elif "```" in response_text:
                    response_text = response_text.split("```")[1].strip()
                
                parsed = json.loads(response_text)
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict) and "id" in item:
                            llm_results[int(item["id"])] = {
                                "score": float(item.get("score", 0)) / 100.0,
                                "reasoning": str(item.get("reasoning", "")).strip()
                            }
    except Exception as e:
        logger.error(f"Ollama LLM search evaluation failed: {e}")
        
    final_candidates = []
    for idx, c in enumerate(candidates):
        eval_data = llm_results.get(idx)
        if eval_data:
            c["score"] = eval_data["score"]
            c["reasoning"] = f"🧠 LLM: {eval_data['reasoning']}"
            if "payload" in c:
                c["payload"]["reasoning"] = f"🧠 LLM: {eval_data['reasoning']}"
        else:
            c["reasoning"] = "Semantic match evaluated by local BERT"
            if "payload" in c:
                c["payload"]["reasoning"] = "Semantic match evaluated by local BERT"
                
        final_candidates.append(c)
        
    final_candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
    return final_candidates

# ─────────────────────────────────────────────────────────────────────────────
def is_valid_mpn_token(token: str) -> bool:
    """Validate if a token is a real part number, excluding common value/unit ratings and SMD packages."""
    import re
    token_upper = token.strip().upper()
    if not token_upper or token_upper in ("NAN", "NONE", "-", "N/A"):
        return False
        
    # An MPN is a single cohesive string token and cannot contain spaces
    if ' ' in token_upper:
        return False
        
    # 1. Must contain at least one digit OR be purely alphabetic or clean alphabetic/alphanumeric with symbols of length >= 3.
    if not any(c.isdigit() for c in token_upper):
        cleaned = re.sub(r'[\-_/\.:]', '', token_upper)
        if not (cleaned.isalpha() and len(cleaned) >= 3):
            return False
    if not any(c.isalpha() for c in token_upper):
        cleaned = re.sub(r'[\-_/\.:]', '', token_upper)
        if not (cleaned.isdigit() and 3 <= len(cleaned) <= 12):
            return False
        
    # 2. Exclude common technical values and unit ratings (e.g. 0.25W, 100V, 10K, 10UF, 1/4W, 50HZ)
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
            
    # Reject generic sequential labels like "Part1", "Item2", "Code3"
    import re
    if re.match(r'^(PART|ITEM|CODE|SEQ|PAGE|LINE|SNO|QTY|QUAN|PRICE|COST)\s*\d*$', val_clean.upper()):
        return False
            
    return True

async def clean_batch(sem: asyncio.Semaphore, client: httpx.AsyncClient, batch: list, original_had_sheet_source: bool, raw_mapping: dict = None) -> list:
    import json
    import pandas as pd
    from utils import identify_columns
    from fastapi.concurrency import run_in_threadpool
    
    if not batch:
        return []
        
    try:
        # Step 1: Identify columns for this batch's keys
        keys = list(batch[0].keys())
        if raw_mapping:
            mapping = raw_mapping
        else:
            # Call identify_columns on the batch DataFrame to utilize content heuristics
            batch_df = pd.DataFrame(batch)
            mapping = identify_columns(batch_df)
        
        # Step 2: Form query strings and collect raw part numbers
        row_queries = []
        raw_mpns = []
        raw_ids = []
        for row in batch:
            mpn = str(row.get(keys[mapping['mpn']])) if mapping['mpn'] >= 0 else ""
            mfg = str(row.get(keys[mapping['manufacturer']])) if mapping['manufacturer'] >= 0 else ""
            desc = str(row.get(keys[mapping['description']])) if mapping['description'] >= 0 else ""
            
            # Clean values
            mpn_clean = mpn.strip()
            mfg_clean = mfg.strip()
            desc_clean = desc.strip()
            if mpn_clean.lower() in ("nan", "none", "null"): mpn_clean = ""
            if mfg_clean.lower() in ("nan", "none", "null"): mfg_clean = ""
            if desc_clean.lower() in ("nan", "none", "null"): desc_clean = ""
            
            raw_mpns.append(mpn_clean)
            
            intl_val = str(row.get(keys[mapping['internal_part']])).strip() if mapping['internal_part'] >= 0 else ""
            if intl_val.lower() in ("nan", "none", "null"): intl_val = ""
            raw_ids.append(intl_val)
            
            q_text = f"{mfg_clean} {mpn_clean} {desc_clean}".strip()
            if not q_text:
                q_text = " ".join([str(v) for v in row.values() if pd.notna(v) and str(v).strip() != ""])
            row_queries.append(q_text)
            
        # Step 2.5: Run high-performance batch exact lookups first
        unique_ids = list(set([i for i in raw_ids if i]))
        unique_mpns = list(set([m for m in raw_mpns if m]))
        
        id_lookup = {}
        mpn_lookup = {}
        if unique_ids:
            id_lookup = await run_in_threadpool(qdrant_db.exact_id_search_batch, unique_ids)
        if unique_mpns:
            mpn_lookup = await run_in_threadpool(qdrant_db.exact_mpn_search_batch, unique_mpns)
            
        # Step 3: Run fast batch semantic search in Qdrant
        qdrant_results = await run_in_threadpool(qdrant_db.search_batch, row_queries, limit=3)
        
        # Step 4: Evaluate candidates for auto-adoption vs Ollama cleansing
        final_records = [None] * len(batch)
        rows_needing_llm = [] # list of tuple (row_dict, q_text, hits, index)
        
        for idx, (row, q_text, hits, orig_mpn, orig_id) in enumerate(zip(batch, row_queries, qdrant_results, raw_mpns, raw_ids)):
            raw_row_data = row.get("raw_row_data", row)
            has_high_conf_match = False
            
            # 1. Try exact match lookup first
            exact_hit = None
            if orig_id and orig_id in id_lookup and id_lookup[orig_id]:
                exact_hit = id_lookup[orig_id][0]
            elif orig_mpn and orig_mpn in mpn_lookup and mpn_lookup[orig_mpn]:
                exact_hit = mpn_lookup[orig_mpn][0]
                
            if exact_hit:
                pay = exact_hit.get('payload', {})
                # Check for manufacturer conflict before adopting the exact hit
                q_mfr = str(row.get(keys[mapping['manufacturer']])).strip().upper() if (mapping['manufacturer'] >= 0 and keys) else ""
                cand_mfr = str(pay.get('manufacturer', '')).strip().upper()
                if not is_manufacturer_aligned(q_mfr, cand_mfr):
                    exact_hit = None

            if exact_hit:
                pay = exact_hit.get('payload', {})
                mpn_val = str(pay.get('mpn', '')).strip()
                mfg_val = str(pay.get('manufacturer', '')).strip()
                desc_val = str(pay.get('description', '')).strip()
                category_val = str(pay.get('category', 'General')).strip()
                
                raw_qty = str(row.get(keys[mapping['qty']])) if mapping['qty'] >= 0 else "1"
                try:
                    from utils import extract_qty_and_uom
                    qty_val, uom_val = extract_qty_and_uom(raw_qty)
                except Exception:
                    qty_val = 1.0
                    uom_val = "PCS"
                    
                record = {
                    "Part Number (MPN)": mpn_val,
                    "Manufacturer": mfg_val,
                    "Description": desc_val,
                    "Quantity": qty_val,
                    "UOM": uom_val.upper(),
                    "Customer Part No.": "",
                    "Internal Part Number": "",
                    "Category": category_val
                }
                if original_had_sheet_source and "Sheet Source" in raw_row_data:
                    record["Sheet Source"] = raw_row_data["Sheet Source"]
                    
                final_records[idx] = record
                has_high_conf_match = True
                
            # 2. Fallback to semantic similarity auto-adoption
            if not has_high_conf_match:
                if hits:
                    def get_hit_sort_key(hit):
                        import re
                        pay = hit.get('payload', {}) if isinstance(hit, dict) else (hit.payload if hasattr(hit, 'payload') else {})
                        cand_mpn = re.sub(r'[^A-Z0-9]', '', str(pay.get('mpn', '')).upper())
                        query_clean = re.sub(r'[^A-Z0-9]', '', str(q_text).upper())
                        has_mpn_in_query = 1 if (cand_mpn and cand_mpn in query_clean) else 0
                        score = hit.get('score', 0) if isinstance(hit, dict) else (hit.score if hasattr(hit, 'score') else 0)
                        return (has_mpn_in_query, score)
                    hits = sorted(hits, key=get_hit_sort_key, reverse=True)
                
                best_hit = hits[0] if hits else None
                if best_hit:
                    score = best_hit.get('score', 0)
                    if score >= 0.65:
                        base_conf = int(85 + (score - 0.65) * 100)
                        base_conf = min(98, base_conf)
                    elif score >= 0.50:
                        base_conf = int(60 + (score - 0.50) * 166)
                        base_conf = min(84, base_conf)
                    else:
                        base_conf = int(score * 120)
                        base_conf = min(59, base_conf)
                        
                    if base_conf >= 80:
                        pay = best_hit.get('payload', {})
                        # Check for manufacturer conflict before adopting semantic hit
                        q_mfr = str(row.get(keys[mapping['manufacturer']])).strip().upper() if (mapping['manufacturer'] >= 0 and keys) else ""
                        cand_mfr = str(pay.get('manufacturer', '')).strip().upper()
                        if not is_manufacturer_aligned(q_mfr, cand_mfr):
                            # Manufacturer conflict, do not auto-adopt
                            pass
                        else:
                            mpn_val = str(pay.get('mpn', '')).strip()
                            mfg_val = str(pay.get('manufacturer', '')).strip()
                            desc_val = str(pay.get('description', '')).strip()
                            category_val = str(pay.get('category', 'General')).strip()
                        
                        raw_qty = str(row.get(keys[mapping['qty']])) if mapping['qty'] >= 0 else "1"
                        try:
                            from utils import extract_qty_and_uom
                            qty_val, uom_val = extract_qty_and_uom(raw_qty)
                        except Exception:
                            qty_val = 1.0
                            uom_val = "PCS"
                            
                        record = {
                            "Part Number (MPN)": mpn_val,
                            "Manufacturer": mfg_val,
                            "Description": desc_val,
                            "Quantity": qty_val,
                            "UOM": uom_val.upper(),
                            "Customer Part No.": "",
                            "Internal Part Number": "",
                            "Category": category_val
                        }
                        if original_had_sheet_source and "Sheet Source" in raw_row_data:
                            record["Sheet Source"] = raw_row_data["Sheet Source"]
                            
                        final_records[idx] = record
                        has_high_conf_match = True
                        
            if not has_high_conf_match:
                rows_needing_llm.append((row, q_text, hits, idx))
                
        # Step 5: Invoke Ollama only for rows needing LLM cleansing
        if rows_needing_llm:
            # Build grounded items for prompt
            grounding_items = []
            for row, q_text, hits, orig_idx in rows_needing_llm:
                row_dict = {str(k): str(v).strip() for k, v in row.items() if pd.notna(v) and str(v).strip() not in ("nan", "None", "")}
                
                candidates_info = []
                for c_idx, hit in enumerate(hits[:3]):
                    pay = hit.get('payload', {})
                    score = hit.get('score', 0)
                    candidates_info.append(
                        f"Candidate {c_idx+1}: "
                        f"MPN: {pay.get('mpn', 'N/A')}, "
                        f"Manufacturer: {pay.get('manufacturer', 'N/A')}, "
                        f"Description: {pay.get('description', 'N/A')}, "
                        f"Category: {pay.get('category', 'General')} "
                        f"(Similarity: {score:.2f})"
                    )
                    
                grounding_items.append({
                    "id": orig_idx, # Use original index for simple reconstruction
                    "raw_row_data": row_dict,
                    "database_candidates": candidates_info
                })
                
            prompt = (
                "You are an Industrial Procurement and MRO Data Normalization Expert.\n"
                "Your job is to take a list of messy spreadsheet rows and standardize them into a clean, unified JSON flat BOM schema.\n"
                "To achieve maximum precision, you are provided with highly relevant reference candidates retrieved from our vector database.\n\n"
                "INSTRUCTIONS:\n"
                "1. If one of the database candidates is a functional/technical match for the messy input row, adopt its exact 'clean_mpn', 'clean_mfg', and 'clean_category' to align with our database.\n"
                "2. If none of the candidates match, extract and standardize the part number, manufacturer, and description cleanly from the raw row data. Never invent information.\n"
                "3. Generate procurement-friendly descriptions.\n"
                "4. Standardize units in 'clean_uom': PCS, MTR, MM, CM, FT, KG, G.\n\n"
                "--------------------------------------------------\n"
                "CATEGORY-SPECIFIC STANDARDS:\n"
                "--------------------------------------------------\n"
                "A. CONNECTOR:\n"
                "   - Description ('clean_desc') format: 'Connector, {Application}'\n"
                "B. TERMINAL:\n"
                "   - Description ('clean_desc') format: 'Terminal, {Type}'\n"
                "   - UOM = 'PCS'\n"
                "C. WIRE SEAL:\n"
                "   - Description ('clean_desc') format: 'Wire Seal, {Wire Size}'\n"
                "   - UOM = 'PCS'\n"
                "D. CAVITY PLUG:\n"
                "   - Description ('clean_desc') format: 'Cavity Plug'\n"
                "   - UOM = 'PCS'\n"
                "E. TPA / WEDGE:\n"
                "   - Description ('clean_desc') format: 'TPA/Wedge Lock'\n"
                "   - UOM = 'PCS'\n"
                "F. WIRE:\n"
                "   - Description ('clean_desc') format: 'Wire, {Standard}, {Gauge}{Unit}, {Insulation}, Class {Class}, Color {Color}'\n"
                "   - MPN ('clean_mpn') format: combine insulation type, gauge size, and color (e.g. 'FLRXX 0.75 RD')\n"
                "   - UOM = 'MTR'\n\n"
                "--------------------------------------------------\n"
                "OUTPUT FORMAT RULES:\n"
                "--------------------------------------------------\n"
                f"Input Items:\n{json.dumps(grounding_items, indent=2)}\n\n"
                "Return ONLY a valid JSON list of objects containing these keys:\n"
                "- 'id' (integer matching input item id)\n"
                "- 'clean_mpn' (standardized part number or spec key)\n"
                "- 'clean_mfg' (standardized manufacturer)\n"
                "- 'clean_desc' (standardized descriptive name)\n"
                "- 'clean_qty' (numeric quantity extracted)\n"
                "- 'clean_uom' (standardized UOM in uppercase like 'PCS' or 'MTR')\n"
                "- 'clean_category' (standardized category string like 'Connectors', 'Terminals', 'Wires', 'General')\n\n"
                "Do not include any explanation or markdown formatting outside the JSON. Return ONLY the raw JSON list."
            )
            
            async with sem:
                response = await client.post(
                    "http://172.17.0.1:8010/api/generate",
                    json={
                        "model": OLLAMA_MODEL,
                        "prompt": prompt,
                        "stream": False
                    },
                    timeout=45.0
                )
                if response.status_code == 200:
                    res_json = response.json()
                    response_text = res_json.get("response", "").strip()
                    
                    if "```json" in response_text:
                        response_text = response_text.split("```json")[1].split("```")[0].strip()
                    elif "```" in response_text:
                        response_text = response_text.split("```")[1].strip()
                        
                    cleaned_batch = json.loads(response_text)
                    
                    # Map cleaned items by ID
                    cleaned_map = {int(item["id"]): item for item in cleaned_batch if "id" in item}
                    
                    for orig_row, q_text, hits, orig_idx in rows_needing_llm:
                        clean_item = cleaned_map.get(orig_idx, {})
                        
                        try:
                            qty_val = float(clean_item.get("clean_qty", 1.0))
                        except Exception:
                            qty_val = 1.0
                            
                        raw_row_data = orig_row.get("raw_row_data", orig_row)
                        
                        # Extract UOM from raw headers
                        header_uom = None
                        for k in raw_row_data.keys():
                            k_lower = str(k).lower()
                            if "qty" in k_lower or "quantity" in k_lower or "qnt" in k_lower:
                                import re
                                match_uom = re.search(r'\b(mtrs?|meters?|metres?|pcs|pieces?|each|nos|feet|ft|inches?|kgs?|kilograms?)\b', k_lower)
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
                                if not header_uom:
                                    if re.search(r'\b(m)\b', k_lower):
                                        header_uom = "mtr"
                                        
                        desc_val = str(clean_item.get("clean_desc", "")).strip()
                        mpn_val = str(clean_item.get("clean_mpn", "")).strip()
                        
                        # Wires detection
                        is_wire = False
                        desc_lower = desc_val.lower()
                        has_wire_keyword = "wire" in desc_lower and not any(header in desc_lower for header in ["wire table -", "wire list -", "cable table -", "wires -"])
                        if has_wire_keyword or "automotive wire" in desc_lower:
                            non_wire_words = ["clip", "connector", "terminal", "wedge", "seal", "plug", "bracket", "clamp", "tape", "sleeve", "tube", "tie", "mount", "cap", "housing", "contact", "pin", "socket"]
                            if not any(w in desc_lower for w in non_wire_words):
                                is_wire = True
                        if any(wk in mpn_val.upper() for wk in ["FLRXX", "FLRY", "TXL", "GXL", "SXL"]):
                            is_wire = True
                            
                        if is_wire:
                            uom_val = "mtr"
                            category_val = "Wires"
                        else:
                            uom_val = str(clean_item.get("clean_uom", "pcs")).strip().lower()
                            if uom_val == "pcs" and header_uom:
                                uom_val = header_uom
                            category_val = str(clean_item.get("clean_category", "General")).strip()
                            
                        record = {
                            "Part Number (MPN)": mpn_val,
                            "Manufacturer": str(clean_item.get("clean_mfg", "")).strip(),
                            "Description": desc_val,
                            "Quantity": qty_val,
                            "UOM": uom_val.upper() if uom_val else "PCS",
                            "Customer Part No.": "",
                            "Internal Part Number": "",
                            "Category": category_val
                        }
                        if original_had_sheet_source and "Sheet Source" in raw_row_data:
                            record["Sheet Source"] = raw_row_data["Sheet Source"]
                            
                        final_records[orig_idx] = record
                else:
                    print(f"⚠️ Ollama returned non-200 code: {response.status_code}")
                    
        # Step 6: Fallback for any rows that failed to cleanse
        for idx in range(len(final_records)):
            if final_records[idx] is None:
                fallback_row = batch[idx]
                raw_row_data = fallback_row.get("raw_row_data", fallback_row)
                
                # Standardize single row
                from utils import standardize_dataframe
                row_df = pd.DataFrame([fallback_row])
                cleaned_row_df = standardize_dataframe(row_df, original_had_sheet_source)
                if not cleaned_row_df.empty:
                    final_records[idx] = cleaned_row_df.to_dict('records')[0]
                else:
                    # Absolute emergency fallback
                    final_records[idx] = {
                        "Part Number (MPN)": "",
                        "Manufacturer": "",
                        "Description": "",
                        "Quantity": 1.0,
                        "UOM": "PCS",
                        "Customer Part No.": "",
                        "Internal Part Number": "",
                        "Category": "General"
                    }
                    
        return final_records
        
    except Exception as e:
        print(f"⚠️ Error running RAG grounded cleanse batch: {e}")
        # Global fallback to python standardize for safety
        from utils import standardize_dataframe
        batch_df = standardize_dataframe(pd.DataFrame(batch), original_had_sheet_source)
        return batch_df.to_dict('records')

async def clean_df_with_ollama(df: pd.DataFrame, original_had_sheet_source: bool = False, raw_mapping: dict = None) -> pd.DataFrame:
    """
    Standardize descriptions, manufacturer names, and part numbers in a DataFrame
    using batched local Ollama (Llama-3) JSON inference on raw spreadsheet rows.
    """
    import httpx
    import json
    import asyncio
    import pandas as pd
    
    if len(df) > 600:
        print(f"⚠️ BOM has {len(df)} rows. Bypassing slow LLM pre-cleansing for datasets > 600 rows to prevent HTTP timeouts and keep execution lightning-fast.")
        from utils import standardize_dataframe
        return standardize_dataframe(df, original_had_sheet_source)
        
    print(f"🧠 Initiating Ollama Cleanse for {len(df)} rows with concurrent batch processing.")
    
    # Process in batches of 15 to prevent context limit or latency issues
    batch_size = 15
    df_records = df.to_dict('records')
    
    batches = [df_records[i:i+batch_size] for i in range(0, len(df_records), batch_size)]
    
    sem = asyncio.Semaphore(3) # Bound concurrency to 3 active LLM calls to keep CPU light
    
    async with httpx.AsyncClient() as client:
        tasks = [clean_batch(sem, client, batch, original_had_sheet_source, raw_mapping) for batch in batches]
        results = await asyncio.gather(*tasks)
        
    cleaned_records = []
    for r in results:
        cleaned_records.extend(r)
        
    return pd.DataFrame(cleaned_records)

async def generate_rag_reasoning(sem: asyncio.Semaphore, client: httpx.AsyncClient, row_info: dict, candidates: list) -> str:
    """
    Query local Ollama concurrently to analyze vector candidates against the original BOM specifications
    and generate a technically sound RAG decision and justification.
    """
    async with sem:
        # Prepare list of candidates for the LLM context (up to 3)
        cand_texts = []
        for idx, c in enumerate(candidates[:3]):
            chunk = c.get("chunk", {})
            cand_texts.append(
                f"Candidate {idx+1}: "
                f"RS Stock No: {c.get('product_id', 'N/A')}, "
                f"MPN: {chunk.get('mpn', 'N/A')}, "
                f"Manufacturer: {chunk.get('manufacturer', 'N/A')}, "
                f"Description: {chunk.get('description', 'N/A')}"
            )
        
        candidates_context = "\n".join(cand_texts) if cand_texts else "No candidates found in database."
        
        prompt = (
            "You are an expert component engineering database assistant for RS Components.\n"
            "Your task is to analyze the original BOM item specifications against the vector candidates and generate a highly precise, 1-sentence RAG technical justification explaining if there is a match and why.\n\n"
            "Original BOM specifications:\n"
            f"- MPN: {row_info.get('mpn', 'N/A')}\n"
            f"- Manufacturer: {row_info.get('manufacturer', 'N/A')}\n"
            f"- Description: {row_info.get('desc', 'N/A')}\n\n"
            "Candidates from Vector DB:\n"
            f"{candidates_context}\n\n"
            "Requirements for your output:\n"
            "1. If a candidate is a functional match, explain why (e.g., 'Selected Murata GRM21... because both specify a 10uF 16V SMD ceramic capacitor providing 100% technical compatibility').\n"
            "2. If no candidate is a functional match, explain why (e.g., 'No matching candidates found; retrieved parts differ in capacitance and packaging specifications').\n"
            "3. Be extremely technical and keep your response to exactly 1 short sentence (max 25 words).\n"
            "4. Do NOT include markdown styling (like bolding or italics), and do NOT say 'Here is the justification:'. Return ONLY the single-sentence justification itself."
        )
        
        try:
            response = await client.post(
                "http://172.17.0.1:8010/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False
                },
                timeout=20.0
            )
            if response.status_code == 200:
                res_json = response.json()
                reasoning = res_json.get("response", "").strip()
                reasoning = reasoning.strip('"\'')
                if reasoning.lower().startswith("here is"):
                    parts = reasoning.split(":", 1)
                    if len(parts) > 1:
                        reasoning = parts[1].strip()
                return reasoning
        except Exception as e:
            logger.error(f"Failed to generate RAG justification: {e}")
            
        return ""

@router.post("/search-bom")
@catch_route_exceptions
async def search_bom(file: UploadFile = File(...), top_k: int = Query(5), use_llm: bool = Query(False), use_rag: bool = Query(False)):
    """
    The Master RS BOM Search Engine.
    Takes an unstructured BOM and performs high-precision semantic alignment.
    """
    if not state.vdb_built:
        return {"status": "success", "results": [], "report": [], "summary": [], "headers": [], "originalHeaders": [], "total_found": 0, "message": "VDB Not Built"}

    content = await file.read()
    df, original_headers = parse_bom_to_df(content, file.filename)
    if df is None or df.empty:
        raise HTTPException(status_code=400, detail="Failed to parse BOM or BOM is empty.")
        
    df_raw = df.fillna("").copy()
    raw_original_headers = [str(h) for h in original_headers]
        
    # Standardize DataFrame first to split wires, extract UOM, and conditionally exclude Sheet Source
    from utils import standardize_dataframe, check_if_sheet_source_in_excel, identify_columns
    original_had_sheet_source = check_if_sheet_source_in_excel(content, file.filename)
    
    # Clean data via Ollama if requested (passing raw DataFrame for end-to-end AI cleansing)
    if use_llm:
        raw_mapping = identify_columns(df, vdb_search_func=qdrant_db.search)
        df = await clean_df_with_ollama(df, original_had_sheet_source, raw_mapping)
    else:
        df = standardize_dataframe(df, original_had_sheet_source)
        
    original_headers = list(df.columns)
        
    # Save the cleaned flat DataFrame to state for immediate download!
    import io
    output = io.BytesIO()
    df.to_excel(output, index=False)
    state.cleaned_bom_bytes = output.getvalue()

    # Identify columns using RS expert heuristics, utilizing vector feedback
    mapping = identify_columns(df, vdb_search_func=qdrant_db.search)
    row_metadata = []
    queries = []

    import asyncio
    from fastapi.concurrency import run_in_threadpool

    # Pre-process all rows to determine exact MPNs
    pre_processed_rows = []
    
    for idx, row in df.iterrows():
        # Check if it's a wire row first!
        from utils import standardize_wire_row
        wire_mpn, wire_desc, wire_qty = standardize_wire_row(row, mapping, df.columns)
        if wire_mpn:
            mpn = wire_mpn
            desc = wire_desc
            qty = wire_qty
            mfg_clean = ""
            cust_part = ""
            intl_part = ""
        else:
            mpn = str(row.iloc[mapping.get('mpn', -1)]) if mapping.get('mpn', -1) >= 0 else ""
            desc = str(row.iloc[mapping.get('description', -1)]) if mapping.get('description', -1) >= 0 else ""
            qty = str(row.iloc[mapping.get('qty', -1)]) if mapping.get('qty', -1) >= 0 else "1"
            mfg = str(row.iloc[mapping.get('manufacturer', -1)]) if mapping.get('manufacturer', -1) >= 0 else ""
            mfg_clean = mfg.strip()
            if mfg_clean.lower() in ("nan", "none", "null"):
                mfg_clean = ""
            
            # Robust MPN Column Validation:
            # If the column identified as MPN actually contains a long description or a non-MPN token,
            # move its value to description and reset MPN so the regex-based deep scanners can find the real MPN.
            mpn_clean = mpn.strip()
            is_valid_mpn = is_valid_mpn_column_value(mpn_clean)
            
            if not is_valid_mpn and mpn_clean:
                if mpn_clean not in desc:
                    desc = f"{desc} {mpn_clean}".strip()
                mpn = ""
                
            cust_part = str(row.iloc[mapping.get('customer_part', -1)]) if mapping.get('customer_part', -1) >= 0 else ""
            intl_part = str(row.iloc[mapping.get('internal_part', -1)]) if mapping.get('internal_part', -1) >= 0 else ""
        
        # Robust fallbacks to display nice values in UI if no direct mapping exists
        if not cust_part and len(row) > 0:
            if 0 not in (mapping.get('mpn'), mapping.get('manufacturer'), mapping.get('qty'), mapping.get('description')):
                cust_part = str(row.iloc[0])
        if not intl_part and len(row) > 1:
            if 1 not in (mapping.get('mpn'), mapping.get('manufacturer'), mapping.get('qty'), mapping.get('description')):
                intl_part = str(row.iloc[1])
                
        row_category = str(row.iloc[mapping.get('commodity', -1)]) if mapping.get('commodity', -1) >= 0 else ""
        row_meta = {
            "i": idx, "mpn": mpn, "desc": desc, "qty": qty,
            "manufacturer": mfg_clean,
            "customer_part_no": cust_part,
            "internal_part": intl_part,
            "category": row_category.strip()
        }
        
        import re
        if not mpn:
            # Pass 1: Try to extract MPN from delimited text
            for val in row.values:
                val_str = str(val).strip()
                if not val_str or val_str in ("nan", "None", ""): continue
                
                if '|' in val_str or '/' in val_str:
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
                            row_meta['mpn'] = mpn
                        elif p != mpn:
                            desc = (desc + " " + p).strip() if desc else p
                            row_meta['desc'] = desc
                if mpn: break
                
            # Pass 2: If no MPN found, scan raw unstructured text, ignoring generic labels
            if not mpn:
                for val in row.values:
                    val_str = str(val).strip()
                    if not val_str or val_str in ("nan", "None", ""): continue
                    
                    # If it's a single word without spaces, test it directly
                    tokens = val_str.split()
                    for token in tokens:
                        if re.match(r'^[A-Z0-9\-\./:]{5,}$', token.upper()) and is_valid_mpn_token(token):
                            mpn = token
                            row_meta['mpn'] = mpn
                            break
                    
                    if mpn:
                        # Extract the remaining tokens as description
                        desc_parts = [t for t in tokens if t != mpn and not re.match(r'^\d+$', t)]
                        if desc_parts:
                            desc = (desc + " " + " ".join(desc_parts)).strip() if desc else " ".join(desc_parts)
                            row_meta['desc'] = desc
                        break
                        
        pre_processed_rows.append({
            'row': row,
            'mpn': mpn,
            'desc': desc,
            'mfg_clean': mfg_clean,
            'row_meta': row_meta
        })

    # Deduplicate exact database hits to achieve extremely high throughput and prevent context-switch or stampede delays
    unique_ids = set()
    unique_mpns = set()
    
    for r in pre_processed_rows:
        intl_val = r['row_meta']['internal_part']
        if intl_val:
            intl_clean = str(intl_val).strip()
            if intl_clean:
                unique_ids.add(intl_clean)
                
        mpn_val = r['mpn']
        if mpn_val:
            unique_mpns.add(mpn_val)
            # Add split parts to find exact matches for combined formats
            import re
            parts = [p.strip() for p in re.split(r'[/,\|]| or ', str(mpn_val)) if len(p.strip()) >= 3]
            for part in parts:
                unique_mpns.add(part)
            
    # Gather exact results using high-performance batch lookups to prevent network/pool overhead!
    unique_ids_list = list(unique_ids)
    unique_mpns_list = list(unique_mpns)
    id_lookup = await run_in_threadpool(qdrant_db.exact_id_search_batch, unique_ids_list)
    mpn_lookup = await run_in_threadpool(qdrant_db.exact_mpn_search_batch, unique_mpns_list)
            
    # Reconstruct exact_results_list mapping back to the sequential rows
    exact_results_list = []
    for r in pre_processed_rows:
        intl_val = r['row_meta']['internal_part']
        mpn_val = r['mpn']
        
        row_res = []
        if intl_val:
            intl_clean = str(intl_val).strip()
            if intl_clean in id_lookup and id_lookup[intl_clean]:
                row_res = id_lookup[intl_clean]
                
        if not row_res and mpn_val:
            if mpn_val in mpn_lookup:
                row_res = mpn_lookup[mpn_val]
            else:
                import re
                parts = [p.strip() for p in re.split(r'[/,\|]| or ', str(mpn_val)) if len(p.strip()) >= 3]
                for part in parts:
                    if part in mpn_lookup:
                        row_res = row_res + mpn_lookup[part]
                
        exact_results_list.append(row_res)

    # Second pass: Build queries and metadata mapping
    query_to_idx = {}
    unique_queries = []
    
    for r_data, exact_res in zip(pre_processed_rows, exact_results_list):
        row = r_data['row']
        mpn = r_data['mpn']
        desc = r_data['desc']
        mfg_clean = r_data['mfg_clean']
        row_meta = r_data['row_meta']
        
        if exact_res:
            row_meta['exact_result'] = exact_res

        query_text = f"{mfg_clean} {mpn} {desc}".strip()
        
        # Append any unmapped column values to ensure we don't drop critical data
        unmapped_text = []
        for i, val in enumerate(row.values):
            if i not in mapping.values() and pd.notna(val):
                val_str = str(val).strip()
                if len(val_str) > 2 and val_str not in query_text:
                    unmapped_text.append(val_str)
        
        if unmapped_text:
            query_text = f"{query_text} {' '.join(unmapped_text)}".strip()
            
        # Fallback for completely unstructured key-value BOMs where mapping failed
        if not query_text:
            query_text = " ".join([str(val) for val in row.values if pd.notna(val) and str(val).strip() != ""])
            
        row_meta['query_text'] = query_text
        
        # --- HIGH-SPEED BYPASS OPTIMIZATION ---
        # If there is already a valid exact match (where the manufacturer matches the query
        # or the query has no manufacturer specified), we bypass semantic vector search.
        # This keeps search speed high while ensuring we fall back to semantic search for collisions.
        has_valid_exact = False
        if exact_res:
            q_mfr = str(mfg_clean).strip().upper()
            if not q_mfr:
                has_valid_exact = True
            else:
                for hit in exact_res:
                    pay = hit.get('payload', {}) if isinstance(hit, dict) else (hit.payload if hasattr(hit, 'payload') else {})
                    cand_mfr = str(pay.get('manufacturer', '')).strip().upper()
                    if is_manufacturer_aligned(q_mfr, cand_mfr):
                        has_valid_exact = True
                        break
        
        if has_valid_exact:
            row_meta['query_index'] = -1
        else:
            if query_text:
                if query_text not in query_to_idx:
                    unique_queries.append(query_text)
                    query_to_idx[query_text] = len(unique_queries) - 1
                row_meta['query_index'] = query_to_idx[query_text]
            else:
                row_meta['query_index'] = -1
                
        row_metadata.append(row_meta)


    # Batch Search against Qdrant only for non-exact matches (Deduplicated queries for up to 5x higher throughput)
    batch_results = await run_in_threadpool(qdrant_db.search_batch, unique_queries, limit=top_k) if unique_queries else []
    
    # Reconstruct final results
    report = []
    
    # Validation Guards variables definition
    instruments = ['power source', 'power supply', 'electronic load', 'instrument', 'meter', 'tester', 'oscilloscope', 'multimeter', 'generator', 'analyzer', 'source', 'sources', 'pws', 'ps', 'load', 'loads', 'supply', 'supplies']
    consumables = ['cable', 'wire', 'connector', 'terminal', 'lug', 'shield', 'housing', 'header', 'socket', 'plug', 'heat shrink', 'tubing', 'sleeve', 'boot shrink', 'conn', 'con', 'term', 'cbl']
    mechanical = ['enclosure', 'enclosures', 'screw', 'nut', 'bolt', 'fastener', 'hardware', 'bracket', 'washer', 'standoff', 'spacer', 'rivet', 'anchor', 'nail', 'slotted', 'pan head', 'flat head', 'cap head', 'scr', 'screws', 'nuts', 'bolts', 'washers']
    passives = ['resistor', 'capacitor', 'diode', 'transistor', 'ic', 'chip', 'inductor', 'relay', 'fuse', 'led', 'res', 'cap', 'diod', 'tran']

    mro_groups = {
        "filters": {"filter", "filters", "filtration", "strainer", "element"},
        "bearings": {"bearing", "bearings", "bushing", "bushings", "ball bearing", "roller bearing"},
        "fasteners": {"fastener", "fasteners", "screw", "screws", "nut", "nuts", "bolt", "bolts", "washer", "washers", "standoff", "standoffs", "spacer", "spacers", "rivet", "rivets", "fixing", "fixings", "stud", "studs", "hardware"},
        "valves_pneumatics": {"valve", "valves", "pneumatic", "pneumatics", "hydraulic", "hydraulics", "fitting", "fittings", "coupling", "couplings", "hose", "hoses", "cylinder", "cylinders", "regulator", "regulators"},
        "electrical": {"fuse", "fuses", "breaker", "breakers", "switch", "switches", "relay", "relays", "resistor", "resistors", "capacitor", "capacitors", "diode", "diodes", "transistor", "transistors", "cable", "cables", "wire", "wires", "connector", "connectors"}
    }

    import re
    # Precompile regex patterns once outside the loops for massive 100x performance boost!
    instruments_pattern = re.compile(r'\b(?:' + '|'.join(map(re.escape, instruments)) + r')\b')
    consumables_pattern = re.compile(r'\b(?:' + '|'.join(map(re.escape, consumables)) + r')\b')
    mechanical_pattern = re.compile(r'\b(?:' + '|'.join(map(re.escape, mechanical)) + r')\b')
    passives_pattern = re.compile(r'\b(?:' + '|'.join(map(re.escape, passives)) + r')\b')
    
    clean_alpha_pattern = re.compile(r'[^A-Z0-9]')
    token_pattern = re.compile(r'\b[a-z0-9]{2,}\b')
    ratings_pattern = re.compile(r'\b(\d+(?:\.\d+)?)\s*(v|vac|vdc|w|watt|watts|volt|volts|a|amp|amps|ma|milliamp|milliamps)\b')
    thread_pattern = re.compile(r'\bM(\d+)\b', re.IGNORECASE)
    mm_pattern = re.compile(r'\b(\d+(?:\.\d+)?)\s*mm\b', re.IGNORECASE)

    def clean_alphanumeric(text: str) -> str:
        return clean_alpha_pattern.sub('', str(text).upper())

    def has_keywords(text: str, compiled_pattern) -> bool:
        if not text: return False
        return bool(compiled_pattern.search(text.lower()))

    def extract_ratings(text: str):
        # Pre-process text to remove false-positive Harting size codes (e.g. Han 3A, Han 4A, Han 10A, Han A)
        # that could otherwise be misidentified as amperage ratings.
        text_lower = str(text).lower()
        import re
        text_clean = re.sub(r'\bhan\s*\-?\s*\d*a\b', '', text_lower)
        
        matches = ratings_pattern.findall(text_clean)
        ratings = {}
        for val, unit in matches:
            try:
                val_num = float(val)
                if val_num == 0: continue
                if unit in ('v', 'vac', 'vdc', 'volt', 'volts'):
                    ratings['v'] = val_num
                elif unit in ('w', 'watt', 'watts'):
                    ratings['w'] = val_num
                elif unit in ('a', 'amp', 'amps'):
                    ratings['a'] = val_num
                elif unit in ('ma', 'milliamp', 'milliamps'):
                    ratings['a'] = val_num / 1000.0
            except ValueError:
                continue
        return ratings

    stop_words = {
        'original', 'sl.no', 'device', 'qty', 'quantity', 'unstructured', 'bom', 'row', 'item', 'column', 'value',
        'and', 'the', 'for', 'with', 'off', 'out', 'bench', 'mixed', 'features', 'range', 'series', 'brand',
        'type', 'size', 'color', 'black', 'white', 'red', 'blue', 'green', 'yellow', 'pcs', 'pack', 'set'
    }
    
    def get_clean_tokens(text: str):
        tokens = token_pattern.findall(text.lower())
        return set([t for t in tokens if t not in stop_words])

    for meta in row_metadata:
        candidates = []
        seen_ids = set()
        
        # 1. Gather exact match candidates
        exact_list = meta.get('exact_result', [])
        for hit in exact_list:
            hit_id = hit.get('id')
            if hit_id not in seen_ids:
                pay = hit.get('payload', {})
                qdrant_db.restore_zeros_from_query(pay, meta.get('query_text', ''))
                qdrant_db.restore_zeros_from_query(pay, meta.get('mpn', ''))
                h_mpn = str(pay.get('mpn', '')).upper()
                h_desc = str(pay.get('description', '')).upper()
                if is_blocklisted(h_mpn, h_desc):
                    continue
                seen_ids.add(hit_id)
                candidates.append({
                    "hit": hit,
                    "is_exact": True
                })
                
        # 2. Gather semantic match candidates
        sem_list = []
        q_idx = meta.get('query_index', -1)
        if q_idx >= 0 and q_idx < len(batch_results):
            sem_list = batch_results[q_idx]
            
        # 2.5 Description keyword fallback if there is a query description but no exact results
        desc_str = meta.get('desc', '')
        if not exact_list and desc_str and len(str(desc_str).strip()) >= 3:
            desc_fallback = await run_in_threadpool(qdrant_db.description_keyword_search, desc_str, 15)
            sem_list = sem_list + desc_fallback
            
        for hit in sem_list:
            hit_id = hit.get('id')
            if hit_id not in seen_ids:
                    pay = hit.get('payload', {})
                    qdrant_db.restore_zeros_from_query(pay, meta.get('query_text', ''))
                    qdrant_db.restore_zeros_from_query(pay, meta.get('mpn', ''))
                    h_mpn = str(pay.get('mpn', '')).upper()
                    h_desc = str(pay.get('description', '')).upper()
                    if is_blocklisted(h_mpn, h_desc):
                        continue
                    seen_ids.add(hit_id)
                    candidates.append({
                        "hit": hit,
                        "is_exact": False
                    })
                    
        # 3. Score all candidates using the advanced guards
        scored_candidates = []
        
        # Hoist row-specific calculations outside the inner candidate loop to optimize 10x!
        query_text_str = meta.get('query_text', '') or f"{meta['mpn']} {meta['desc']}".strip()
        q_mpn_alpha = clean_alphanumeric(meta['mpn'])
        is_query_instrument = has_keywords(query_text_str, instruments_pattern)
        is_query_consumable = has_keywords(query_text_str, consumables_pattern)
        is_query_passive = has_keywords(query_text_str, passives_pattern)
        is_query_mechanical = has_keywords(query_text_str, mechanical_pattern) and not (is_query_consumable or is_query_passive)
        query_desc_str = str(meta.get('desc', '')).strip().lower()
        q_tokens = get_clean_tokens(query_desc_str) if query_desc_str else set()
        q_ratings = extract_ratings(query_text_str)
        q_threads = set(thread_pattern.findall(query_text_str))
        q_mms = set(mm_pattern.findall(query_text_str))

        for cand in candidates:
            hit = cand["hit"]
            is_exact = cand["is_exact"]
            
            pay = hit.get('payload', {})
            score = hit.get('score', 0)
            # Normalize MPNs to check if it's a full normalized exact MPN match
            pay_mpn = str(pay.get('mpn', ''))
            pay_mfr = str(pay.get('manufacturer', ''))
            pay_desc = str(pay.get('description', ''))
            
            mfg_clean_upper = str(meta.get('manufacturer', '')).strip().upper()
            mfr_match = is_manufacturer_aligned(mfg_clean_upper, pay_mfr)
            
            norm_q_mpn = clean_alphanumeric(meta['mpn']).lstrip('0')
            norm_pay_mpn = clean_alphanumeric(pay_mpn).lstrip('0')
            norm_pay_pid = clean_alphanumeric(pay.get('product_id', '')).lstrip('0')
            
            is_mpn_match = (norm_q_mpn != "" and norm_q_mpn == norm_pay_mpn)
            is_pid_match = (norm_q_mpn != "" and norm_q_mpn == norm_pay_pid)
            
            is_full_exact_mpn = is_mpn_match or (is_pid_match and mfr_match)
            
            # Determine if this is a semantic-only candidate
            is_sem_only = not is_full_exact_mpn and not is_exact
            
            # Apply the custom confidence scoring matrix based on relationship and brand alignment
            base_conf = calculate_confidence_score(meta['mpn'], pay_mpn, pay.get('product_id', ''), mfr_match, is_semantic_only=is_sem_only)
            
            pay_desc = str(pay.get('description', '')).lower()
            pay_mpn = str(pay.get('mpn', '')).lower()
            pay_mfr = str(pay.get('manufacturer', '')).lower()
            pay_full_desc = f"{pay_desc} {str(pay.get('category', ''))}".strip()
            
            is_result_instrument = has_keywords(pay_full_desc, instruments_pattern)
            is_result_consumable = has_keywords(pay_full_desc, consumables_pattern)
            is_result_passive = has_keywords(pay_full_desc, passives_pattern)
            is_result_mechanical = has_keywords(pay_full_desc, mechanical_pattern) and not (is_result_consumable or is_result_passive)
            
            cand_conf = base_conf
            downgrade_reasons = []
            category_conflict = False
            
            has_mfr_contradiction = False
            has_category_contradiction = False
            has_mpn_contradiction = False
            has_rating_contradiction = False
            has_mechanical_contradiction = False
            
            # Instrument category conflicts are absolute (e.g. power supply matching a screw or connector)
            # and must be checked even for exact matches to prevent model number collisions!
            # Instrument vs mechanical
            if (is_query_instrument and is_result_mechanical) or (is_query_mechanical and is_result_instrument):
                category_conflict = True
                cand_conf = 30
                has_category_contradiction = True
                downgrade_reasons.append("Category conflict (Instrument vs Mechanical)")
            # Instrument vs consumable
            elif (is_query_instrument and is_result_consumable) or (is_query_consumable and is_result_instrument):
                category_conflict = True
                cand_conf = 40
                has_category_contradiction = True
                downgrade_reasons.append("Category conflict (Instrument vs Consumable)")
            # Instrument vs passive
            elif (is_query_instrument and is_result_passive) or (is_query_passive and is_result_instrument):
                category_conflict = True
                cand_conf = 30
                has_category_contradiction = True
                downgrade_reasons.append("Category conflict (Instrument vs Passive)")
            
            # Guard 3: Description Word Overlap Guard (Consolidated)
            # Checked for zero-overlap to prevent generic model number collisions (only for non-exact matches)
            is_exact_mpn_clean = (norm_q_mpn != "" and (norm_q_mpn == norm_pay_mpn or norm_q_mpn == norm_pay_pid))
            if query_desc_str and pay_desc and not is_full_exact_mpn and not is_exact_mpn_clean:
                res_tokens = get_clean_tokens(pay_desc)
                if q_tokens:
                    overlap = q_tokens.intersection(res_tokens)
                    overlap_ratio = len(overlap) / len(q_tokens)
                    if overlap_ratio == 0 and len(q_tokens) >= 2:
                        # Check if it is a prefix/suffix match
                        is_similar_mpn = False
                        if norm_q_mpn and norm_pay_mpn:
                            q_num = norm_q_mpn.lstrip('0')
                            p_num = norm_pay_mpn.lstrip('0')
                            if q_num in p_num or p_num in q_num:
                                is_similar_mpn = True
                                
                        if is_similar_mpn:
                            # Similar prefix/suffix variant matches are governed by the custom matrix, bypass cap
                            pass
                        else:
                            cand_conf = min(cand_conf, 45)
                            downgrade_reasons.append("No common description keywords (potential model number collision)")
                    elif not is_exact and not category_conflict and overlap_ratio >= 0.75:
                        # High keyword overlap boost for semantic matches
                        if cand_conf < 95:
                            boost = int(10 * overlap_ratio)
                            cand_conf = min(98, cand_conf + boost)
                            logger.info(f"✨ Description Overlap Boost: {pay_mpn} boosted by +{boost}% to {cand_conf}% (overlap ratio: {overlap_ratio:.2f})")
            
            # Run other validation guards to detect and flag mismatches for all candidates (exact and semantic)
            if True:
                # Guard 0: Manufacturer Match/Mismatch Check
                mfg_clean_upper = str(meta.get('manufacturer', '')).strip().upper()
                mfr_match = is_manufacturer_aligned(mfg_clean_upper, pay_mfr)
                
                if not mfr_match:
                    has_mfr_contradiction = True
                    downgrade_reasons.append(f"Manufacturer mismatch ({meta.get('manufacturer')} vs {pay.get('manufacturer')})")

                # Guard 1: MPN Strictness using format-insensitive clean alphanumeric comparison
                pay_mpn_alpha = clean_alphanumeric(pay_mpn)
                
                if q_mpn_alpha and pay_mpn_alpha:
                    q_mpn_numeric = q_mpn_alpha.lstrip('0')
                    pay_mpn_numeric = pay_mpn_alpha.lstrip('0')
                    pay_pid_numeric = clean_alphanumeric(pay.get('product_id', '')).lstrip('0')
                    
                    if q_mpn_numeric == pay_pid_numeric:
                        # Exact stock number/product ID match, do not flag as MPN mismatch
                        pass
                    elif (q_mpn_numeric not in pay_mpn_numeric and pay_mpn_numeric not in q_mpn_numeric
                          and q_mpn_numeric not in pay_pid_numeric and pay_pid_numeric not in q_mpn_numeric):
                        # Check if the candidate MPN is explicitly written inside the query description
                        desc_clean = clean_alphanumeric(meta.get('desc', ''))
                        if pay_mpn_alpha and pay_mpn_alpha in desc_clean:
                            cand_conf = min(cand_conf, 80)
                        else:
                            cand_conf = min(cand_conf, 40)
                            has_mpn_contradiction = True
                            downgrade_reasons.append("MPN prefix/suffix mismatch")
                        
                # Guard 2: Category Conflict Guard for non-instrument conflicts
                # Passive vs mechanical
                if not category_conflict:
                    if (is_query_passive and is_result_mechanical) or (is_query_mechanical and is_result_passive):
                        category_conflict = True
                        cand_conf = min(cand_conf, 40)
                        has_category_contradiction = True
                        downgrade_reasons.append("Category conflict (Passive vs Mechanical)")
                # Consumable vs mechanical
                if not category_conflict:
                    if (is_query_consumable and is_result_mechanical) or (is_query_mechanical and is_result_consumable):
                        category_conflict = True
                        cand_conf = min(cand_conf, 40)
                        has_category_contradiction = True
                        downgrade_reasons.append("Category conflict (Consumable vs Mechanical)")
    
                # Guard 2.5: MRO Category Group Guard (Preventing Filters matching Fasteners, Bearings, etc.)
                q_category = str(meta.get('category', '')).strip().lower()
                if q_category and not category_conflict:
                    cand_div = str(pay.get('category', '')).strip().lower()
                    cand_fam = str(pay.get('family', '')).strip().lower()
                    cand_seg = str(pay.get('segment', '')).strip().lower()
                    cand_grp = str(pay.get('product_group', '')).strip().lower()
                    cand_full_context = f"{pay_desc} {cand_div} {cand_fam} {cand_seg} {cand_grp}".lower()
                    
                    # Check which group the query category belongs to
                    query_group = None
                    for group_name, keywords in mro_groups.items():
                        if any(kw in q_category for kw in keywords):
                            query_group = group_name
                            break
                    
                    if query_group:
                        # Check if the candidate has any keyword from the query's group
                        has_group_match = any(kw in cand_full_context for kw in mro_groups[query_group])
                        
                        if not has_group_match:
                            # Candidate doesn't match the query group. Let's see if it matches another incompatible MRO group!
                            matched_other_group = None
                            for group_name, keywords in mro_groups.items():
                                if group_name != query_group:
                                    if any(kw in cand_full_context for kw in keywords):
                                        matched_other_group = group_name
                                        break
                                        
                            if matched_other_group:
                                cand_conf = min(cand_conf, 10)
                                category_conflict = True
                                has_category_contradiction = True
                                downgrade_reasons.append(
                                    f"Category mismatch (Original Category: {q_category.capitalize()} vs Matched: {cand_fam.capitalize() or cand_div.capitalize()})"
                                )
                                
                # Guard 4: Physical Parameter Conflict Guard
                res_ratings = extract_ratings(pay_desc)
    
                rating_conflict = False
                for key in ('v', 'w', 'a'):
                    if key in q_ratings and key in res_ratings:
                        q_val = q_ratings[key]
                        res_val = res_ratings[key]
                        if abs(q_val - res_val) / max(q_val, res_val) > 0.1:
                            rating_conflict = True
                            break
    
                if rating_conflict:
                    cand_conf = min(cand_conf, 30)
                    has_rating_contradiction = True
                    downgrade_reasons.append("Rating/parameter mismatch (V/W/A)")
                    
                # Guard 5: Mechanical Parameter Guard
                res_threads = set(thread_pattern.findall(pay_desc))
                res_mms = set(mm_pattern.findall(pay_desc))
                
                if q_threads and res_threads:
                    if not q_threads.intersection(res_threads):
                        cand_conf = min(cand_conf, 75) if is_full_exact_mpn else min(cand_conf, 30)
                        has_mechanical_contradiction = True
                        q_thread_str = ", ".join([f"M{t}" for t in q_threads])
                        res_thread_str = ", ".join([f"M{t}" for t in res_threads])
                        downgrade_reasons.append(f"Thread size mismatch ({q_thread_str} vs {res_thread_str})")
                
                if q_mms and res_mms:
                    try:
                        q_floats = [float(x) for x in q_mms]
                        res_floats = [float(x) for x in res_mms]
                        has_dimension_overlap = False
                        for q_f in q_floats:
                            for r_f in res_floats:
                                if max(q_f, r_f) > 0 and abs(q_f - r_f) / max(q_f, r_f) <= 0.05:
                                    has_dimension_overlap = True
                                    break
                            if has_dimension_overlap:
                                break
                        if not has_dimension_overlap:
                            cand_conf = min(cand_conf, 75) if is_full_exact_mpn else min(cand_conf, 35)
                            has_mechanical_contradiction = True
                            q_mm_str = ", ".join([f"{x}mm" for x in q_mms])
                            res_mm_str = ", ".join([f"{x}mm" for x in res_mms])
                            downgrade_reasons.append(f"Mechanical dimension mismatch ({q_mm_str} vs {res_mm_str})")
                    except ValueError:
                        pass
                        
                if not downgrade_reasons and cand_conf < 75:
                    downgrade_reasons.append("Low baseline semantic similarity")
            
            raw_val = pay.get('raw_data', '')
            prod_id = pay.get('product_id')
            if not prod_id and raw_val:
                raw_parts = str(raw_val).split('|')
                if raw_parts:
                    prod_id = raw_parts[0]
            
            # Save it back to the dictionary for UI compatibility
            pay['product_id'] = prod_id
            
            query_tokens_set = set(q_tokens)
            res_tokens_set = set(res_tokens) if 'res_tokens' in locals() else set(get_clean_tokens(pay_desc))
            intersection = query_tokens_set & res_tokens_set
            union = query_tokens_set | res_tokens_set
            match_rate = len(intersection) / len(union) if union else 0
            
            h_mpn_val = pay.get('mpn')
            h_mfr_val = pay.get('manufacturer')
            is_generic = 1 if (not h_mpn_val or str(h_mpn_val).lower().strip() in ('nan', 'none', '', ' ') or str(h_mfr_val).upper().strip() == 'NEUTRAL') else 0
            
            if has_mfr_contradiction:
                # The matrix score already accounts for manufacturer mismatch, so we do not double-penalize.
                pass
                            
            scored_candidates.append({
                "score": cand_conf / 100.0,
                "confidence_score": cand_conf,
                "match_rate": match_rate,
                "is_generic": is_generic,
                "chunk": pay,
                "product_id": prod_id,
                "manufacturer": pay.get('manufacturer'),
                "description": pay.get('description'),
                "uom": pay.get('uom', ''),
                "moq": pay.get('moq', ''),
                "raw_data": raw_val,
                "reasons": downgrade_reasons,
                "is_full_exact_mpn": is_full_exact_mpn,
                "has_mfr_contradiction": has_mfr_contradiction,
                "has_category_contradiction": has_category_contradiction,
                "has_mpn_contradiction": has_mpn_contradiction,
                "has_rating_contradiction": has_rating_contradiction,
                "has_mechanical_contradiction": has_mechanical_contradiction
            })
            
        # Sort candidates descending:
        # 1. Primary: confidence_score (higher is better)
        # 2. Secondary: MPN substring presence in query text (tie-breaker)
        # 3. Tertiary: is_generic (0 is branded/better, 1 is generic/worse)
        # 4. Quaternary: Jaccard match_rate (higher is better)
        def get_sorting_key(x):
            cand_mpn = clean_alphanumeric(x.get("chunk", {}).get("mpn", ""))
            query_clean = clean_alphanumeric(meta.get("query_text", ""))
            has_mpn_in_query = 1 if (cand_mpn and cand_mpn in query_clean) else 0
            return (
                x.get("confidence_score", 0),
                has_mpn_in_query,
                -x.get("is_generic", 0),
                x.get("match_rate", 0.0)
            )
        scored_candidates.sort(key=get_sorting_key, reverse=True)
        scored_candidates = scored_candidates[:top_k]
        
        # Select the top candidate as the primary match
        top = scored_candidates[0] if scored_candidates else {}
        final_conf = top.get("confidence_score", 0) if top else 0
        pay = top.get("chunk", {}) if top else {}
        
        # Enforce strict description and category relevance checks for auto-promotion to 100%
        is_relevant_for_promotion = False
        
        if top:
            # Check if the top candidate satisfies all strict promotion criteria:
            # 1. Complete normalized Input MPN exactly matches complete normalized Catalog MPN.
            # 2. Manufacturer does not contradict catalog manufacturer.
            # 3. No critical category, specification, physical, electrical, or dimensional contradiction is detected.
            top_is_full_exact_mpn = top.get("is_full_exact_mpn", False)
            top_has_mfr_contradiction = top.get("has_mfr_contradiction", False)
            top_has_category_contradiction = top.get("has_category_contradiction", False)
            top_has_mpn_contradiction = top.get("has_mpn_contradiction", False)
            top_has_rating_contradiction = top.get("has_rating_contradiction", False)
            top_has_mechanical_contradiction = top.get("has_mechanical_contradiction", False)
            
            if (top_is_full_exact_mpn 
                and not top_has_mfr_contradiction 
                and not top_has_category_contradiction 
                and not top_has_mpn_contradiction 
                and not top_has_rating_contradiction 
                and not top_has_mechanical_contradiction):
                is_relevant_for_promotion = True
        
        # Promote any valid match to 100% ONLY if it satisfies all strict promotion conditions
        # (Must be a strict exact string match in terms of the original formatting to get 100%)
        if final_conf >= 60 and is_relevant_for_promotion:
            if top.get("confidence_score", 0) >= 99:
                final_conf = 100
            
        # STRICT CONFIDENCE FILTER: Prevent garbage matches (< 15%) from auto-suggesting,
        # but retain the original internal score and reasons for debugging and model improvement.
        original_conf = final_conf
        rejection_reasons = list(top.get("reasons", [])) if top else ["No candidates found"]
        if not rejection_reasons and original_conf < 15:
            rejection_reasons = ["Low baseline similarity score"]
            
        if final_conf < 15:
            final_conf = 0
            ai_status = f"Discarded: {', '.join(rejection_reasons)} (Original: {original_conf}%)" if rejection_reasons else f"Discarded (Low Confidence {original_conf}%)"
        else:
            ai_status = "Verified by VDB" if final_conf == 100 else "Pending Review"
            if final_conf < 75:
                top_reasons = top.get("reasons", [])
                if top_reasons:
                    ai_status = f"Low Confidence: {', '.join(top_reasons)}"
                else:
                    ai_status = f"Low Confidence ({final_conf}%)"
        
        report.append({
            "row_number": meta['i'] + 1,
            "qty": meta['qty'],
            "project_qty": meta['qty'], # index.html expects this
            "customer_part_no": meta['customer_part_no'],
            "internal_part": meta['internal_part'],
            "specification": meta['mpn'],
            "uom": pay.get('uom', '') if pay else '',
            "moq": pay.get('moq', '') if pay else '',
            "confidence_score": final_conf,
            "original_confidence_score": original_conf,
            "rejection_reasons": rejection_reasons,
            "chunk": pay,
            "internal_data": pay.get('raw_data', f"{pay.get('product_id')}|{pay.get('manufacturer')}|{pay.get('description')}") if pay else '',
            "bom_columns": list(df_raw.iloc[meta['i']]),
            "matches": scored_candidates,          # for index.html dropdown/selection list
            "all_sourcelists": scored_candidates,   # for index.html compatibility
            "ai_status": ai_status
        })

    # 4. Optional: Run High-Precision RAG Reasoning (gated by use_llm and use_rag)
    if use_llm and use_rag and report:
        import httpx
        import asyncio
        
        # Enterprise Optimization: Focus RAG reasoning exclusively on ambiguous, low-confidence matches 
        # (15% to 80% confidence) that have vector candidates but no exact VDB database match.
        # This prevents wasting LLM tokens on obvious matches or zero-matches!
        rag_candidates = []
        for item in report:
            conf = item.get("confidence_score", 0)
            if 15 <= conf <= 80 and item.get("matches"):
                # Skip if there is an exact match in the candidate list
                if not any(c.get("is_exact") for c in item["matches"]):
                    rag_candidates.append(item)
                    
        # Apply a safety cap of max 40 items to prevent HTTP browser timeouts on large (5,000+) uploads
        rag_candidates = rag_candidates[:40]
        
        if rag_candidates:
            sem = asyncio.Semaphore(3)  # Max 3 concurrent LLM requests to prevent CPU/GPU overload
            async with httpx.AsyncClient() as client:
                tasks = []
                for item in rag_candidates:
                    # Prepare specification context
                    row_info = {
                        "mpn": item["specification"],
                        "manufacturer": item["chunk"].get("manufacturer", "") if item["chunk"] else "",
                        "desc": item["chunk"].get("description", "") if item["chunk"] else ""
                    }
                    # If no MPN or spec, get it from the raw query_text from metadata
                    idx = item["row_number"] - 1
                    if idx < len(row_metadata):
                        meta = row_metadata[idx]
                        if not row_info["mpn"]:
                            row_info["mpn"] = meta.get("mpn")
                        if not row_info["manufacturer"]:
                            row_info["manufacturer"] = meta.get("manufacturer")
                        if not row_info["desc"]:
                            row_info["desc"] = meta.get("desc")
                    
                    # Pass candidates (matches list)
                    tasks.append(
                        generate_rag_reasoning(
                            sem, client, row_info, item["matches"]
                        )
                    )
                
                # Execute all RAG generations concurrently!
                rag_results = await asyncio.gather(*tasks)
                
                # Inject RAG reasoning back into report items
                for item, reasoning in zip(rag_candidates, rag_results):
                    if reasoning:
                        item["ai_status"] = f"🧠 RAG: {reasoning}"

    state.results = {
        "status": "success",
        "results": report, 
        "report": report, # Backwards compatibility
        "summary": report, # Standard for index.html
        "headers": raw_original_headers,
        "originalHeaders": raw_original_headers, # UI specific
        "total_found": len(report)
    }
    return state.results

@router.get("/results")
async def get_results():
    """Retrieve the last processed RS BOM results."""
    return state.results if state.results else {"status": "success", "results": [], "report": [], "summary": [], "headers": [], "originalHeaders": [], "total_found": 0}

@router.post("/save-consolidated")
async def save_consolidated(items_json: str = Form(...), headers_json: str = Form("[]")):
    """Save the consolidated and refined BOM items to the memory state for short-lived export sessions."""
    import json
    try:
        state.consolidated_results = json.loads(items_json)
        state.consolidated_headers = json.loads(headers_json)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to save results: {str(e)}")

@router.get("/export-cleaned-source")
async def export_cleaned_source():
    """Retrieve and export the last parsed cleaned flat BOM source as an Excel spreadsheet."""
    import io
    if not hasattr(state, 'cleaned_bom_bytes') or not state.cleaned_bom_bytes:
        raise HTTPException(status_code=400, detail="No cleaned BOM source found. Please upload a BOM file first.")
        
    return StreamingResponse(
        io.BytesIO(state.cleaned_bom_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            'Content-Disposition': 'attachment; filename="Cleaned_Flat_BOM.xlsx"',
            'Cache-Control': 'no-cache'
        }
    )

@router.get("/export-consolidated")
async def export_consolidated():
    """Export the consolidated and refined BOM items directly to a formatted Excel file via a standard GET download."""
    import io
    import pandas as pd
    try:
        items = getattr(state, 'consolidated_results', [])
        original_headers = getattr(state, 'consolidated_headers', [])
        if not items:
            raise HTTPException(status_code=400, detail="No consolidated results found in current session.")
            
        # Sort items by original row_number to preserve the input BOM order details
        items = sorted(items, key=lambda x: x.get('row_number', 999999))
        
        data = []
        s_no = 1
        for item in items:
            c_mpn = str(item.get('catalog_mpn', '')).upper()
            c_desc = str(item.get('description', '')).upper()
            if is_blocklisted(c_mpn, c_desc):
                continue
            
            row_dict = {
                "S.No": s_no,
                "Qty": float(item.get('qty', 1.0)),
                "Stock Number": item.get('stock_number', ''),
                "Division": item.get('division', ''),
                "Category": item.get('category', ''),
                "Sub-Category": item.get('sub_category', ''),
                "Product Group": item.get('product_group', ''),
                "Catalog MPN": item.get('catalog_mpn', ''),
                "Brand": item.get('brand', ''),
                "UOM": item.get('uom', ''),
                "MOQ": item.get('moq', ''),
                "Detailed Description": item.get('description', ''),
                "Confidence": item.get('confidence', '100%'),
                "AI Status": item.get('ai_status', 'Expert Approved')
            }
            
            bom_cols = item.get('bom_columns', [])
            for idx, h in enumerate(original_headers):
                val = bom_cols[idx] if idx < len(bom_cols) else ''
                row_dict[f"Input - {h}"] = val
                
            data.append(row_dict)
            s_no += 1
            
        df = pd.DataFrame(data)
        
        # Write Excel to BytesIO buffer
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='Consolidated Sourcing', index=False)
            workbook  = writer.book
            worksheet = writer.sheets['Consolidated Sourcing']
            
            # Format Definitions
            header_format = workbook.add_format({
                'bold': True,
                'text_wrap': True,
                'valign': 'vcenter',
                'align': 'center',
                'fg_color': '#0F172A',
                'font_color': '#F8FAFC',
                'border': 1
            })
            
            cell_format = workbook.add_format({
                'valign': 'vcenter',
                'border': 1
            })
            
            # Apply header format
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_format)
                
            # Autofit Columns
            for idx, col in enumerate(df):
                series = df[col]
                max_len = max(
                    series.astype(str).map(len).max(),
                    len(str(series.name))
                ) + 3
                worksheet.set_column(idx, idx, min(max_len, 50), cell_format)
                
        output.seek(0)
        xlsx_data = output.getvalue()
        
        headers = {
            'Content-Disposition': 'attachment; filename="RS_Consolidated_Sourcing_Report.xlsx"',
            'Content-Length': str(len(xlsx_data))
        }
        return Response(content=xlsx_data, headers=headers, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")

class AlignmentTelemetry(BaseModel):
    anchor: str
    positive_mpn: str
    positive_description: str
    negative_description: Optional[str] = None

@router.post("/telemetry/alignment")
async def save_alignment_telemetry(data: List[AlignmentTelemetry]):
    """Harvest human-verified BOM alignments for future model fine-tuning."""
    import json
    import os
    os.makedirs("./model_cache/training_data", exist_ok=True)
    with open("./model_cache/training_data/training_triplets.jsonl", "a", encoding="utf-8") as f:
        for item in data:
            f.write(json.dumps(item.dict()) + "\n")
    return {"status": "success", "message": "Telemetry saved"}

class SyntheticDataRequest(BaseModel):
    limit: int = 50

@router.get("/admin/training-metrics")
async def get_training_metrics():
    """Admin endpoint to fetch the latest model evaluation metrics."""
    metrics_path = os.path.join(os.path.dirname(__file__), "model_cache", "latest_metrics.json")
    if os.path.exists(metrics_path):
        try:
            with open(metrics_path, "r") as f:
                return json.load(f)
        except Exception as e:
            return {"error": str(e)}
    return {"message": "No training metrics available yet."}

@router.post("/admin/trigger-synthetic-generation")
async def trigger_synthetic_generation(req: SyntheticDataRequest):
    """Admin endpoint to queue background generation of synthetic training data via Ollama."""
    try:
        from celery_worker import generate_synthetic_data_task
        task = generate_synthetic_data_task.delay(limit=req.limit)
        return {"status": "queued", "task_id": task.id, "message": f"Generating {req.limit} synthetic records in the background."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/admin/trigger-finetuning")
async def trigger_model_finetuning():
    """Admin endpoint to queue background model fine-tuning."""
    try:
        from celery_worker import trigger_model_finetuning_task
        task = trigger_model_finetuning_task.delay()
        return {"status": "queued", "task_id": task.id, "message": "Model fine-tuning started in the background."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/admin/run-inspect")
async def run_inspect_report(q: str = None):
    """Diagnostic endpoint to read the Excel report and output a detailed markdown analysis."""
    try:
        import pandas as pd
        import os
        
        path = "/home/rajasethupathy/AI_Vector_Bom_Sourcing/docs/RS_Consolidated_Sourcing_Report (9) copy.xlsx"
        if not os.path.exists(path):
            path = "docs/RS_Consolidated_Sourcing_Report (9) copy.xlsx"
        if not os.path.exists(path):
            path = "uploads/RS_Consolidated_Sourcing_Report (9) copy.xlsx"
        if not os.path.exists(path):
            path = "/app/uploads/RS_Consolidated_Sourcing_Report (9) copy.xlsx"
            
        if not os.path.exists(path):
            return {"status": "error", "message": f"File {path} not found in docs/ or uploads/."}
            
        df = pd.read_excel(path)
        
        # Calculate summary statistics
        has_conf = 'Confidence' in df.columns
        avg_conf = 0.0
        high_conf = 0
        med_conf = 0
        low_conf = 0
        if has_conf:
            df['Confidence_Val'] = df['Confidence'].astype(str).str.rstrip('%').astype(float)
            high_conf = len(df[df['Confidence_Val'] >= 80])
            med_conf = len(df[(df['Confidence_Val'] >= 60) & (df['Confidence_Val'] < 80)])
            low_conf = len(df[df['Confidence_Val'] < 60])
            avg_conf = df['Confidence_Val'].mean()
            
        # Write to sourcing_review.md inside mapped uploads so it is visible on the host
        review_path = "uploads/sourcing_review.md"
        
        md_content = f"""# Sourcing Report Review & Verification
 
## 📊 Summary Statistics for `{os.path.basename(path)}`
- **Total Sourced Rows**: {len(df)}
- **Average Confidence**: {avg_conf:.1f}%
- **High Confidence (>=80%)**: {high_conf} rows ({high_conf/len(df)*100:.1f}%)
- **Medium Confidence (60-79%)**: {med_conf} rows ({med_conf/len(df)*100:.1f}%)
- **Low Confidence (<60%)**: {low_conf} rows ({low_conf/len(df)*100:.1f}%)
 
---
 
## 📋 Top 15 Aligned BOM Items Review
"""
        cols_to_print = [c for c in ["S.No", "Qty", "Stock Number", "Catalog MPN", "Brand", "Detailed Description", "Confidence", "AI Status"] if c in df.columns]
        df_print = df[cols_to_print].head(15)
        
        md_content += "| " + " | ".join(cols_to_print) + " |\n"
        md_content += "| " + " | ".join(["---"] * len(cols_to_print)) + " |\n"
        for i, r in df_print.iterrows():
            row_vals = []
            for col in cols_to_print:
                val = str(r[col]).strip()
                if col == "Detailed Description" and len(val) > 50:
                    val = val[:47] + "..."
                row_vals.append(val.replace("|", "\\|"))
            md_content += "| " + " | ".join(row_vals) + " |\n"
            
        with open(review_path, "w", encoding="utf-8") as f:
            f.write(md_content)
            
        # Find diagnostic rows matching "10076" or custom search query `q`
        matching_rows = []
        q_term = q.strip().lower() if q else None
        for idx, r in df.iterrows():
            r_str = " ".join([str(v) for v in r.values]).lower()
            if q_term:
                if q_term in r_str:
                    matching_rows.append(r.to_dict())
            else:
                if "10076" in r_str or "xmp" in r_str:
                    matching_rows.append(r.to_dict())
            
        return {
            "status": "success",
            "message": f"Successfully generated review at {review_path}",
            "stats": {
                "total_rows": len(df),
                "avg_confidence": f"{avg_conf:.1f}%",
                "high_confidence_count": high_conf,
                "medium_confidence_count": med_conf,
                "low_confidence_count": low_conf
            },
            "markdown_preview": md_content,
            "matching_rows": matching_rows
        }
    except Exception as e:
        import traceback
        return {"status": "error", "error": str(e), "trace": traceback.format_exc()}


@router.get("/admin/inspect-mro")
async def inspect_mro_excel():
    """Diagnostic endpoint to inspect sheets and row counts of MRO_Data Template_G.xlsx."""
    try:
        import io
        import os
        import pandas as pd
        from utils import identify_columns
        
        path = "/home/rajasethupathy/AI_Vector_Bom_Sourcing/docs/MRO_Data Template_G.xlsx"
        if not os.path.exists(path):
            path = "docs/MRO_Data Template_G.xlsx"
        if not os.path.exists(path):
            path = "uploads/MRO_Data Template_G.xlsx"
        if not os.path.exists(path):
            path = "/app/uploads/MRO_Data Template_G.xlsx"
            
        if not os.path.exists(path):
            return {
                "status": "error", 
                "message": "File 'MRO_Data Template_G.xlsx' not found inside container.",
                "hint": "Please copy 'MRO_Data Template_G.xlsx' into your local 'uploads/' folder so that the container can read it at `/app/uploads/MRO_Data Template_G.xlsx`."
            }
            
        # Read the Excel file
        xls = pd.ExcelFile(path)
        sheet_names = xls.sheet_names
        
        results = []
        total_rows_all_sheets = 0
        
        for name in sheet_names:
            df = pd.read_excel(path, sheet_name=name, header=None, dtype=str)
            raw_row_count = len(df)
            total_rows_all_sheets += raw_row_count
            
            # Simulate header detection
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
                    
            new_header = df.iloc[best_row_idx].fillna("").astype(str).tolist()
            unique_headers = []
            for i, col in enumerate(new_header):
                col_name = col.strip() if col.strip() and col.strip() not in ("nan", "None") else f"Unnamed_{i}"
                original_col_name = col_name
                counter = 1
                while col_name in unique_headers:
                    col_name = f"{original_col_name}_{counter}"
                    counter += 1
                    if counter > 50: break # Safety exit
                unique_headers.append(col_name)
                
            df_cleaned = df.iloc[best_row_idx + 1:].copy()
            df_cleaned.columns = unique_headers
            df_cleaned = df_cleaned.dropna(how='all').reset_index(drop=True)
            df_cleaned = df_cleaned.fillna("")
            
            mapping = identify_columns(df_cleaned)
            
            results.append({
                "sheet_name": name,
                "raw_rows": raw_row_count,
                "best_header_row_index": int(best_row_idx),
                "headers_found": unique_headers,
                "mapped_columns": {k: int(v) for k, v in mapping.items() if k != "unrelated_indices"},
                "active_data_rows": len(df_cleaned),
                "is_sheet_valid": bool(mapping["mpn"] >= 0 or mapping["description"] >= 0 or mapping["manufacturer"] >= 0)
            })
            
        return {
            "status": "success",
            "file_path": path,
            "total_sheets": len(sheet_names),
            "sheet_names": sheet_names,
            "total_raw_rows_across_all_sheets": total_rows_all_sheets,
            "sheet_details": results
        }
    except Exception as e:
        import traceback
        return {"status": "error", "error": str(e), "trace": traceback.format_exc()}


def populate_taxonomy_from_db_sync():
    import sqlite3
    import os
    
    conn = sqlite3.connect("sourcebuddy.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS categories_taxonomy (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            division TEXT, 
            category TEXT, 
            sub_category TEXT, 
            product_group TEXT
        )
    """)
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_taxonomy_unique ON categories_taxonomy(division, category, sub_category, product_group)")
    conn.commit()
    
    # Try scrolling Qdrant first (extremely fast)
    from qdrant_manager import qdrant_db
    try:
        offset = None
        unique_taxonomies = set()
        for _ in range(50):  # Scroll up to 25,000 points
            res, next_offset = qdrant_db.client.scroll(
                collection_name="products",
                limit=500,
                with_payload=True,
                with_vectors=False,
                offset=offset
            )
            for record in res:
                pay = record.payload or {}
                raw_data = pay.get("raw_data", "")
                if raw_data:
                    parts = raw_data.split('|')
                    if len(parts) > 8:
                        div = parts[2].strip()
                        cat = parts[4].strip()
                        sub = parts[6].strip()
                        grp = parts[8].strip()
                        if div and div != '-' and div.lower() != 'nan':
                            unique_taxonomies.add((div, cat, sub, grp))
            if not next_offset:
                break
            offset = next_offset
            
        if unique_taxonomies:
            for div, cat, sub, grp in unique_taxonomies:
                cursor.execute("""
                    INSERT OR IGNORE INTO categories_taxonomy (division, category, sub_category, product_group)
                    VALUES (?, ?, ?, ?)
                """, (div, cat, sub, grp))
            conn.commit()
    except Exception as e:
        logger.error(f"Failed Qdrant scroll in sync populate: {e}")
        
    # Check count
    cursor.execute("SELECT COUNT(*) FROM categories_taxonomy")
    count = cursor.fetchone()[0]
    
    # If Qdrant was empty or failed, try parsing IN_products.txt (first 50,000 lines for fast startup)
    if count < 5:
        file_path = "uploads/IN_products.txt"
        if not os.path.exists(file_path):
            file_path = "/app/uploads/IN_products.txt"
        if os.path.exists(file_path):
            try:
                unique_taxonomies = set()
                encodings = ['utf-16le', 'utf-8', 'utf-16', 'iso-8859-1', 'cp1252']
                for enc in encodings:
                    try:
                        with open(file_path, 'r', encoding=enc) as f:
                            header = f.readline()
                            sep = '^' if '^' in header else ('|' if '|' in header else '\t')
                            line_cnt = 0
                            for line in f:
                                parts = line.strip().split(sep)
                                if len(parts) > 8:
                                    div = parts[2].replace('"', '').strip()
                                    cat = parts[4].replace('"', '').strip()
                                    sub = parts[6].replace('"', '').strip()
                                    grp = parts[8].replace('"', '').strip()
                                    if div and div != '-' and div.lower() != 'nan':
                                        unique_taxonomies.add((div, cat, sub, grp))
                                line_cnt += 1
                                if line_cnt > 50000:  # Cap at 50k lines for startup speed
                                    break
                        break
                    except Exception as e:
                        continue
                if unique_taxonomies:
                    for div, cat, sub, grp in unique_taxonomies:
                        cursor.execute("""
                            INSERT OR IGNORE INTO categories_taxonomy (division, category, sub_category, product_group)
                            VALUES (?, ?, ?, ?)
                        """, (div, cat, sub, grp))
                    conn.commit()
            except Exception as e:
                logger.error(f"Failed file read in sync populate: {e}")
                
    conn.close()


@router.get("/taxonomy-tree")
async def get_taxonomy_tree_endpoint():
    """Retrieve the dynamic taxonomy tree from SQLite categories_taxonomy table."""
    import sqlite3
    try:
        conn = sqlite3.connect("sourcebuddy.db")
        cursor = conn.cursor()
        
        # Verify table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='categories_taxonomy'")
        if not cursor.fetchone():
            populate_taxonomy_from_db_sync()
            
        cursor.execute("SELECT division, category, sub_category, product_group FROM categories_taxonomy")
        rows = cursor.fetchall()
        conn.close()
        
        # Build hierarchical tree
        tree = {}
        for div, cat, sub, grp in rows:
            div = str(div).strip()
            cat = str(cat).strip()
            sub = str(sub).strip()
            grp = str(grp).strip()
            
            if not div or div == '-' or div.lower() == 'nan':
                continue
                
            if div not in tree:
                tree[div] = {}
                
            if not cat or cat == '-' or cat.lower() == 'nan':
                continue
                
            if cat not in tree[div]:
                tree[div][cat] = {}
                
            if not sub or sub == '-' or sub.lower() == 'nan':
                continue
                
            if sub not in tree[div][cat]:
                tree[div][cat][sub] = []
                
            if grp and grp != '-' and grp.lower() != 'nan':
                if grp not in tree[div][cat][sub]:
                    tree[div][cat][sub].append(grp)
                    
        return {"status": "success", "tree": tree}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def rebuild_sqlite_taxonomy():
    import sqlite3
    try:
        conn = sqlite3.connect("sourcebuddy.db")
        cursor = conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS categories_taxonomy")
        conn.commit()
        conn.close()
        populate_taxonomy_from_db_sync()
        logger.info("✅ Rebuilt categories_taxonomy table upon indexing completion.")
    except Exception as e:
        logger.error(f"Failed to drop/rebuild categories_taxonomy: {e}")


async def run_rebuild_taxonomy_task():
    from fastapi.concurrency import run_in_threadpool
    await run_in_threadpool(rebuild_sqlite_taxonomy)


@router.get("/temp-test")
async def temp_test():
    import sys
    try:
        import pyxlsb
        pyxlsb_installed = True
    except ImportError:
        pyxlsb_installed = False
    
    import os
    files = []
    if os.path.exists("uploads/Rs_stock"):
        files = os.listdir("uploads/Rs_stock")
        
    return {
        "python_version": sys.version,
        "pyxlsb_installed": pyxlsb_installed,
        "files_in_stock": files
    }




