import os
import sys
import logging
from celery import Celery
from dotenv import load_dotenv

# Ensure the current directory is in the path for module imports
sys.path.append(os.getcwd())

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CeleryWorker")

# Initialize Celery
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "sourcebuddy_tasks",
    broker=REDIS_URL,
    backend=REDIS_URL
)

# Optimized configuration for high-load AI tasks
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    
    # Stability settings for high-load environments
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        'visibility_timeout': 43200, # 12 hours for long indexing jobs
        'max_retries': 5,
        'interval_start': 0,
        'interval_step': 0.5,
        'interval_max': 3,
    },
    result_backend_transport_options={
        'retry_policy': {
            'timeout': 10.0, # Increased from default 1.0s
            'max_retries': 10
        }
    },
    redis_backend_health_check_interval=30
)

# Import tasks here to register them
# We'll import them inside functions or use strings to avoid circular imports if needed
@celery_app.task(name="ingest_products_task", bind=True)
def ingest_products_task(self, file_path: str, batch_size: int = 1000):
    """Celery task to ingest products into Qdrant."""
    from ingest_products import ingest_products
    
    logger.info(f"🚀 Starting background ingestion: {file_path}")
    try:
        # Pass a callback to update progress if needed
        def progress_callback(current, total):
            self.update_state(state='PROGRESS', meta={'current': current, 'total': total})
            
        ingest_products(file_path, batch_size=batch_size, progress_callback=progress_callback)
        return {"status": "complete", "file": file_path}
    except Exception as e:
        logger.error(f"❌ Ingestion task failed: {e}")
        return {"status": "failed", "error": str(e)}



@celery_app.task(name="generate_synthetic_data_task", bind=True)
def generate_synthetic_data_task(self, limit: int = 20):
    """Generates synthetic training data using Ollama."""
    import httpx
    import json
    import os
    from qdrant_manager import qdrant_db
    
    logger.info(f"🧠 Starting synthetic data generation for {limit} records.")
    
    try:
        # 1. Scroll a few records from Qdrant
        records, next_page = qdrant_db.client.scroll(
            collection_name=qdrant_db.collection_name,
            limit=limit,
            with_payload=True
        )
        
        generated_count = 0
        os.makedirs("./model_cache/training_data", exist_ok=True)
        
        with open("./model_cache/training_data/training_triplets.jsonl", "a", encoding="utf-8") as f:
            for record in records:
                payload = record.payload
                desc = payload.get("description", "")
                mpn = payload.get("catalog_mpn", "")
                if not desc: continue
                
                # 2. Ask Ollama to generate a messy version
                prompt = (
                    f"Act as a lazy procurement engineer. "
                    f"Write a messy, highly abbreviated, 3-to-5 word BOM description "
                    f"for this electronic component: '{desc}'. "
                    f"Return ONLY the messy string, no quotes, no explanation."
                )
                
                try:
                    res = httpx.post(
                        "http://172.17.0.1:8010/api/generate",
                        json={"model": "llama3:8b", "prompt": prompt, "stream": False},
                        timeout=30.0
                    )
                    if res.status_code == 200:
                        messy_anchor = res.json().get("response", "").strip().strip('"\'')
                        
                        triplet = {
                            "anchor": messy_anchor,
                            "positive_mpn": mpn,
                            "positive_description": desc,
                            "negative_description": None
                        }
                        f.write(json.dumps(triplet) + "\n")
                        generated_count += 1
                        
                        # Update task progress
                        self.update_state(state='PROGRESS', meta={'current': generated_count, 'total': limit})
                        
                except Exception as e:
                    logger.error(f"Ollama request failed: {e}")
                    
        return {"status": "complete", "generated": generated_count}
    except Exception as e:
        logger.error(f"❌ Synthetic data task failed: {e}")
        return {"status": "failed", "error": str(e)}

@celery_app.task(name="trigger_model_finetuning_task", bind=True)
def trigger_model_finetuning_task(self):
    """Executes the local fine-tuning script."""
    from finetune_model import run_finetuning
    logger.info("🧠 Starting Model Fine-Tuning Task")
    
    try:
        result = run_finetuning()
        return result
    except Exception as e:
        logger.error(f"❌ Fine-tuning failed: {e}")
        return {"status": "failed", "error": str(e)}
