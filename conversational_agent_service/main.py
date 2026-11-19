import os
import asyncio
import logging
import csv
import time
from typing import List, Dict, Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
from dotenv import load_dotenv

# Load env variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ConversationalAgent")

app = FastAPI(
    title="SourceBuddy Conversational Agent Service",
    description="Dedicated microservice for natural language component sourcing and RAG reasoning",
    version="1.0.0"
)

# CORS
allowed_origins_env = os.getenv("ALLOWED_ORIGINS", "http://localhost")
allowed_origins = [origin.strip() for origin in allowed_origins_env.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create a static directory for reports
os.makedirs("reports", exist_ok=True)
app.mount("/reports", StaticFiles(directory="reports"), name="reports")

# Models
class ChatRequest(BaseModel):
    message: str
    history: List[Dict[str, str]] = []
    bom_context: Optional[List[Dict]] = None

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "conversational_agent"}

def is_blocklisted(mpn: str, desc: str) -> bool:
    m = str(mpn).upper()
    d = str(desc).upper()
    blocklist_terms = ["PTFIX", "3274056", "3274058", "3274054"]
    for term in blocklist_terms:
        if term in m or term in d:
            return True
    return False

async def finalize_sourcing_suggestions_with_llm(
    valid_items: List[Dict],
    llm_provider: str,
    llm_base_url: str,
    llm_model: str,
    llm_api_key: str
) -> List[Dict]:
    """
    Invokes the local Ollama LLM (or Groq/OpenAI if configured) to select and finalize
    the absolute best matching component for each BOM item from its retrieved VectorDB candidates.
    Chunks the input to ensure it fits within the context window limits and avoids output limits.
    Returns a list of finalized suggestion dictionaries matching the target UI schema.
    """
    import json
    if not valid_items:
        return []

    # Helper function to get fallback suggestions for a specific chunk of items
    def get_fallback_suggestions_for_chunk(chunk_items):
        suggestions = []
        for item in chunk_items:
            if item["candidates"]:
                c = item["candidates"][0]
                suggestions.append({
                    "qty": item["qty"],
                    "stock_number": c["stock_number"],
                    "division": c["division"],
                    "category": c["category"],
                    "sub_category": c["sub_category"],
                    "product_group": c["product_group"],
                    "catalog_mpn": c["catalog_mpn"],
                    "brand": c["brand"],
                    "description": c["description"],
                    "confidence_score": c["confidence_score"],
                    "ai_status": "VectorDB Auto Match (LLM Fallback)",
                    "original_specification": item["original_specification"]
                })
        return suggestions

    # Chunk the items into groups of 15 to prevent context window limitations and generation cuts
    chunk_size = 15
    item_chunks = [valid_items[i:i + chunk_size] for i in range(0, len(valid_items), chunk_size)]
    finalized_results = []

    # System instruction template
    system_prompt = (
        "You are an Elite Component Sourcing and Procurement Engine.\n"
        "Your task is to analyze each original BOM item and its top retrieved VectorDB candidates, and select the single best, most compatible alternative part for each item.\n\n"
        "For each BOM item, follow these strict procurement logic guidelines:\n"
        "1. Identify the candidate that best matches the required specifications (MPN, brand, electrical/physical specifications) in the original description.\n"
        "2. If a candidate is an exact match (e.g. same manufacturer and part number or very high confidence), select it and set its `ai_status` to 'Exact Match Approved'.\n"
        "3. If a candidate is a different brand but technically compatible (e.g. form-factor, pinout, voltage/current specifications align), select it and set its `ai_status` to 'Highly Compatible Alternative'.\n"
        "4. If no candidate is compatible, or the match is highly questionable, select the closest candidate but set its `ai_status` to 'Pending Engineering Review' and lower the confidence score accordingly.\n"
        "5. Keep the `original_specification` and `qty` fields exactly as they are in the input BOM item.\n\n"
        "OUTPUT REQUIREMENT:\n"
        "You MUST return a valid JSON object with a single root key 'suggestions' containing the list of finalized part selections. Do NOT include any markdown formatting, explanatory text, or code block ticks (like ```json). Just output the raw JSON string.\n"
        "The schema of the returned JSON MUST be:\n"
        "{\n"
        "  \"suggestions\": [\n"
        "    {\n"
        "      \"qty\": <original quantity (integer)>,\n"
        "      \"stock_number\": \"<finalized selected part stock number/RS code>\",\n"
        "      \"division\": \"<finalized selected part division>\",\n"
        "      \"category\": \"<finalized selected part category>\",\n"
        "      \"sub_category\": \"<finalized selected part sub_category>\",\n"
        "      \"product_group\": \"<finalized selected part product_group>\",\n"
        "      \"catalog_mpn\": \"<finalized selected part manufacturer part number>\",\n"
        "      \"brand\": \"<finalized selected part manufacturer/brand name>\",\n"
        "      \"description\": \"<finalized selected part description>\",\n"
        "      \"confidence_score\": \"<LLM compatibility score as percentage string, e.g. 95%>\",\n"
        "      \"ai_status\": \"<LLM selection status, e.g. 'Exact Match Approved', 'Highly Compatible Alternative', 'Pending Engineering Review'>\",\n"
        "      \"original_specification\": \"<exact input original_specification string>\"\n"
        "    }\n"
        "  ]\n"
        "}"
    )

    logger.info(f"🧠 Dividing LLM finalization into {len(item_chunks)} chunks (size {chunk_size} per chunk).")

    # Limit concurrent Ollama requests to 2 to protect CPU/GPU from thread locks or out of memory crashes
    ollama_sem = asyncio.Semaphore(2)

    async def process_single_chunk(chunk_idx, chunk_items):
        logger.info(f"🤖 Processing LLM finalization chunk {chunk_idx + 1}/{len(item_chunks)}...")
        # Build chunk input representation
        formatted_input = []
        for item in chunk_items:
            formatted_input.append({
                "original_specification": item["original_specification"],
                "qty": item["qty"],
                "candidates": [
                    {
                        "brand": c["brand"],
                        "catalog_mpn": c["catalog_mpn"],
                        "stock_number": c["stock_number"],
                        "description": c["description"],
                        "division": c["division"],
                        "category": c["category"],
                        "sub_category": c["sub_category"],
                        "product_group": c["product_group"],
                        "db_confidence": c["confidence_score"]
                    }
                    for c in item["candidates"]
                ]
            })
            
        user_content = f"Input BOM Items and their Database Candidates:\n{json.dumps(formatted_input, indent=2)}"
        chunk_suggestions = None
        
        async with ollama_sem:
            try:
                raw_response = ""
                if llm_provider == "groq" and llm_api_key:
                    from groq import Groq
                    client = Groq(api_key=llm_api_key)
                    completion = client.chat.completions.create(
                        model=llm_model if llm_model else "llama3-8b-8192",
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content}
                        ],
                        temperature=0.1,
                        response_format={"type": "json_object"}
                    )
                    raw_response = completion.choices[0].message.content
                        
                elif llm_provider == "ollama" or llm_provider == "vllm":
                    url = f"{llm_base_url}/api/chat" if "ollama" in llm_provider else f"{llm_base_url}/v1/chat/completions"
                    async with httpx.AsyncClient() as client:
                        if "ollama" in llm_provider:
                            payload = {
                                "model": llm_model,
                                "messages": [
                                    {"role": "system", "content": system_prompt},
                                    {"role": "user", "content": user_content}
                                ],
                                "stream": False,
                                "format": "json",
                                "options": {"temperature": 0.1}
                            }
                            response = await client.post(url, json=payload, timeout=60.0)
                            if response.status_code == 200:
                                res_json = response.json()
                                raw_response = res_json.get("message", {}).get("content", "")
                        else:
                            payload = {
                                "model": llm_model,
                                "messages": [
                                    {"role": "system", "content": system_prompt},
                                    {"role": "user", "content": user_content}
                                ],
                                "temperature": 0.1,
                                "response_format": {"type": "json_object"}
                            }
                            headers = {"Authorization": f"Bearer {llm_api_key}"} if llm_api_key else {}
                            response = await client.post(url, json=payload, headers=headers, timeout=60.0)
                            if response.status_code == 200:
                                res_json = response.json()
                                raw_response = res_json.get("choices", [{}])[0].get("message", {}).get("content", "")

                # Log raw response for diagnostics
                if raw_response:
                    logger.info(f"LLM Raw Response for chunk {chunk_idx + 1}: {raw_response[:200]}...")
                    parsed = json.loads(raw_response)
                    
                    # Dynamic flexible parsing to handle different LLM JSON formatting conventions
                    if isinstance(parsed, list):
                        chunk_suggestions = parsed
                    elif isinstance(parsed, dict):
                        if "suggestions" in parsed:
                            chunk_suggestions = parsed["suggestions"]
                        elif "stock_number" in parsed or "catalog_mpn" in parsed:
                            # Single suggestion dictionary directly returned by the LLM (common when chunk size is 1)
                            chunk_suggestions = [parsed]
                        else:
                            # Try to find any list value inside the dict (e.g. {"finalized": [...]})
                            list_val = next((v for v in parsed.values() if isinstance(v, list)), None)
                            if list_val:
                                chunk_suggestions = list_val
                            # Check if it is a dictionary of dictionaries where keys are index strings (e.g. {"0": {...}, "1": {...}})
                            elif any(isinstance(v, dict) and ("stock_number" in v or "catalog_mpn" in v) for v in parsed.values()):
                                chunk_suggestions = list(parsed.values())
            except Exception as e:
                logger.error(f"❌ LLM finalization chunk {chunk_idx + 1} failed: {e}. Using chunk fallback.")
                
            if chunk_suggestions and isinstance(chunk_suggestions, list) and len(chunk_suggestions) > 0:
                logger.info(f"✅ LLM successfully processed chunk {chunk_idx + 1} ({len(chunk_suggestions)} suggestions).")
                return chunk_suggestions
            else:
                logger.warning(f"⚠️ Falling back to top VectorDB auto-matches for chunk {chunk_idx + 1} due to parsing/LLM failure.")
                return get_fallback_suggestions_for_chunk(chunk_items)

    # Run all chunks concurrently in parallel
    tasks = [process_single_chunk(idx, chunk) for idx, chunk in enumerate(item_chunks)]
    chunks_results = await asyncio.gather(*tasks)
    for res in chunks_results:
        finalized_results.extend(res)

    return finalized_results


@app.post("/chat")
async def chat_with_agent(req: ChatRequest):
    """
    Stand-alone RAG Conversational Sourcing Agent.
    Queries the main SourceBuddy app for vector search context, compiles standard prompts,
    and calls Ollama, Groq, or OpenAI to return premium component suggestions.
    """
    user_query = req.message
    history = req.history
    
    # Intercept Snoop my BOM prompts
    if "snoop my bom" in str(user_query).lower():
        return {
            "status": "success",
            "reply": "The 'Snoop my BOM' prompt option has been deprecated and is no longer available on this platform. Please upload your BOM via the sourcing dashboard to process and search standard catalog parts.",
            "context": [],
            "suggestions": [],
            "report_url": None
        }

    # Clean history turns containing Snoop my BOM
    cleaned_history = []
    for turn in history:
        content = turn.get("content", "")
        if "snoop my bom" not in str(content).lower():
            cleaned_history.append(turn)
    history = cleaned_history
    
    # 1. Fetch catalog matches from the main SourceBuddy API (High efficiency, zero redundant VRAM load)
    main_app_url = os.getenv("MAIN_APP_URL", "http://localhost:8005").rstrip('/')
    logger.info(f"🔍 Fetching context from main app: {main_app_url}/search")
    
    context_items = []
    direct_db_suggestions = []
    try:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient() as client:
                    res = await client.get(f"{main_app_url}/search", params={"query": user_query, "limit": 5}, timeout=10.0)
                    if res.status_code == 200:
                        data = res.json()
                        # Handle both plain list responses and dict-wrapped responses
                        if isinstance(data, list):
                            results = data
                        else:
                            results = data.get("results", []) or data.get("summary", []) or []
                        for idx, r in enumerate(results[:5]):
                            # Parse Qdrant payload
                            payload = r.get("payload", r)
                            # If it's a nested catalog match
                            matches = r.get("matches", [])
                            top_match = matches[0] if matches else payload
                            
                            mfr = top_match.get("manufacturer") or top_match.get("brand") or "NEUTRAL"
                            mpn = top_match.get("mpn") or top_match.get("catalog_mpn") or "N/A"
                            rs_code = top_match.get("product_id") or top_match.get("rs_code") or "N/A"
                            desc = top_match.get("description") or top_match.get("technical_description") or "No description available"
                            
                            if is_blocklisted(mpn, desc):
                                continue
                            
                            item_str = f"[{idx+1}] Brand: {mfr} | MPN: {mpn} | RS Code: {rs_code} | Description: {desc}"
                            context_items.append(item_str)
                            
                            div = top_match.get("division") or top_match.get("category1") or "Electronic Components"
                            cat = top_match.get("category") or top_match.get("category2") or "Passive Parts"
                            sub_cat = top_match.get("sub_category") or top_match.get("category3") or "Resistors/Capacitors"
                            prod_group = top_match.get("product_group") or top_match.get("category4") or "SMD"
                            conf = int(float(r.get("confidence", 85) or 85))
                            
                            direct_db_suggestions.append({
                                "qty": 1,
                                "stock_number": rs_code,
                                "division": div,
                                "category": cat,
                                "sub_category": sub_cat,
                                "product_group": prod_group,
                                "catalog_mpn": mpn,
                                "brand": mfr,
                                "description": desc,
                                "confidence_score": f"{conf}%",
                                "ai_status": "VectorDB Auto Match",
                                "original_specification": user_query
                            })
                        break  # Success, exit retry loop
                    else:
                        logger.error(f"Main app search returned status: {res.status_code}")
            except Exception as e:
                logger.error(f"Failed to fetch search context from main app (Attempt {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
    except Exception as e:
        logger.error(f"Failed to execute search context loop: {e}")
        
    context_str = "\n".join(context_items) if context_items else "No matching products found in the catalog database."

    # 1.1 BOM Sourcing context expansion (Search BOM items in parallel VectorDB calls)
    bom_context_str = ""
    valid_items = []
    if req.bom_context:
        logger.info(f"📋 Processing BOM context of size {len(req.bom_context)}")
        # Process all BOM items (no limits)
        target_items = [
            item for item in req.bom_context 
            if float(str(item.get("confidence_score", "0")).replace('%', '')) < 100
        ]
        if not target_items:
            target_items = req.bom_context
            
        queries_to_send = []
        queries_metadata = []
        for item in target_items:
            desc = item.get("description") or item.get("specification") or ""
            qty = item.get("qty") or 1
            if desc and desc != 'N/A' and desc != '-':
                queries_to_send.append(desc)
                queries_metadata.append({"original_specification": desc, "qty": qty})

        if queries_to_send:
            try:
                # Query in a single POST request to /search-batch to avoid connection pool starvation and thread contention
                async with httpx.AsyncClient() as client:
                    payload = {"queries": queries_to_send, "limit": 3}
                    res = await client.post(f"{main_app_url}/search-batch", json=payload, timeout=300.0)
                    if res.status_code == 200:
                        data = res.json()
                        batch_results = data.get("results", [])
                        
                        # Process and structure the candidates
                        for idx, results in enumerate(batch_results):
                            desc_meta = queries_metadata[idx]
                            desc = desc_meta["original_specification"]
                            qty = desc_meta["qty"]
                            
                            candidates = []
                            for r_idx, r in enumerate(results[:3]):
                                payload_item = r.get("payload", r)
                                matches = r.get("matches", [])
                                top_match = matches[0] if matches else payload_item
                                
                                mfr = top_match.get("manufacturer") or top_match.get("brand") or "NEUTRAL"
                                mpn = top_match.get("mpn") or top_match.get("catalog_mpn") or "N/A"
                                rs_code = top_match.get("product_id") or top_match.get("rs_code") or "N/A"
                                desc_match = top_match.get("description") or top_match.get("technical_description") or "No description"
                                
                                if is_blocklisted(mpn, desc_match):
                                    continue
                                
                                div = top_match.get("division") or top_match.get("category1") or "Electronic Components"
                                cat = top_match.get("category") or top_match.get("category2") or "Passive Parts"
                                sub_cat = top_match.get("sub_category") or top_match.get("category3") or "Resistors/Capacitors"
                                prod_group = top_match.get("product_group") or top_match.get("category4") or "SMD"
                                
                                score_val = r.get("score")
                                if score_val is not None:
                                    if float(score_val) <= 1.0:
                                        conf = int(float(score_val) * 100)
                                    else:
                                        conf = int(float(score_val))
                                else:
                                    conf = int(float(r.get("confidence", 85)))
                                
                                candidates.append({
                                    "qty": qty,
                                    "stock_number": rs_code,
                                    "division": div,
                                    "category": cat,
                                    "sub_category": sub_cat,
                                    "product_group": prod_group,
                                    "catalog_mpn": mpn,
                                    "brand": mfr,
                                    "description": desc_match,
                                    "confidence_score": f"{conf}%",
                                    "ai_status": "VectorDB Auto Match",
                                    "original_specification": desc
                                })
                            
                            if candidates:
                                valid_items.append({
                                    "original_specification": desc,
                                    "qty": qty,
                                    "candidates": candidates
                                })
            except Exception as e:
                import traceback
                logger.error(f"Error executing batch RAG context search: {e}\n{traceback.format_exc()}")
        
        # Compile standard BOM context string for conversational reply
        context_lines = []
        for idx, item in enumerate(valid_items):
            desc = item["original_specification"]
            qty = item["qty"]
            match_strs = []
            for c in item["candidates"]:
                match_strs.append(f"Mfr: {c['brand']} | MPN: {c['catalog_mpn']} | RS Stock Number: {c['stock_number']} | Description: {c['description']}")
            if match_strs:
                context_lines.append(f"BOM Item #{idx+1} Description: '{desc}' (Qty: {qty})\nTop Verified Matches in VectorDB:\n" + "\n".join([f"  - {m}" for m in match_strs]))
        if context_lines:
            bom_context_str = "\n\n--- ACTIVE BOM ITEMS & VECTORDB ALTERNATIVES ---\n" + "\n\n".join(context_lines)
    
    # 2. Build system instruction system prompt
    system_prompt = (
        "You are SourceBuddy Copilot, a World-Class Elite Procurement Expert and AI Sourcing Agent.\n"
        "Your objective is to provide the user with an unparalleled, highly professional, and deeply technical sourcing experience.\n\n"
        "CRITICAL RULES - YOU MUST OBEY THESE OR YOU WILL BE PENALIZED:\n"
        "1. STRICT CONTEXT ONLY: You are ONLY allowed to recommend parts from the `--- VERIFIED DATABASE SEARCH CONTEXT ---` or `--- ACTIVE BOM ITEMS & VECTORDB ALTERNATIVES ---` sections below. \n"
        "2. ZERO HALLUCINATION: DO NOT EVER invent, guess, or use your own training data to provide MPNs, Manufacturers, or specifications. If the exact requested part is not in the context, you must explicitly say: 'This part is not available in our current catalog' and then ONLY list the closest alternatives IF they exist in the context below.\n"
        "3. PRODUCTION-GRADE FORMATTING: Your response must be visually impressive. Use markdown headings (###), bold key terms (MPNs, Manufacturers), and clear sections.\n"
        "4. COMPARISON TABLES: When presenting parts from the context, always use a clean Markdown table (e.g., | Manufacturer | MPN | Description |).\n"
        "5. PROCUREMENT INSIGHTS: Always include a brief 'Procurement Insights' or 'Technical Notes' section at the end, highlighting potential constraints, form-factor checks, or supply chain advice.\n"
        "6. TONE: Confident, highly precise, authoritative, and helpful.\n"
        "7. FINALIZED TABLE PROVIDED: The system has already generated a highly accurate table of component alternatives directly from the VectorDB and attached it below your message for the user. Do not attempt to list every component manually. Simply provide a brief 2-sentence professional summary acknowledging that the finalized components have been found and are presented in the table below.\n\n"
        f"--- VERIFIED DATABASE SEARCH CONTEXT ---\n{context_str}\n"
        f"{bom_context_str}\n"
    )
    
    # 3. LLM Configuration
    llm_provider = os.getenv("LLM_PROVIDER", "ollama").lower()
    llm_base_url = os.getenv("LLM_BASE_URL", "http://localhost:11434").rstrip('/')
    llm_model = os.getenv("LLM_MODEL", "llama3:8b")
    llm_api_key = os.getenv("LLM_API_KEY", "")
    
    messages = [{"role": "system", "content": system_prompt}]
    # Add trailing conversation history
    for turn in history[-6:]:
        messages.append({"role": turn.get("role"), "content": turn.get("content")})
    messages.append({"role": "user", "content": user_query})
    
    reply = ""
    try:
        if llm_provider == "groq" and llm_api_key:
            # Groq implementation
            from groq import Groq
            client = Groq(api_key=llm_api_key)
            completion = client.chat.completions.create(
                model=llm_model if llm_model else "llama3-8b-8192",
                messages=messages,
                temperature=0.2,
                max_tokens=1024
            )
            reply = completion.choices[0].message.content
        elif llm_provider == "ollama" or llm_provider == "vllm":
            # Ollama/vLLM implementation
            url = f"{llm_base_url}/api/chat" if "ollama" in llm_provider else f"{llm_base_url}/v1/chat/completions"
            async with httpx.AsyncClient() as client:
                if "ollama" in llm_provider:
                    payload = {
                        "model": llm_model,
                        "messages": messages,
                        "stream": False,
                        "options": {"temperature": 0.2}
                    }
                    response = await client.post(url, json=payload, timeout=300.0)
                    res_json = response.json()
                    reply = res_json.get("message", {}).get("content", "")
                else:
                    payload = {
                        "model": llm_model,
                        "messages": messages,
                        "temperature": 0.2
                    }
                    headers = {"Authorization": f"Bearer {llm_api_key}"} if llm_api_key else {}
                    response = await client.post(url, json=payload, headers=headers, timeout=300.0)
                    res_json = response.json()
                    reply = res_json.get("choices", [{}])[0].get("message", {}).get("content", "")
        else:
            # Fallback mock
            best_match = context_items[0] if context_items else "no master data match"
            reply = (
                f"Hello! I am the standalone SourceBuddy Conversational Agent.\n\n"
                f"Based on our active 1.8M master catalog, here is the top matching record:\n"
                f"👉 **{best_match}**\n\n"
                f"Please configure an external LLM server in your `.env` (e.g. `LLM_PROVIDER=ollama`) to unlock natural language responses!"
            )
    except Exception as e:
        logger.error(f"LLM request failed: {e}")
        best_match = context_items[0] if context_items else "no master data match"
        reply = (
            f"I encountered a connection issue with the conversational LLM server, but I have retrieved the exact match from our database context for you:\n\n"
            f"🔍 **Top Database Recommendation:**\n{best_match}\n\n"
            f"*(Technical note: {str(e)})*"
        )
    except Exception as e:
        logger.error(f"LLM request failed: {e}")
        best_match = context_items[0] if context_items else "no master data match"
        reply = (
            f"I encountered a connection issue with the conversational LLM server, but I have retrieved the exact match from our database context for you:\n\n"
            f"🔍 **Top Database Recommendation:**\n{best_match}\n\n"
            f"*(Technical note: {str(e)})*"
        )
        
    # Get finalized suggestions using local Ollama LLM
    unique_suggestions = []
    if req.bom_context and valid_items:
        unique_suggestions = await finalize_sourcing_suggestions_with_llm(
            valid_items=valid_items,
            llm_provider=llm_provider,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
            llm_api_key=llm_api_key
        )
    else:
        seen_mpns = set()
        for s in direct_db_suggestions:
            if s["catalog_mpn"] not in seen_mpns:
                unique_suggestions.append(s)
                seen_mpns.add(s["catalog_mpn"])

    # Filter out blocklisted items from unique suggestions
    unique_suggestions = [
        s for s in unique_suggestions
        if not is_blocklisted(s.get("catalog_mpn") or s.get("stock_number") or "", s.get("description") or "")
    ]

    # Generate Full CSV Report if BOM Context was provided
    report_link = None
    if req.bom_context and unique_suggestions:
        try:
            timestamp = int(time.time())
            filename = f"ai_suggested_bom_{timestamp}.csv"
            filepath = os.path.join("reports", filename)
            
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "qty", "stock_number", "division", "category", "sub_category", 
                    "product_group", "catalog_mpn", "brand", "description", 
                    "confidence_score", "ai_status", "original_specification"
                ], extrasaction='ignore')
                writer.writeheader()
                writer.writerows(unique_suggestions)
                
            host = os.getenv("HOST", "localhost")
            port = os.getenv("PORT", "8010")
            report_link = f"http://{host}:{port}/reports/{filename}"
        except Exception as e:
            logger.error(f"Failed to generate CSV report: {e}")
        
    return {
        "status": "success",
        "reply": reply,
        "context": context_items,
        "suggestions": unique_suggestions,  # Return all unique matches to the UI
        "report_url": report_link
    }



class GenerateRequest(BaseModel):
    model: str
    prompt: str
    stream: bool = False

@app.post("/api/generate")
async def proxy_generate(req: GenerateRequest):
    """Proxy endpoint for backend services to reach Ollama."""
    try:
        llm_base_url = os.getenv("LLM_BASE_URL", "http://localhost:11434").rstrip('/')
        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.post(
                f"{llm_base_url}/api/generate",
                json=req.dict(),
            )
            return res.json()
    except Exception as e:
        logger.error(f"Proxy generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
