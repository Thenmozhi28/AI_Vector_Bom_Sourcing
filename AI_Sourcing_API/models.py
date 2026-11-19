from pydantic import BaseModel
from typing import List, Dict, Optional, Tuple
from datetime import datetime  
import numpy as np
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity 
# ─────────────────────────────────────────────────────────────────────────────
# GLOBAL STATE & SETTINGS
# ─────────────────────────────────────────────────────────────────────────────
class AppSettings(BaseModel):
    top_k: int = 5
    threshold: float = 0.20
    device: str = "cpu"
    model_name: str = "sourcebuddy-electronics-bert"
    
    model_config = {
        "protected_namespaces": ()
    }

class AppState:
    vdb_chunks: Optional[List[Dict]] = None
    vdb_vectors: Optional[np.ndarray] = None
    vdb_built: bool = False
    results: Optional[List[Dict]] = None
    report_bytes: Optional[bytes] = None
    csv_bytes: Optional[bytes] = None
    vdb_lock: bool = False  # Global lock for ingestion
    active_task_id: Optional[str] = None  # Current running task
    settings: AppSettings = AppSettings()
    search_count: int = 0
    search_history: List[Dict] = []
    bom_uploads: List[Dict] = []

state = AppState()

# ─────────────────────────────────────────────────────────────────────────────
# PYDANTIC MODELS
# ─────────────────────────────────────────────────────────────────────────────
class BuildIndexRequest(BaseModel):
    use_sample: bool = True
    use_mongo: bool = False
    mongo_uri: Optional[str] = None
    mongo_db: Optional[str] = None
    mongo_collections: Optional[str] = None

class SearchRequest(BaseModel):
    top_k: int = 5

class HealthResponse(BaseModel):
    status: str
    vdb_built: bool
    chunks_count: int

class SettingsResponse(BaseModel):
    top_k: int
    threshold: float
    device: str
    model_name: str
    
    model_config = {
        "protected_namespaces": ()
    }

class StatsResponse(BaseModel):
    total_chunks: int
    total_searches: int
    total_uploads: int
    last_search: Optional[str]
    vdb_status: str
    
class ValidationResponse(BaseModel):
    valid: bool
    total_parts: int
    message: str

class SearchResult(BaseModel):
    filename: str
    valid: bool
    total_parts: int
    message: str