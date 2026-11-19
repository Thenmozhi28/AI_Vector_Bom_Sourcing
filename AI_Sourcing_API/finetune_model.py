import os
import json
import csv
import datetime
import logging
import random
from sentence_transformers import SentenceTransformer, InputExample, losses
from sentence_transformers.evaluation import InformationRetrievalEvaluator
from torch.utils.data import DataLoader

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

# Configuration
TRIPLETS_FILE = "./model_cache/training_data/training_triplets.jsonl"
BASE_MODEL_PATH = "./model_cache/sourcebuddy-electronics-bert"
OUTPUT_MODEL_PATH = "./model_cache/sourcebuddy-electronics-bert" # Continuous learning: overwrite base model
EPOCHS = 3
MIN_BATCH_SIZE = 32
MAX_BATCH_SIZE = 128

def run_finetuning():
    if not os.path.exists(TRIPLETS_FILE):
        msg = f"Training data not found at {TRIPLETS_FILE}."
        logger.error(msg)
        return {"status": "error", "message": msg}

    logger.info("Loading training triplets...")
    all_data = []
    
    with open(TRIPLETS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            try:
                data = json.loads(line)
                anchor = data.get("anchor")
                positive = data.get("positive_description")
                negative = data.get("negative_description")
                
                # Allow pairs (Anchor, Positive) without negatives
                if anchor and positive:
                    all_data.append((anchor, positive))
            except Exception as e:
                logger.warning(f"Failed to parse line: {e}")
                
    if len(all_data) < 10:
        msg = f"Not enough valid training examples found ({len(all_data)}). Need at least 10 for Train/Validation split."
        logger.error(msg)
        return {"status": "error", "message": msg}
        
    # AutoTrain: Shuffle and Split (90% Train, 10% Validation)
    random.seed(42)
    random.shuffle(all_data)
    val_size = max(1, int(len(all_data) * 0.1))
    val_data = all_data[:val_size]
    train_data = all_data[val_size:]
    
    logger.info(f"Loaded {len(all_data)} total examples.")
    logger.info(f"AutoTrain Split: {len(train_data)} training and {len(val_data)} validation examples.")
    
    # Convert train data to InputExamples (handling optional negatives)
    train_examples = [InputExample(texts=[d[0], d[1]]) for d in train_data]
    
    # Extract validation lists for InformationRetrievalEvaluator
    val_queries = {}
    val_corpus = {}
    val_rel_docs = {}
    
    for idx, d in enumerate(val_data):
        qid = f"q_{idx}"
        docid = f"d_{idx}"
        val_queries[qid] = d[0]
        val_corpus[docid] = d[1]
        val_rel_docs[qid] = {docid}
        
    evaluator = InformationRetrievalEvaluator(val_queries, val_corpus, val_rel_docs, name="val-eval")
    
    # AutoTrain: Dynamic Batch Sizing (max 64, scales with data size)
    # Roughly 10 batches per epoch is a good minimum target
    calculated_batch = len(train_examples) // 10
    batch_size = max(MIN_BATCH_SIZE, min(MAX_BATCH_SIZE, calculated_batch))
    logger.info(f"AutoTrain: Dynamically selected Batch Size = {batch_size}")
    
    logger.info(f"Loading base model from {BASE_MODEL_PATH}...")
    try:
        model = SentenceTransformer(BASE_MODEL_PATH)
    except Exception as e:
        msg = f"Failed to load base model: {e}"
        logger.error(msg)
        return {"status": "error", "message": msg}

    logger.info(f"Starting AutoTrain for {EPOCHS} epochs with Live Evaluation...")
    os.makedirs(OUTPUT_MODEL_PATH, exist_ok=True)
    
    train_dataloader = DataLoader(train_examples, shuffle=True, batch_size=batch_size)
    train_loss = losses.MultipleNegativesRankingLoss(model=model)
    
    # Fit model
    model.fit(
        train_objectives=[(train_dataloader, train_loss)],
        epochs=EPOCHS,
        warmup_steps=10,
        evaluator=evaluator,
        evaluation_steps=200,
        output_path=OUTPUT_MODEL_PATH,
        save_best_model=True,
        show_progress_bar=True
    )
    
    
    # Extract evaluation metrics from CSV
    try:
        csv_path = os.path.join(OUTPUT_MODEL_PATH, "eval", "Information-Retrieval_evaluation_val-eval_results.csv")
        if os.path.exists(csv_path):
            with open(csv_path, 'r') as csvfile:
                reader = csv.DictReader(csvfile)
                rows = list(reader)
                if rows:
                    best_row = rows[-1] # Usually the last row is the final epoch (if not overwritten by save_best)
                    # Actually, we can find the row with the best MAP@100
                    best_row = max(rows, key=lambda x: float(x.get('cos_sim-MAP@100', x.get('MAP@100', 0))))
                    
                    metrics = {
                        "accuracy_at_1": float(best_row.get('cos_sim-Accuracy@1', 0)),
                        "accuracy_at_5": float(best_row.get('cos_sim-Accuracy@5', 0)),
                        "accuracy_at_10": float(best_row.get('cos_sim-Accuracy@10', 0)),
                        "map_at_100": float(best_row.get('cos_sim-MAP@100', 0)),
                        "epoch": int(best_row.get('epoch', 0)),
                        "timestamp": datetime.datetime.now().isoformat()
                    }
                    
                    metrics_path = os.path.join(os.path.dirname(OUTPUT_MODEL_PATH), "latest_metrics.json")
                    with open(metrics_path, 'w') as mf:
                        json.dump(metrics, mf)
                    logger.info(f"Saved latest training metrics to {metrics_path}")
    except Exception as e:
        logger.error(f"Failed to extract metrics from CSV: {e}")
        
    logger.info(f"✅ AutoTrain complete! Best model automatically saved to {OUTPUT_MODEL_PATH}")
    return {"status": "success", "message": f"AutoTrain completed successfully! Best model saved to {OUTPUT_MODEL_PATH}"}

if __name__ == "__main__":
    run_finetuning()
