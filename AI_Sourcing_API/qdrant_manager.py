"""
Production-grade Qdrant Manager for RS Components Intelligence Engine.
Connects to Qdrant Server for multi-container stability.
"""
import os
import sys
import json
import logging
import shutil
import types
import re
from typing import List, Dict, Any, Optional, Union
from pathlib import Path
import numpy as np
import threading
from qdrant_client import QdrantClient
from qdrant_client.http import models as rest
from qdrant_client.models import Distance, VectorParams, PointStruct

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("QdrantManager")
import traceback

class QdrantManager:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(QdrantManager, cls).__new__(cls)
        return cls._instance

    def __init__(self, collection_name: str = "products"):
        if hasattr(self, 'initialized'): return
        
        self.collection_name = collection_name
        
        # Connect to Qdrant Server (instead of local path)
        qdrant_host = os.getenv("QDRANT_HOST", "sourcebuddy-qdrant")
        qdrant_port = int(os.getenv("QDRANT_PORT", 6333))
        
        logger.info(f"🚀 Connecting to Qdrant Server at {qdrant_host}:{qdrant_port}...")
        try:
            self.client = QdrantClient(host=qdrant_host, port=qdrant_port, timeout=60)
            logger.info("✅ Qdrant Server Connection Successful")
            
            # Auto-create collection if missing
            if not self.client.collection_exists(self.collection_name):
                logger.info(f"✨ Creating collection: {self.collection_name}")
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(size=384, distance=Distance.COSINE)
                )
        except Exception as e:
            logger.error(f"❌ Qdrant Connection Failed: {e}")
            # Fallback to local only if server is absolutely unreachable
            self.client = QdrantClient(path="./qdrant_db")
        
        self.model = None
        self.reranker = None
        self.loading = True
        
        # We now have 4GB WSL RAM headroom! Let's pre-load the AI model in a background
        # thread on startup so the first search query is lightning-fast and doesn't block.
        # CRITICAL: We do NOT pre-load the model at startup if running inside a Celery worker.
        # This completely avoids the famous PyTorch/CUDA multi-process fork deadlock.
        is_celery = any("celery" in arg.lower() for arg in sys.argv) or "celery" in sys.modules
        if not is_celery:
            threading.Thread(target=self._initialize_model, daemon=True).start()
        else:
            logger.info("🤖 Celery worker environment detected. Bypassing startup background pre-load to prevent CUDA fork conflict.")
        
        self.initialized = True

    def restore_zeros_from_query(self, payload: dict, query_str: str):
        if not payload or not query_str: return
        pay_mpn = payload.get('mpn')
        if pay_mpn:
            p_mpn_str = str(pay_mpn).strip()
            if not p_mpn_str: return
            
            # Simple direct check first
            q_clean = str(query_str).strip()
            if q_clean.lstrip('0') == p_mpn_str.lstrip('0') and len(q_clean) > len(p_mpn_str):
                payload['mpn'] = q_clean
                return
                
            # Regex check to find padded version in description
            import re
            pattern = re.compile(r'\b0*' + re.escape(p_mpn_str) + r'\b')
            matches = pattern.findall(query_str)
            if matches:
                best_match = max(matches, key=len)
                if len(best_match) > len(p_mpn_str):
                    payload['mpn'] = best_match

    def _strip_key_from_all_configs(self, root_path: str, key_to_remove: str):
        protected = ["model_type", "sentence_transformer", "transformers_version"]
        if key_to_remove in protected: return False
            
        modified_any = False
        for root, dirs, files in os.walk(root_path):
            for file in files:
                if file.endswith(".json"):
                    path = os.path.join(root, file)
                    try:
                        with open(path, 'r') as f:
                            config = json.load(f)
                        if isinstance(config, dict) and key_to_remove in config:
                            logger.info(f"🔧 Surgical Healing: Removing incompatible '{key_to_remove}' from {path}")
                            del config[key_to_remove]
                            with open(path, 'w') as f:
                                json.dump(config, f, indent=2)
                            modified_any = True
                    except: continue
        return modified_any

    def _initialize_model(self):
        # Absolute path based on the application directory
        base_dir = Path(__file__).parent
        local_path = base_dir / "model_cache" / "sourcebuddy-electronics-bert"
        
        logger.info(f"💾 Checking local AI model at: {local_path.absolute()}")
        
        if not local_path.exists(): 
            logger.error(f"❌ CRITICAL: Model path does not exist: {local_path.absolute()}")
            # Fallback for docker volume mapping
            alt_path = base_dir.parent / "model_cache" / "sourcebuddy-electronics-bert"
            logger.info(f"🔍 Checking alternative path: {alt_path.absolute()}")
            if alt_path.exists():
                local_path = alt_path
                logger.info("✅ Found model at alternative path!")
            else:
                return
        
        local_path_str = str(local_path.absolute())
        from sentence_transformers import SentenceTransformer
        
        attempts = 0
        while attempts < 15:
            attempts += 1
            try:
                logger.info(f"📡 Loading AI engine from {local_path_str} (Attempt {attempts})...")
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
                logger.info(f"🖥️ Using device: {device.upper()}")
                self.model = SentenceTransformer(local_path_str, device=device, local_files_only=True)
                logger.info("✅ AI Engine Loaded Successfully!")
                self.loading = False
                break
            except TypeError as e:
                error_msg = str(e)
                match = re.search(r"unexpected keyword argument '([^']+)'", error_msg)
                if match:
                    bad_key = match.group(1)
                    if not self._strip_key_from_all_configs(local_path, bad_key): break
                    continue 
                else: break
            except Exception as e:
                logger.error(f"❌ AI Load Error: {e}")
                logger.error(traceback.format_exc())
                break

    def search(self, query: str, limit: int = 10, use_rerank: bool = False, **kwargs):
        # 1. Try Exact Match First (Zero AI needed, Zero delay)
        exact = self.exact_mpn_search(query)
        if exact: return exact
        
        # 2. Only if no exact match, load AI and do semantic search
        if not self.model:
            if self.loading:
                logger.info("⏳ AI Model is still loading in background. Please wait for semantic results...")
                return []
            logger.info("🤖 AI Model not loaded for search. Starting load...")
            self._initialize_model()
            
        if not self.model: return []
        
        try:
            query_vector = self.model.encode(query).tolist()
            hits = self.client.search(collection_name=self.collection_name, query_vector=query_vector, limit=limit)
            for hit in hits:
                self.restore_zeros_from_query(hit.payload, query)
            return [{
                "id": hit.id,
                "score": hit.score,
                "payload": hit.payload
            } for hit in hits]
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []

    def search_batch(self, queries: List[str], limit: int = 5):
        """Perform high-speed batch search for multiple BOM rows."""
        if not self.model:
            logger.info("🤖 AI Model not loaded for batch search. Attempting lazy initialization...")
            self._initialize_model()
            
        if not self.model or not queries: return [[] for _ in queries]
        try:
            # Safely slice into sub-batches of 128 to prevent Out Of Memory (OOM) errors and HTTP timeouts for large uploads (e.g. 2500 records)
            import torch
            sub_batch_size = 128
            results = []
            
            for index in range(0, len(queries), sub_batch_size):
                chunk = queries[index:index + sub_batch_size]
                orig_threads = torch.get_num_threads()
                if orig_threads > 2:
                    torch.set_num_threads(2)
                    
                # 1. Batch encode the chunk
                vectors = self.model.encode(chunk)
                
                if orig_threads > 2:
                    torch.set_num_threads(orig_threads)
                
                # 2. Prepare search requests
                requests = [
                    rest.SearchRequest(
                        vector=v.tolist(),
                        limit=limit,
                        with_payload=True
                    ) for v in vectors
                ]
                
                # 3. Execute batch search
                batch_res = self.client.search_batch(
                    collection_name=self.collection_name,
                    requests=requests
                )
                
                # 4. Convert results
                for r_idx, r_list in enumerate(batch_res):
                    q_text = chunk[r_idx]
                    for hit in r_list:
                        self.restore_zeros_from_query(hit.payload, q_text)
                    results.append([{
                        "id": hit.id,
                        "score": hit.score,
                        "payload": hit.payload
                    } for hit in r_list])
                    
            return results
        except Exception as e:
            logger.error(f"Batch search failed: {e}")
            return [[] for _ in queries]

    def exact_mpn_search(self, query: str):
        """Ultra-fast exact MPN matching with numeric zero-padding and case-insensitivity support."""
        if not self.client: return []
        try:
            # Clean query for exact comparison
            raw_q = str(query).strip()
            queries_to_process = [raw_q]
            
            # Remove common manufacturer/vendor prefix additions (e.g., HAR.19 20 003 1440 -> 19 20 003 1440, or 595-TL431 -> TL431)
            prefix_match = re.match(r'^([A-Za-z]{2,8}|[0-9]{3,5})[\.\-_\s/]+([A-Za-z0-9\-\.\s_]+)$', raw_q)
            if prefix_match:
                stripped_prefix_q = prefix_match.group(2).strip()
                if len(stripped_prefix_q) >= 4:
                    queries_to_process.append(stripped_prefix_q)
            
            match_values = []
            for base_q in queries_to_process:
                upper_q = base_q.upper()
                lower_q = base_q.lower()
                
                # Query Qdrant with raw, uppercase, and lowercase values concurrently to be case-insensitive
                for q in (base_q, upper_q, lower_q):
                    if q not in match_values:
                        match_values.append(q)
                
                # Add a normalized version stripping spaces, hyphens, dots, slashes, and colons
                norm_q = re.sub(r'[\s\-_/\.:]', '', upper_q)
                if norm_q and norm_q not in match_values:
                    match_values.append(norm_q)
                    match_values.append(norm_q.lower())
                    
                # Strip or prepend leading zeros on the raw punctuation-preserved string
                if base_q.startswith('0'):
                    stripped_raw = base_q.lstrip('0')
                    if stripped_raw and stripped_raw not in match_values:
                        match_values.append(stripped_raw)
                        match_values.append(stripped_raw.lower())
                        match_values.append(stripped_raw.upper())
                else:
                    prepended_raw = "0" + base_q
                    if prepended_raw not in match_values:
                        match_values.append(prepended_raw)
                        match_values.append(prepended_raw.lower())
                        match_values.append(prepended_raw.upper())
                
                # Generate split-hyphenated / split-dotted variants for alphanumeric query inputs
                # to match punctuation-separated database entries (like 90779-3003 or 12.3456)
                if upper_q.isalnum() and 5 <= len(upper_q) <= 15:
                    for i in range(1, len(upper_q)):
                        hyphenated = upper_q[:i] + '-' + upper_q[i:]
                        if hyphenated not in match_values:
                            match_values.append(hyphenated)
                            match_values.append(hyphenated.lower())
                        dotted = upper_q[:i] + '.' + upper_q[i:]
                        if dotted not in match_values:
                            match_values.append(dotted)
                            match_values.append(dotted.lower())

                # If the query is long, generate its prefixes to match base part numbers in the database (e.g. ISO6521FREUR -> ISO6521)
                if len(upper_q) >= 6:
                    for length in range(5, len(upper_q)):
                        prefix = upper_q[:length]
                        if prefix not in match_values:
                            match_values.append(prefix)
                            match_values.append(prefix.lower())

            # If any variant is numeric, try matching zero-stripped, zero-prepended, and zero-padded formats
            numeric_variants = [val for val in list(match_values) if val.isdigit()]
            for num_val in numeric_variants:
                # Add version stripped of leading zeros
                stripped_q = num_val.lstrip('0')
                if stripped_q and stripped_q not in match_values:
                    match_values.append(stripped_q)
                
                # Add single leading zero (handles Excel stripping the first '0')
                prepended_q = "0" + num_val
                if prepended_q not in match_values:
                    match_values.append(prepended_q)
                
                # Also try zfill up to 8, 10, or 11 digits if relevant
                if len(num_val) < 8:
                    padded_8 = num_val.zfill(8)
                    if padded_8 not in match_values:
                        match_values.append(padded_8)
                if len(num_val) < 11:
                    padded_11 = num_val.zfill(11)
                    if padded_11 not in match_values:
                        match_values.append(padded_11)
                
            points = []
            # Query Qdrant once using MatchAny for extremely high-speed scroll lookup
            try:
                res = self.client.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=rest.Filter(
                        should=[
                            rest.FieldCondition(key="mpn", match=rest.MatchAny(any=match_values)),
                            rest.FieldCondition(key="product_id", match=rest.MatchAny(any=match_values))
                        ]
                    ),
                    limit=10,
                    with_payload=True
                )
                if res and res[0]:
                    points = res[0]
            except Exception as e:
                logger.warning(f"MatchAny scroll failed in exact_mpn_search: {e}")
            
            # Fallback: if no exact matches, try MatchText tokenized matching for query length >= 5
            # We restrict this to queries with at most 1 space (<= 2 words) to prevent running it on long descriptions.
            has_true_exact = False
            if points:
                q_clean = re.sub(r'[\s\-_/\.:]', '', raw_q.upper())
                for p in points:
                    p_mpn = re.sub(r'[\s\-_/\.:]', '', str(p.payload.get("mpn", "")).upper())
                    if p_mpn == q_clean:
                        has_true_exact = True
                        break

            if not has_true_exact:
                fallback_vals = [v for v in match_values[:3] if len(v) >= 5 and len(v.split()) <= 2]
                seen_ids = {p.id for p in points}
                for val in fallback_vals:
                    try:
                        res = self.client.scroll(
                            collection_name=self.collection_name,
                            scroll_filter=rest.Filter(
                                should=[
                                    rest.FieldCondition(key="mpn", match=rest.MatchText(text=val)),
                                    rest.FieldCondition(key="product_id", match=rest.MatchText(text=val))
                                ]
                            ),
                            limit=5,
                            with_payload=True
                        )
                        if res and res[0]:
                            for p in res[0]:
                                if p.id not in seen_ids:
                                    points.append(p)
                                    seen_ids.add(p.id)
                            break
                    except Exception:
                        pass
            
            for p in points:
                self.restore_zeros_from_query(p.payload, raw_q)
            return [{
                "id": p.id,
                "score": 1.0, # Exact match is always 100%
                "payload": p.payload,
                "is_exact": True
            } for p in points]
        except Exception as e:
            logger.error(f"Exact search failed: {e}")
            return []

    def exact_id_search(self, product_id: str):
        """Ultra-fast exact product_id (RS Stock Number/Article Number) matching with case-insensitivity support."""
        if not self.client or not product_id: return []
        try:
            raw_id = str(product_id).strip()
            
            # Remove any trailing '.0' from Excel numeric parsing
            if raw_id.endswith('.0'):
                raw_id = raw_id[:-2]
                
            ids_to_process = [raw_id]
            # Remove common manufacturer/vendor prefix additions (e.g., HAR.19 20 003 1440 -> 19 20 003 1440, or 595-TL431 -> TL431)
            prefix_match = re.match(r'^([A-Za-z]{2,8}|[0-9]{3,5})[\.\-_\s/]+([A-Za-z0-9\-\.\s_]+)$', raw_id)
            if prefix_match:
                stripped_prefix_id = prefix_match.group(2).strip()
                if len(stripped_prefix_id) >= 4:
                    ids_to_process.append(stripped_prefix_id)
            
            match_values = []
            for base_id in ids_to_process:
                upper_id = base_id.upper()
                lower_id = base_id.lower()
                
                for q in (base_id, upper_id, lower_id):
                    if q not in match_values:
                        match_values.append(q)
                        
                # Strip or prepend leading zeros on the raw punctuation-preserved string
                if base_id.startswith('0'):
                    stripped_raw = base_id.lstrip('0')
                    if stripped_raw and stripped_raw not in match_values:
                        match_values.append(stripped_raw)
                        match_values.append(stripped_raw.lower())
                        match_values.append(stripped_raw.upper())
                else:
                    prepended_raw = "0" + base_id
                    if prepended_raw not in match_values:
                        match_values.append(prepended_raw)
                        match_values.append(prepended_raw.lower())
                        match_values.append(prepended_raw.upper())

                # Generate split-hyphenated / split-dotted variants for alphanumeric query inputs
                # to match punctuation-separated database entries (like 90779-3003 or 12.3456)
                if upper_id.isalnum() and 5 <= len(upper_id) <= 15:
                    for i in range(1, len(upper_id)):
                        hyphenated = upper_id[:i] + '-' + upper_id[i:]
                        if hyphenated not in match_values:
                            match_values.append(hyphenated)
                            match_values.append(hyphenated.lower())
                        dotted = upper_id[:i] + '.' + upper_id[i:]
                        if dotted not in match_values:
                            match_values.append(dotted)
                            match_values.append(dotted.lower())

            if upper_id.isdigit():
                stripped_id = upper_id.lstrip('0')
                if stripped_id and stripped_id not in match_values:
                    match_values.append(stripped_id)
                prepended_id = "0" + upper_id
                if prepended_id not in match_values:
                    match_values.append(prepended_id)
                if len(upper_id) < 8:
                    padded_8 = upper_id.zfill(8)
                    if padded_8 not in match_values:
                        match_values.append(padded_8)
                if len(upper_id) < 7:
                    padded_7 = upper_id.zfill(7)
                    if padded_7 not in match_values:
                        match_values.append(padded_7)
            
            points = []
            # Query Qdrant once using MatchAny for extremely high-speed scroll lookup
            try:
                res = self.client.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=rest.Filter(
                        should=[
                            rest.FieldCondition(key="product_id", match=rest.MatchAny(any=match_values)),
                            rest.FieldCondition(key="mpn", match=rest.MatchAny(any=match_values))
                        ]
                    ),
                    limit=10,
                    with_payload=True
                )
                if res and res[0]:
                    points = res[0]
            except Exception as e:
                logger.warning(f"MatchAny scroll failed in exact_id_search: {e}")
            
            for p in points:
                self.restore_zeros_from_query(p.payload, raw_id)
            return [{
                "id": p.id,
                "score": 1.0, # Exact match is always 100%
                "payload": p.payload,
                "is_exact": True
            } for p in points]
        except Exception as e:
            logger.error(f"Exact product_id search failed: {e}")
            return []

    def exact_mpn_search_batch(self, queries: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        """Perform high-speed batch exact MPN matching to completely prevent connection-pool starvation and CPU thrashing."""
        if not self.client or not queries: return {}
        try:
            # For each query, generate its match values and map them back to the original query
            query_to_variants = {}
            all_variants = set()
            
            for q_raw in queries:
                raw_q = str(q_raw).strip()
                if not raw_q: continue
                
                # Split raw query on slashes, commas, pipes, or 'or' to handle multiple MPNs in one row
                parts = re.split(r'\s+or\s+|[\s,/|]*[/,|][\s,/|]*', raw_q, flags=re.IGNORECASE)
                queries_to_process = [p.strip() for p in parts if p.strip()]
                
                # Add stripped-prefix versions for each part
                extended_queries = list(queries_to_process)
                for part in queries_to_process:
                    prefix_match = re.match(r'^([A-Za-z]{2,8}|[0-9]{3,5})[\.\-_\s/]+([A-Za-z0-9\-\.\s_]+)$', part)
                    if prefix_match:
                        stripped_prefix_q = prefix_match.group(2).strip()
                        if len(stripped_prefix_q) >= 4 and stripped_prefix_q not in extended_queries:
                            extended_queries.append(stripped_prefix_q)
                
                match_values = []
                for base_q in extended_queries:
                    upper_q = base_q.upper()
                    lower_q = base_q.lower()
                    
                    for q in (base_q, upper_q, lower_q):
                        if q not in match_values:
                            match_values.append(q)
                    
                    # Add normalized version stripping spaces, hyphens, dots, slashes, and colons
                    norm_q = re.sub(r'[\s\-_/\.:]', '', upper_q)
                    if norm_q and norm_q not in match_values:
                        match_values.append(norm_q)
                        match_values.append(norm_q.lower())
                        
                    # Strip or prepend leading zeros on the raw punctuation-preserved string
                    if base_q.startswith('0'):
                        stripped_raw = base_q.lstrip('0')
                        if stripped_raw and stripped_raw not in match_values:
                            match_values.append(stripped_raw)
                            match_values.append(stripped_raw.lower())
                            match_values.append(stripped_raw.upper())
                    else:
                        prepended_raw = "0" + base_q
                        if prepended_raw not in match_values:
                            match_values.append(prepended_raw)
                            match_values.append(prepended_raw.lower())
                            match_values.append(prepended_raw.upper())
                    
                    # Generate split-hyphenated / split-dotted variants for alphanumeric query inputs
                    # to match punctuation-separated database entries (like 90779-3003 or 12.3456)
                    if upper_q.isalnum() and 5 <= len(upper_q) <= 15:
                        for i in range(1, len(upper_q)):
                            hyphenated = upper_q[:i] + '-' + upper_q[i:]
                            if hyphenated not in match_values:
                                match_values.append(hyphenated)
                                match_values.append(hyphenated.lower())
                            dotted = upper_q[:i] + '.' + upper_q[i:]
                            if dotted not in match_values:
                                match_values.append(dotted)
                                match_values.append(dotted.lower())

                    # If the query is long and not purely numeric, generate its prefixes to match base part numbers in the database (e.g. ISO6521FREUR -> ISO6521)
                    if len(upper_q) >= 6 and not upper_q.isdigit():
                        for length in range(5, len(upper_q)):
                            prefix = upper_q[:length]
                            if prefix not in match_values:
                                match_values.append(prefix)
                                match_values.append(prefix.lower())

                # If any variant is numeric, try matching zero-stripped, zero-prepended, and zero-padded formats
                numeric_variants = [val for val in list(match_values) if val.isdigit()]
                for num_val in numeric_variants:
                    stripped_q = num_val.lstrip('0')
                    if stripped_q and stripped_q not in match_values:
                        match_values.append(stripped_q)
                    prepended_q = "0" + num_val
                    if prepended_q not in match_values:
                        match_values.append(prepended_q)
                    if len(num_val) < 8:
                        padded_8 = num_val.zfill(8)
                        if padded_8 not in match_values:
                            match_values.append(padded_8)
                    if len(num_val) < 11:
                        padded_11 = num_val.zfill(11)
                        if padded_11 not in match_values:
                            match_values.append(padded_11)
                
                query_to_variants[q_raw] = match_values
                for v in match_values:
                    all_variants.add(v)
            
            # Query all variants in chunks of 100 using MatchAny
            all_variants_list = list(all_variants)
            chunk_size = 100
            variant_to_points = {}
            
            for i in range(0, len(all_variants_list), chunk_size):
                chunk = all_variants_list[i:i+chunk_size]
                res = self.client.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=rest.Filter(
                        should=[
                            rest.FieldCondition(
                                key="mpn",
                                match=rest.MatchAny(any=chunk)
                            ),
                            rest.FieldCondition(
                                key="product_id",
                                match=rest.MatchAny(any=chunk)
                            )
                        ]
                    ),
                    limit=1000,
                    with_payload=True
                )
                if res and res[0]:
                    for p in res[0]:
                        p_mpn = p.payload.get("mpn")
                        if p_mpn:
                            p_mpn_str = str(p_mpn).strip()
                            for k in (p_mpn_str, p_mpn_str.upper(), p_mpn_str.lower()):
                                if k not in variant_to_points:
                                    variant_to_points[k] = []
                                if p not in variant_to_points[k]:
                                    variant_to_points[k].append(p)
                                    
                        p_pid = p.payload.get("product_id")
                        if p_pid:
                            p_pid_str = str(p_pid).strip()
                            for k in (p_pid_str, p_pid_str.upper(), p_pid_str.lower()):
                                if k not in variant_to_points:
                                    variant_to_points[k] = []
                                if p not in variant_to_points[k]:
                                    variant_to_points[k].append(p)
                                
            # Map back to the original queries
            results_map = {}
            for q_raw in queries:
                variants = query_to_variants.get(q_raw, [])
                points = []
                seen_ids = set()
                for v in variants:
                    for p in variant_to_points.get(v, []):
                        if p.id not in seen_ids:
                            seen_ids.add(p.id)
                            points.append(p)
                
                for p in points:
                    self.restore_zeros_from_query(p.payload, q_raw)
                results_map[q_raw] = [{
                    "id": p.id,
                    "score": 1.0,
                    "payload": p.payload,
                    "is_exact": True
                } for p in points]
                
            # Fallback for queries with no exact matches that are >= 5 chars
            # Restrict to queries with at most 1 space (<= 2 words) to avoid querying descriptions.
            for q_raw in queries:
                has_true_exact = False
                existing_hits = results_map.get(q_raw, [])
                if existing_hits:
                    q_clean = re.sub(r'[\s\-_/\.:]', '', str(q_raw).upper())
                    for hit in existing_hits:
                        hit_mpn = re.sub(r'[\s\-_/\.:]', '', str(hit.get("payload", {}).get("mpn", "")).upper())
                        if hit_mpn == q_clean:
                            has_true_exact = True
                            break

                if not has_true_exact and len(q_raw) >= 5 and len(q_raw.split()) <= 2:
                    try:
                        res = self.client.scroll(
                            collection_name=self.collection_name,
                            scroll_filter=rest.Filter(
                                must=[
                                    rest.FieldCondition(
                                        key="mpn",
                                        match=rest.MatchText(text=q_raw)
                                    )
                                ]
                            ),
                            limit=5,
                            with_payload=True
                        )
                        if res and res[0]:
                            for p in res[0]:
                                self.restore_zeros_from_query(p.payload, q_raw)
                            
                            new_hits = [{
                                "id": p.id,
                                "score": 1.0,
                                "payload": p.payload,
                                "is_exact": True
                            } for p in res[0]]
                            
                            seen_ids = {h["id"] for h in existing_hits}
                            for nh in new_hits:
                                if nh["id"] not in seen_ids:
                                    existing_hits.append(nh)
                            results_map[q_raw] = existing_hits
                    except Exception as ex:
                        logger.warning(f"Batch MatchText fallback failed for query {q_raw}: {ex}")
                        
            return results_map
        except Exception as e:
            logger.error(f"Batch exact MPN search failed: {e}")
            logger.error(traceback.format_exc())
            return {}

    def exact_id_search_batch(self, product_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        """Perform high-speed batch exact product_id matching to completely prevent connection-pool starvation."""
        if not self.client or not product_ids: return {}
        try:
            query_to_variants = {}
            all_variants = set()
            
            for pid_raw in product_ids:
                raw_id = str(pid_raw).strip()
                if not raw_id: continue
                if raw_id.endswith('.0'):
                    raw_id = raw_id[:-2]
                
                ids_to_process = [raw_id]
                # Remove common manufacturer/vendor prefix additions (e.g., HAR.19 20 003 1440 -> 19 20 003 1440, or 595-TL431 -> TL431)
                prefix_match = re.match(r'^([A-Za-z]{2,8}|[0-9]{3,5})[\.\-_\s/]+([A-Za-z0-9\-\.\s_]+)$', raw_id)
                if prefix_match:
                    stripped_prefix_id = prefix_match.group(2).strip()
                    if len(stripped_prefix_id) >= 4:
                        ids_to_process.append(stripped_prefix_id)
                
                match_values = []
                for base_id in ids_to_process:
                    upper_id = base_id.upper()
                    lower_id = base_id.lower()
                    
                    for q in (base_id, upper_id, lower_id):
                        if q not in match_values:
                            match_values.append(q)
                    
                    # Strip or prepend leading zeros on the raw punctuation-preserved string
                    if base_id.startswith('0'):
                        stripped_raw = base_id.lstrip('0')
                        if stripped_raw and stripped_raw not in match_values:
                            match_values.append(stripped_raw)
                            match_values.append(stripped_raw.lower())
                            match_values.append(stripped_raw.upper())
                    else:
                        prepended_raw = "0" + base_id
                        if prepended_raw not in match_values:
                            match_values.append(prepended_raw)
                            match_values.append(prepended_raw.lower())
                            match_values.append(prepended_raw.upper())

                    # Generate split-hyphenated / split-dotted variants for alphanumeric query inputs
                    # to match punctuation-separated database entries (like 90779-3003 or 12.3456)
                    if upper_id.isalnum() and 5 <= len(upper_id) <= 15:
                        for i in range(1, len(upper_id)):
                            hyphenated = upper_id[:i] + '-' + upper_id[i:]
                            if hyphenated not in match_values:
                                match_values.append(hyphenated)
                                match_values.append(hyphenated.lower())
                            dotted = upper_id[:i] + '.' + upper_id[i:]
                            if dotted not in match_values:
                                match_values.append(dotted)
                                match_values.append(dotted.lower())

                if upper_id.isdigit():
                    stripped_id = upper_id.lstrip('0')
                    if stripped_id and stripped_id not in match_values:
                        match_values.append(stripped_id)
                    prepended_id = "0" + upper_id
                    if prepended_id not in match_values:
                        match_values.append(prepended_id)
                    if len(upper_id) < 8:
                        padded_8 = upper_id.zfill(8)
                        if padded_8 not in match_values:
                            match_values.append(padded_8)
                    if len(upper_id) < 7:
                        padded_7 = upper_id.zfill(7)
                        if padded_7 not in match_values:
                            match_values.append(padded_7)
                
                query_to_variants[pid_raw] = match_values
                for v in match_values:
                    all_variants.add(v)
            
            all_variants_list = list(all_variants)
            chunk_size = 100
            variant_to_points = {}
            
            for i in range(0, len(all_variants_list), chunk_size):
                chunk = all_variants_list[i:i+chunk_size]
                res = self.client.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=rest.Filter(
                        should=[
                            rest.FieldCondition(
                                key="product_id",
                                match=rest.MatchAny(any=chunk)
                            ),
                            rest.FieldCondition(
                                key="mpn",
                                match=rest.MatchAny(any=chunk)
                            )
                        ]
                    ),
                    limit=1000,
                    with_payload=True
                )
                if res and res[0]:
                    for p in res[0]:
                        p_id = p.payload.get("product_id")
                        if p_id:
                            p_id_str = str(p_id).strip()
                            for k in (p_id_str, p_id_str.upper(), p_id_str.lower()):
                                if k not in variant_to_points:
                                    variant_to_points[k] = []
                                if p not in variant_to_points[k]:
                                    variant_to_points[k].append(p)
                                    
                        p_mpn = p.payload.get("mpn")
                        if p_mpn:
                            p_mpn_str = str(p_mpn).strip()
                            for k in (p_mpn_str, p_mpn_str.upper(), p_mpn_str.lower()):
                                if k not in variant_to_points:
                                    variant_to_points[k] = []
                                if p not in variant_to_points[k]:
                                    variant_to_points[k].append(p)
                                
            results_map = {}
            for pid_raw in product_ids:
                variants = query_to_variants.get(pid_raw, [])
                points = []
                seen_ids = set()
                for v in variants:
                    for p in variant_to_points.get(v, []):
                        if p.id not in seen_ids:
                            seen_ids.add(p.id)
                            points.append(p)
                            
                for p in points:
                    self.restore_zeros_from_query(p.payload, pid_raw)
                results_map[pid_raw] = [{
                    "id": p.id,
                    "score": 1.0,
                    "payload": p.payload,
                    "is_exact": True
                } for p in points]
                
            return results_map
        except Exception as e:
            logger.error(f"Batch exact ID search failed: {e}")
            logger.error(traceback.format_exc())
            return {}

    def get_embeddings(self, texts: List[str]):
        """Generate embeddings for a list of texts."""
        if not self.model:
            logger.info("🤖 AI Model not loaded. Attempting lazy initialization...")
            self._initialize_model()
            
        if not self.model:
            logger.error("❌ AI Model failed to initialize during lazy loading.")
            return []
            
        try:
            # Check if texts is a single string and wrap it
            if isinstance(texts, str):
                texts = [texts]
            return self.model.encode(texts).tolist()
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            return []

    def description_keyword_search(self, desc: str, limit: int = 15):
        """Perform full-text MatchText scroll on description field to fallback for unaligned MPN/manufacturer query descriptions."""
        if not self.client or not desc: return []
        try:
            # Clean description text
            cleaned_desc = str(desc).strip()
            if len(cleaned_desc) < 3: return []
            
            res = self.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=rest.Filter(
                    must=[
                        rest.FieldCondition(
                            key="description",
                            match=rest.MatchText(text=cleaned_desc)
                        )
                    ]
                ),
                limit=limit,
                with_payload=True
            )
            
            points = res[0] if res and res[0] else []
            for p in points:
                self.restore_zeros_from_query(p.payload, desc)
            return [{
                "id": p.id,
                "score": 0.70, # Treat description keyword fallback as 70% match base confidence
                "payload": p.payload
            } for p in points]
        except Exception as e:
            logger.error(f"Description keyword search failed: {e}")
            return []

    def upsert_batch(self, points: List[Dict]):
        """Upsert a batch of points into Qdrant."""
        if not self.client: return
        try:
            q_points = [
                PointStruct(
                    id=p["id"],
                    vector=p["vector"],
                    payload=p["payload"]
                ) for p in points
            ]
            self.client.upsert(
                collection_name=self.collection_name,
                points=q_points,
                wait=False
            )
        except Exception as e:
            logger.error(f"Upsert failed: {e}")

    def recreate_collection(self):
        """Wipe and recreate the collection."""
        try:
            if self.client.collection_exists(self.collection_name):
                self.client.delete_collection(self.collection_name)
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=384, distance=Distance.COSINE)
            )
            logger.info(f"🔥 Collection {self.collection_name} recreated.")
        except Exception as e:
            logger.error(f"Failed to recreate collection: {e}")

# Single global instance
qdrant_db = QdrantManager()
