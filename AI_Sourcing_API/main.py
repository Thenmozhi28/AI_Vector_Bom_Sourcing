"""
SourceBuddy — RS Components Intelligence Backend
==============================================
Production-grade engine for high-precision procurement search.

Run:
    python main.py

Core Endpoints:
    GET    /                   → Premium Search UI
    POST   /search             → High-Intelligence Sourcing Search
    POST   /build-index        → Rebuild local Vector DB
    GET    /admin/vdb-stats    → Live system metrics
    POST   /auth/login         → Secure Session Management
"""

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
from fastapi import FastAPI, Depends, HTTPException, Request, Form, UploadFile, File, Query
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import logging
import numpy as np

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Main")

# Load environment variables
load_dotenv()

# Initialize database
from database import init_database
init_database()

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from utils import *
from models import *
from qdrant_manager import qdrant_db

# Auto-detect if Vector DB is built based on Qdrant collection
try:
    if qdrant_db.client.collection_exists("products"):
        count = qdrant_db.client.count(collection_name="products").count
        if count > 0:
            from models import state
            state.vdb_built = True
            # Also set a dummy chunk count so routers don't complain
            state.vdb_chunks = [{"id": "dummy"}] * count 
            logger.info(f"✅ Vector DB auto-detected: {count} points found.")
except Exception as e:
    logger.error(f"Failed to auto-detect Vector DB status: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# FASTAPI APP SETUP
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="SourceBuddy API",
    description="Complete BOM Column-Based Search Engine",
    version="2.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import core RS routers
from routers import router as rs_router
from auth_router import router as auth_router
from database import init_database

# Unified Startup
@app.on_event("startup")
async def startup_event():
    """Ensure database and local systems are ready."""
    print("🚀 Initializing RS Components Engine...")
    init_database()

# Include the unified RS routers
app.include_router(auth_router, tags=["Authentication"])
app.include_router(rs_router, tags=["RS Components Sourcing Engine"])

# ─────────────────────────────────────────────────────────────────────────────
# UI ROUTES (Root / Home)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/", response_class=FileResponse)
async def root():
    """Serve the premium standalone AI Search UI (searches for index.html, main.html)."""
    for file_name in ["index.html", "main.html"]:
        path = Path(file_name)
        if path.exists():
            return FileResponse(path, headers={"Cache-Control": "no-store"})
        path_ui = Path("UI") / file_name
        if path_ui.exists():
            return FileResponse(path_ui, headers={"Cache-Control": "no-store"})
    
    # Fallback default path if nothing found
    return FileResponse(Path("UI/index.html"), headers={"Cache-Control": "no-store"})

@app.get("/index.html", response_class=FileResponse)
async def get_index():
    """Serve UI via /index.html endpoint."""
    return await root()

def generate_page_content(page_type: str) -> str:
    """
    Dynamically loads index.html and replaces the main upload card block
    to serve page-specific layouts on the fly without manual file splits.
    """
    for path in [Path("index.html"), Path("UI/index.html")]:
        if path.exists() and path.is_file():
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
                
            # Locate the card boundary dynamically using robust string index lookups
            title_text = 'BOM Direct Matching'
            title_idx = content.find(title_text)
            if title_idx == -1:
                return content
                
            # Find the outer parent div starting tag backwards
            possible_starts = [
                '<div style="display: flex; justify-content: space-between;',
                '<div style="display:flex; justify-content:space-between;',
                '<div style="display: flex; justify-content: space-between'
            ]
            
            parent_start = -1
            for ps in possible_starts:
                parent_start = content.rfind(ps, 0, title_idx)
                if parent_start != -1:
                    break
                    
            if parent_start == -1:
                # Fallback backwards find
                first_div = content.rfind('<div', 0, title_idx)
                if first_div != -1:
                    parent_start = content.rfind('<div', 0, first_div)
                    
            if parent_start == -1:
                return content
                
            input_tag = content.find('id="bomUp"', title_idx)
            if input_tag == -1:
                input_tag = content.find("id='bomUp'", title_idx)
            if input_tag == -1:
                return content
                
            first_div_close = content.find('</div>', input_tag)
            parent_end = content.find('</div>', first_div_close + 1)
            if parent_end == -1:
                return content
            parent_end += 6  # Include '</div>'
            
            if page_type == "ollama":
                ollama_upload_card = """<div style="display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <h3 style="font-size: 20px;">Ollama Clean</h3>
                        <p style="color: var(--text-dim); font-size: 14px;">Upload and clean your spreadsheet data via local Ollama Llama-3.</p>
                    </div>
                    <div style="display: flex; gap: 12px; align-items: center;">
                        <button class="btn btn-llm" onclick="document.getElementById('bomLlmUp').click()"
                            style="background: linear-gradient(135deg, #8b5cf6, #6d28d9); color: white; border: none; font-size: 14px; padding: 10px 20px; border-radius: 6px; font-weight: 600; cursor: pointer; transition: all 0.2s ease; display: flex; align-items: center; gap: 8px;">🧠 Upload & Clean via Ollama</button>
                        <input type="file" id="bomLlmUp" hidden accept=".xlsx,.csv">
                    </div>
                </div>"""
                return content[:parent_start] + ollama_upload_card + content[parent_end:]
                
            elif page_type == "ai":
                ai_upload_card = """<div style="display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <h3 style="font-size: 20px;">AI Sourcing</h3>
                        <p style="color: var(--text-dim); font-size: 14px;">Upload, clean data using Ollama, and fetch correct parts from Vector DB.</p>
                    </div>
                    <div style="display: flex; gap: 12px; align-items: center;">
                        <button class="btn btn-ai" onclick="document.getElementById('bomAiUp').click()"
                            style="background: linear-gradient(135deg, #ec4899, #be185d); color: white; border: none; font-size: 14px; padding: 10px 20px; border-radius: 6px; font-weight: 600; cursor: pointer; transition: all 0.2s ease; display: flex; align-items: center; gap: 8px; box-shadow: 0 4px 12px rgba(236, 72, 153, 0.3);">⚡ AI Sourcing (Ollama + Vector DB)</button>
                        <input type="file" id="bomAiUp" hidden accept=".xlsx,.csv">
                    </div>
                </div>"""
                return content[:parent_start] + ai_upload_card + content[parent_end:]
                
    return ""

@app.get("/ollama-clean.html", response_class=HTMLResponse)
async def get_ollama_clean():
    """Serve the Ollama Clean UI page dynamically in-memory."""
    html_content = generate_page_content("ollama")
    if html_content:
        return HTMLResponse(content=html_content, status_code=200, headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail="ollama-clean.html failed to generate")

@app.get("/ai-sourcing.html", response_class=HTMLResponse)
async def get_ai_sourcing():
    """Serve the AI Sourcing UI page dynamically in-memory."""
    html_content = generate_page_content("ai")
    if html_content:
        return HTMLResponse(content=html_content, status_code=200, headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail="ai-sourcing.html failed to generate")

@app.get("/main.html", response_class=FileResponse)
async def get_main():
    """Serve UI via /main.html endpoint."""
    return await root()

@app.get("/search-ui", response_class=FileResponse)
async def get_search_ui():
    """Alias for root UI."""
    return await root()

@app.get("/health")
async def health_check():
    """System health check for Docker/Load Balancers."""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.post("/chat")
async def proxy_chat(request: Request):
    """Proxy chat requests to the Copilot service."""
    import httpx
    async with httpx.AsyncClient() as client:
        try:
            body = await request.body()
            response = await client.post(
                "http://164.52.192.57:8010/chat",
                content=body,
                headers={"Content-Type": "application/json"},
                timeout=300.0
            )
            return JSONResponse(status_code=response.status_code, content=response.json())
        except Exception as e:
            return JSONResponse(status_code=500, content={"status": "error", "reply": f"Proxy Error: {str(e)}"})


def process_search_candidates(query: str, exact_results: List[Dict], semantic_results: List[Dict], limit: int) -> List[Dict]:
    """Helper function to de-duplicate, score, penalize, and boost candidates for a query."""
    final_results = []
    seen_parts = set()
    
    # Determine if we should be strict (only return exact matches)
    # We are strict if we found exact matches AND the query looks like a specific MPN (alphanumeric, no spaces)
    is_mpn_query = len(query) >= 5 and ' ' not in query and any(c.isdigit() for c in query)
    
    # Strictness for long technical descriptions
    # If the user provides a full specification, they expect high precision
    import re
    has_voltage = bool(re.search(r'\b\d+(?:\.\d+)?\s*v\b|\bvolt\b|\bvolts\b', query.lower()))
    has_tech_kw = any(kw in query.lower() for kw in ['uf', 'pf', 'mlcc', 'smd', 'capacitor'])
    is_tech_query = len(query) > 20 and (has_voltage or has_tech_kw)
    
    if exact_results and (is_mpn_query or (is_tech_query and any(r.get('score', 0) == 1.0 for r in exact_results))):
        # If we found exact matches for a specific query, semantic results are usually noise
        logger.info(f"🎯 Strict match found for '{query}'. Filtering out semantic noise.")
        candidates = exact_results
    elif is_tech_query and semantic_results:
        # For technical queries, keep high-confidence semantic results (> 0.70)
        # This prevents 16V search from showing 50V results while preserving legitimate matches
        candidates = exact_results + [r for r in semantic_results if r.get('score', 0) > 0.70]
    else:
        # Otherwise, use unified search results
        candidates = exact_results + semantic_results
    
    for res in candidates:
        p = res.get('payload', res)
        mpn = str(p.get('mpn', '')).upper()
        mfr = str(p.get('manufacturer', '')).upper()
        desc = str(p.get('description', '')).lower()
        query_lower = query.lower()
        
        # --- HUMAN-MINDSET BOOSTING WITH UNIT NORMALIZATION ---
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

        # --- BRAND & CATEGORY STRICTNESS (NEW) ---
        brand_mismatch = False
        if norm_mfr and norm_query:
            mfg_aliases = {
                "TI": "TEXAS INSTRUMENTS",
                "TEXAS INSTRUMENTS": "TI",
                "ST": "STMICROELECTRONICS",
                "STMICROELECTRONICS": "ST",
                "HAR": "HARTING",
                "HARTING": "HAR",
                "MOLEX": "MOL",
                "MOL": "MOLEX",
                "TE": "TE CONNECTIVITY",
                "TE CONNECTIVITY": "TE",
                "AMP": "TE CONNECTIVITY",
                "STANLEY": "STANLEY ELECTRIC",
                "NXP": "NXP SEMICONDUCTORS"
            }
            
            if mfr and mfr != "NEUTRAL":
                known_brands = ["TEXAS INSTRUMENTS", "TI", "STMICROELECTRONICS", "ST", "HARTING", "HAR", "MOLEX", "TE CONNECTIVITY", "TE", "NEXPERIA", "NXP", "PANASONIC", "MEAN WELL", "ITECH", "FACOM"]
                
                query_mentioned_brands = []
                for brand in known_brands:
                    brand_words = set(brand.split())
                    if brand in norm_query.upper() or (len(brand_words) >= 2 and brand_words.issubset(query_tokens_set)):
                        query_mentioned_brands.append(brand)
                        
                if query_mentioned_brands:
                    mfr_match = False
                    pay_mfr_upper = mfr.upper()
                    pay_mfr_words = set(pay_mfr_upper.split())
                    
                    for q_brand in query_mentioned_brands:
                        q_brand_words = set(q_brand.split())
                        if q_brand == pay_mfr_upper or q_brand in pay_mfr_upper or pay_mfr_upper in q_brand or q_brand_words.intersection(pay_mfr_words):
                            mfr_match = True
                            break
                        for alias, full_name in mfg_aliases.items():
                            if alias == q_brand and full_name in pay_mfr_upper:
                                mfr_match = True
                                break
                            if full_name == q_brand and alias in pay_mfr_upper:
                                mfr_match = True
                                break
                                
                    if not mfr_match:
                        brand_mismatch = True

        if brand_mismatch:
            res['score'] = res.get('score', 0) * 0.6 # Significant penalty for wrong brand
            res['is_exact'] = False
            logger.info(f"⚠️ Brand Mismatch: Query has brand info but {mfr} doesn't match. Penalized.")

        # --- CATEGORY CONFLICT GUARD (EXPERT LOGIC) ---
        # If input mentions an instrument but result is a consumable (or vice versa)
        instruments = ['power source', 'power supply', 'electronic load', 'instrument', 'meter', 'tester', 'source']
        consumables = ['cable', 'wire', 'connector', 'terminal', 'lug', 'shield']
        
        # Check if this is a cross-category collision (e.g. MPN 8540)
        is_query_instrument = any(kw in query_lower for kw in instruments)
        is_result_consumable = any(kw in desc or kw in str(p.get('category', '')).lower() for kw in consumables)
        
        if is_query_instrument and is_result_consumable:
            res['score'] = res.get('score', 0) * 0.4 # Heavy penalty for category mismatch
            res['is_exact'] = False
            logger.info(f"🚫 Category Conflict: Query is 'Instrument' but result is 'Consumable'. Penalized {mpn}.")

        # Create a unique key based on MPN and Manufacturer
        key = f"{mpn}|{mfr}"
        if key not in seen_parts:
            res['is_exact'] = res.get('is_exact', False) or any(mpn == query.upper() for e in exact_results)
            # Ensure score is present for UI
            if 'score' not in res: 
                base_score = res.get('score', 0.8)
                res['score'] = 0.95 if is_high_quality else base_score
            final_results.append(res)
            seen_parts.add(key)
    
    # Re-sort based on the new boosted scores and secondary match rate token overlap
    final_results.sort(key=lambda x: (x.get('score', 0.0), x.get('match_rate', 0.0)), reverse=True)
    
    # Apply strict 50% threshold and single-token constraints to eliminate low-confidence junk matches
    filtered_results = []
    q_clean = query.strip().upper()
    q_words = q_clean.split()
    is_single_token = len(q_words) == 1 and len(q_clean) >= 3
    
    for res in final_results:
        p = res.get('payload', res)
        mpn = str(p.get('mpn', '')).upper()
        mfr = str(p.get('manufacturer', '')).upper()
        desc = str(p.get('description', '')).upper()
        score = res.get('score', 0.0)
        
        if is_single_token:
            is_exact_match = (score >= 0.95)
            contains_token = (q_clean in mpn or q_clean in desc or q_clean in mfr or mpn in q_clean)
            if is_exact_match or (score >= 0.50 and contains_token):
                filtered_results.append(res)
        else:
            if score >= 0.50:
                filtered_results.append(res)
                
    return filtered_results


@app.api_route("/search", methods=["GET", "POST"])
async def semantic_search(request: Request, query: Optional[str] = Query(None), limit: int = Query(10), use_rerank: bool = Query(True)):
    """Perform a high-intelligence search (exact MPN + fallback) against Qdrant."""
    # Check if this is a BOM upload via POST /search (Frontend Legacy Support)
    if request.method == "POST":
        try:
            form = await request.form()
            if "files" in form or "file" in form:
                # Redirect to search_bom logic but return what frontend expects
                from routers import search_bom
                upload_file = form.get("files") or form.get("file")
                logger.info(f"📁 BOM processing via /search endpoint for {upload_file.filename}")
                if not state.vdb_built:
                    return {
                        "status": "error",
                        "message": "Vector DB not ready. Please index the product list first.",
                        "summary": [],
                        "headers": []
                    }
                    
                bom_res = await search_bom(upload_file, top_k=limit)
                
                # Frontend expects "summary" and "headers"
                return {
                    "status": "success",
                    "summary": bom_res.get("results", []),
                    "headers": bom_res.get("headers", []),
                    "total": bom_res.get("total_found", 0)
                }
            
            # Otherwise treat as JSON query
            try:
                body = await request.json()
                query = body.get("query", query)
            except:
                query = form.get("query", query)
        except Exception as e:
            logger.error(f"Form parsing error: {e}")
            
    if not query:
        return {"status": "error", "message": "Query parameter is required"}
    try:
        # Clean query if it's a pasted row (removes SNo, Qty, etc.)
        query = clean_search_query(query)
        
        # 1. Parallel Execution: Run Exact Match and Semantic Search at the same time
        exact_task = run_in_threadpool(qdrant_db.exact_mpn_search, query)
        semantic_task = run_in_threadpool(qdrant_db.search, query, limit=max(350, limit*2), use_rerank=use_rerank)
        
        # Await both simultaneously (Saves ~50% latency)
        exact_results, semantic_results = await asyncio.gather(exact_task, semantic_task)
        
        final_results = process_search_candidates(query, exact_results, semantic_results, limit)
        
        return {
            "status": "success", 
            "results": final_results[:limit],
            "total_found": len(final_results)
        }
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class BatchSearchRequest(BaseModel):
    queries: List[str]
    limit: int = 3
    use_rerank: bool = True


@app.post("/search-batch")
async def batch_search(req: BatchSearchRequest):
    """Perform a high-intelligence batch search (exact MPN + fallback) against Qdrant."""
    if not req.queries:
        return {"status": "success", "results": []}
    try:
        # Pre-clean all queries using clean_search_query
        cleaned_queries = [clean_search_query(q) for q in req.queries]
        
        # 1. Parallel batch lookup for exact matches
        exact_results_map = await run_in_threadpool(qdrant_db.exact_mpn_search_batch, cleaned_queries)
        
        # Identify which queries don't have exact matches
        semantic_queries = []
        for q in cleaned_queries:
            if not exact_results_map.get(q):
                semantic_queries.append(q)
                
        # 2. Batch semantic search only for queries that had NO exact matches
        semantic_results_list = []
        if semantic_queries:
            semantic_results_list = await run_in_threadpool(qdrant_db.search_batch, semantic_queries, limit=max(350, req.limit*2))
            
        semantic_map = dict(zip(semantic_queries, semantic_results_list))
        
        # 3. Post-process and boost/filter each query's candidates
        batch_results = []
        for orig_q, cleaned_q in zip(req.queries, cleaned_queries):
            exact_results = exact_results_map.get(cleaned_q, [])
            semantic_results = semantic_map.get(cleaned_q, [])
            
            final_results = process_search_candidates(cleaned_q, exact_results, semantic_results, req.limit)
            batch_results.append(final_results)
            
        return {
            "status": "success",
            "results": batch_results
        }
    except Exception as e:
        logger.error(f"Batch search error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.api_route("/admin/vdb-clear", methods=["POST", "DELETE"])
async def clear_vdb_fast():
    """Wipe the Vector DB and reset all states immediately."""
    try:
        # 1. Clear Qdrant Collection (Server or Local)
        qdrant_db.recreate_collection()
        
        # 2. Reset Global State
        from models import state
        state.vdb_built = False
        state.results = None
        
        # 3. Clear disk caches
        if os.path.exists("vector_db.pkl"):
            os.remove("vector_db.pkl")
            
        logger.info("🔥 Vector DB wiped by administrative request.")
        return {"status": "success", "message": "Vector DB wiped successfully. You can now start a fresh build."}
    except Exception as e:
        logger.error(f"Clear VDB error: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/admin/vdb-stats")
async def get_vdb_stats():
    """Fetch live record count from Qdrant."""
    try:
        info = qdrant_db.client.get_collection("products")
        return {
            "points_count": info.points_count,
            "indexed_count": info.indexed_vectors_count,
            "status": info.status
        }
    except Exception as e:
        logger.error(f"VDB Stats error: {e}")
        return {"points_count": 0, "error": str(e)}

@app.get("/debug-clean-and-save")
async def debug_clean_and_save():
    import pandas as pd
    import os
    from utils import standardize_dataframe, check_if_sheet_source_in_excel, parse_bom_to_df
    
    file_path = "uploads/Spare Part procurement.xlsx"
    if not os.path.exists(file_path):
        file_path = "/app/uploads/Spare Part procurement.xlsx"
        
    if not os.path.exists(file_path):
        return {"status": "error", "message": f"File not found: {file_path}"}
        
    try:
        # Read the file
        with open(file_path, "rb") as f:
            content = f.read()
            
        df, original_headers = parse_bom_to_df(content, "Spare Part procurement.xlsx")
        
        if df is None or df.empty:
            return {"status": "error", "message": "Failed to parse spreadsheet or spreadsheet is empty"}
            
        original_had_sheet_source = check_if_sheet_source_in_excel(content, "Spare Part procurement.xlsx")
        
        # Standardize using the high-intelligence python logic
        cleaned_df = standardize_dataframe(df, original_had_sheet_source)
        
        # Save to output path
        out_dir = os.path.dirname(file_path)
        out_path = os.path.join(out_dir, "cleaned_flat_bom.xlsx")
        cleaned_df.to_excel(out_path, index=False)
        
        # Also copy it to standard host location if writable
        try:
            cleaned_df.to_excel("/home/rajasethupathy/AI_Vector_Bom_Sourcing/uploads/cleaned_flat_bom.xlsx", index=False)
        except Exception as copy_err:
            pass
            
        # Format preview
        preview = cleaned_df.head(20).to_dict('records')
        
        return {
            "status": "success",
            "message": f"Successfully cleaned and saved {len(cleaned_df)} rows to {out_path}",
            "rows_cleaned": len(cleaned_df),
            "columns": list(cleaned_df.columns),
            "preview": preview
        }
    except Exception as e:
        import traceback
        return {"status": "error", "message": str(e), "traceback": traceback.format_exc()}

@app.get("/{file_path:path}")

async def serve_static(file_path: str):
    file = Path(file_path)
    if file.exists() and file.is_file():
        return FileResponse(file)
    # Check dynamically inside UI folder
    ui_file = Path("UI") / file_path
    if ui_file.exists() and ui_file.is_file():
        return FileResponse(ui_file)
    for base in ["Sourcebuddy_RS", "../Sourcebuddy_RS"]:
        rs_file = Path(base) / file_path
        if rs_file.exists() and rs_file.is_file():
            return FileResponse(rs_file)
    return JSONResponse({"detail": "Not Found"}, status_code=404)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", os.getenv("SOURCEBUDDY_PORT", 8005)))
    is_docker = os.getenv("DOCKER_ENV") == "true" or os.path.exists("/.dockerenv")
    host = "0.0.0.0" if is_docker else "127.0.0.1"
    
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        limit_max_request_size=1_073_741_824
    )
