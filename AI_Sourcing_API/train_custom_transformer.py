"""
SourceBuddy — Custom Electronics Transformer Training Pipeline
==============================================================
This script trains a custom technical tokenizer and pre-trains a compact 
BERT-style transformer model from scratch on the 1.8M records of IN_products.txt.

Requirements:
    pip install torch transformers tokenizers pandas tqdm
"""

import os
import time
import logging
import pandas as pd
from pathlib import Path
from tqdm import tqdm

import torch
from torch.utils.data import Dataset, DataLoader
from tokenizers import BertWordPieceTokenizer
from transformers import (
    BertConfig,
    BertForMaskedLM,
    DataCollatorForLanguageModeling,
    Trainer,
    TrainingArguments
)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("CustomTraining")

BASE_DIR = Path(__file__).parent.parent
DATA_FILE = BASE_DIR / "uploads" / "IN_products.txt"
OUTPUT_DIR = Path(__file__).parent / "custom_model_output"
TOKENIZER_DIR = OUTPUT_DIR / "tokenizer"
MODEL_DIR = OUTPUT_DIR / "transformer"

# Make sure directories exist
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TOKENIZER_DIR.mkdir(parents=True, exist_ok=True)
MODEL_DIR.mkdir(parents=True, exist_ok=True)

# Detect GPU
device = "cuda" if torch.cuda.is_available() else "cpu"
logger.info(f"🖥️ Training Device Detected: {device.upper()}")
if device == "cuda":
    logger.info(f"🔥 GPU Model: {torch.cuda.get_device_name(0)}")

# ==============================================================================
# STEP 1: TRAIN CUSTOM TECHNICAL TOKENIZER
# ==============================================================================
def train_custom_tokenizer():
    """Trains a BertWordPieceTokenizer on IN_products.txt to learn electronics jargon."""
    logger.info("🎬 STEP 1: Starting Custom WordPiece Tokenizer Training...")
    
    # Check if data file exists
    if not DATA_FILE.exists():
        raise FileNotFoundError(f"❌ Product catalog data file not found at: {DATA_FILE}")
        
    vocab_size = 30000
    
    # We write a clean subset of descriptions to a temporary text file for the tokenizer trainer
    temp_txt = OUTPUT_DIR / "tokenizer_train_data.txt"
    logger.info(f"📝 Extracting clean training descriptions for tokenizer training to {temp_txt}...")
    
    chunk_size = 50000
    total_written = 0
    
    # Stream the file to avoid loading 1 GB in memory at once (safe for all machines)
    with open(temp_txt, "w", encoding="utf-8") as out_f:
        # Read caret-delimited file
        reader = pd.read_csv(
            DATA_FILE, 
            sep="^", 
            chunksize=chunk_size, 
            dtype=str, 
            on_bad_lines="skip", 
            quotechar='"', 
            doublequote=True
        )
        for chunk in tqdm(reader, desc="Streaming text for tokenizer"):
            # Extract description column (column 11) or raw_data if column not found
            cols = chunk.columns
            idx_desc = next((i for i, c in enumerate(cols) if 'desc' in str(c).lower()), -1)
            
            for _, row in chunk.iterrows():
                row_list = [str(x).strip() for x in row.tolist() if pd.notna(x)]
                desc = row_list[idx_desc] if 0 <= idx_desc < len(row_list) else " ".join(row_list)
                if desc and desc.lower() not in ("nan", "none", ""):
                    out_f.write(desc + "\n")
                    total_written += 1
            
            # Limit tokenizer corpus size to first 500k rows for extremely fast tokenizer training
            if total_written >= 500000:
                logger.info("⏹️ Tokenizer corpus size reached 500k rows. Proceeding to fit...")
                break
                
    # Initialize and train tokenizer
    tokenizer = BertWordPieceTokenizer(
        clean_text=True,
        handle_chinese_chars=False,
        strip_accents=True,
        lowercase=True
    )
    
    logger.info("⏳ Fitting WordPiece Tokenizer (this takes about 2-3 minutes on CPU)...")
    tokenizer.train(
        files=[str(temp_txt)],
        vocab_size=vocab_size,
        min_frequency=2,
        special_tokens=["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]"],
        wordpieces_prefix="##"
    )
    
    # Save the trained tokenizer
    tokenizer.save_model(str(TOKENIZER_DIR))
    logger.info(f"✅ Tokenizer Saved Successfully to: {TOKENIZER_DIR}")
    
    # Clean up temp file
    if temp_txt.exists():
        os.remove(temp_txt)

# ==============================================================================
# STEP 2: PYTORCH DATASET CLASS (MEMORY EFFICIENT)
# ==============================================================================
class LazyElectronicsDataset(Dataset):
    """Memory-efficient streaming dataset that loads tokens from processed catalog strings."""
    def __init__(self, file_path, tokenizer, max_length=64, max_records=200000):
        self.file_path = file_path
        self.tokenizer = tokenizer
        self.max_length = max_length
        self.texts = []
        
        logger.info(f"📖 Preloading up to {max_records} records into memory for training...")
        
        # Stream load to avoid high memory spikes
        chunk_size = 20000
        reader = pd.read_csv(
            file_path, 
            sep="^", 
            chunksize=chunk_size, 
            dtype=str, 
            on_bad_lines="skip", 
            quotechar='"', 
            doublequote=True
        )
        
        for chunk in tqdm(reader, desc="Loading dataset"):
            cols = chunk.columns
            idx_desc = next((i for i, c in enumerate(cols) if 'desc' in str(c).lower()), -1)
            idx_mpn = next((i for i, c in enumerate(cols) if 'mpn' in str(c).lower() or 'part' in str(c).lower()), -1)
            
            for _, row in chunk.iterrows():
                row_list = [str(x).strip() for x in row.tolist() if pd.notna(x)]
                mpn = row_list[idx_mpn] if 0 <= idx_mpn < len(row_list) else ""
                desc = row_list[idx_desc] if 0 <= idx_desc < len(row_list) else ""
                
                # Combine parameters: Manufacturer + MPN + Description
                text = f"{mpn} {desc}".strip()
                if text and len(text) > 5:
                    self.texts.append(text)
                    
                if len(self.texts) >= max_records:
                    break
            if len(self.texts) >= max_records:
                break
                
        logger.info(f"✅ Dataset Initialized: Loaded {len(self.texts)} active records!")

    def __len__(self):
        return len(self.texts)

    def __getitem__(self, idx):
        text = self.texts[idx]
        encoding = self.tokenizer(
            text,
            max_length=self.max_length,
            padding="max_length",
            truncation=True,
            return_tensors="pt"
        )
        # Squeeze to remove batch dimension
        return {
            "input_ids": encoding["input_ids"].squeeze(0),
            "attention_mask": encoding["attention_mask"].squeeze(0)
        }

# ==============================================================================
# STEP 3: MASKED LANGUAGE PRE-TRAINING (MLM)
# ==============================================================================
def run_pretraining(epochs=5, batch_size=64):
    """Initializes and pre-trains a compact BERT model from scratch on technical tokens."""
    logger.info("🎬 STEP 2: Initializing Transformer pre-training model...")
    
    # 1. Load Custom Tokenizer
    from transformers import BertTokenizerFast
    vocab_file = TOKENIZER_DIR / "vocab.txt"
    if not vocab_file.exists():
        raise FileNotFoundError(f"Tokenizer vocabulary file not found at: {vocab_file}. Please run tokenizer training first!")
        
    tokenizer = BertTokenizerFast(vocab_file=str(vocab_file), lowercase=True)
    
    # 2. Define Compact BERT Config (6 layers, 384 hidden size, same size as MiniLM-L6)
    config = BertConfig(
        vocab_size=tokenizer.vocab_size,
        hidden_size=384,
        num_hidden_layers=6,
        num_attention_heads=12,
        intermediate_size=1536,
        max_position_embeddings=128,
        pad_token_id=tokenizer.pad_token_id,
        bos_token_id=tokenizer.cls_token_id,
        eos_token_id=tokenizer.sep_token_id
    )
    
    model = BertForMaskedLM(config)
    logger.info(f"✨ Custom Transformer initialized: {model.num_parameters() / 1e6:.1f} Million Parameters.")
    
    # 3. Load Dataset (Limit to 200k for demonstration, scale to 1.8M on heavy GPU servers!)
    dataset = LazyElectronicsDataset(
        file_path=DATA_FILE,
        tokenizer=tokenizer,
        max_length=64,
        max_records=200000
    )
    
    # 4. Collator for Masked Language Modeling
    data_collator = DataCollatorForLanguageModeling(
        tokenizer=tokenizer, 
        mlm=True, 
        mlm_probability=0.15
    )
    
    # 5. Define HuggingFace Training Arguments optimized for high-end GPUs
    training_args = TrainingArguments(
        output_dir=str(OUTPUT_DIR / "results"),
        overwrite_output_dir=True,
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        save_steps=1000,
        save_total_limit=2,
        prediction_loss_only=True,
        fp16=torch.cuda.is_available(), # Activate automatic mixed precision on GPU!
        logging_steps=100,
        learning_rate=1e-4,
        weight_decay=0.01,
        report_to="none"
    )
    
    # 6. Initialize Trainer
    trainer = Trainer(
        model=model,
        args=training_args,
        data_collator=data_collator,
        train_dataset=dataset
    )
    
    # 7. Start pre-training
    logger.info("🚀 Starting PyTorch pre-training loop...")
    start_time = time.time()
    trainer.train()
    elapsed = time.time() - start_time
    logger.info(f"🎉 Pre-training Complete! Time Taken: {elapsed/3600:.2f} hours")
    
    # Save the pre-trained transformer model
    model.save_pretrained(str(MODEL_DIR))
    tokenizer.save_pretrained(str(MODEL_DIR))
    logger.info(f"💾 Custom Electronics Model saved successfully to: {MODEL_DIR}")

if __name__ == "__main__":
    logger.info("🔥 Starting SourceBuddy Sourcing-BERT Training Pipeline...")
    
    # Step 1: Train vocabulary
    if not (TOKENIZER_DIR / "vocab.txt").exists():
        train_custom_tokenizer()
    else:
        logger.info("ℹ️ Custom tokenizer already trained. Skipping Step 1.")
        
    # Step 2: Run Masked Language Pre-training
    run_pretraining(epochs=5, batch_size=64)
    logger.info("🎯 Pipeline execution successfully completed!")
