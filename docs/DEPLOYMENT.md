# SourceBuddy Production Deployment Guide
## Target Server: `164.52.192.57`

This document outlines the detailed system requirements, network ports, Docker build processes, and hardware utilization configurations for deploying the SourceBuddy Sourcing Engine to the production server.

---

## 1. Network Architecture & Ports

The SourceBuddy environment runs inside a secured Docker Bridge network (`sourcebuddy-net`). 
Here are the port requirements and routing rules for the host system:

| Service Name | Container Port | Host Port | Accessibility | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| **sourcebuddy-app** | `8005` | `8005` | **Public** (or via reverse proxy) | Serves the FastAPI Backend REST APIs and the Standalone AI Search UI dashboard. |
| **sourcebuddy-qdrant** | `6333` | `6333` | **Internal** (External block recommended) | Port for Qdrant Vector database REST API. *Keep blocked at the firewall level for external traffic; only internal container communication is required.* |
| **sourcebuddy-redis** | `6379` | *None* | **Internal-Only** (No port mapping) | Message broker and task result backend for Celery. Restricted strictly to the Docker private network. |
| **sourcebuddy-celery** | *None* | *None* | **Internal-Only** | Asynchronous worker processing the heavy BOM parsing, sentence embedding generation, and Qdrant re-indexing. |
| **External MySQL DB** | `3306` | *Outbound* | **Outbound Only** | System database (`35.207.243.110:3306`). The app connects outbound to store/retrieve auth records and session data. |

> [!IMPORTANT]
> Keep Port `6333` closed to the public internet on the host firewall. The containerized app connects to Qdrant using the private Docker network name `sourcebuddy-qdrant:6333`.

---

## 2. Dynamic Hardware Resource Allocation (RAM & GPU)

Per requirements, **no strict memory limits or hardware constraints are set inside Docker Compose**. This ensures that Docker and PyTorch dynamically use the maximum available resources on the server.

### System RAM (Dynamic)
Docker containers by default do not restrict RAM usage. SourceBuddy will dynamically scale and consume host RAM depending on ingestion batch sizes and active worker threads.
- For bulk indexing of the **1.8M record dataset**, the system will automatically utilize the host RAM to load CSV/Excel chunks.
- If the server has 16GB, 32GB, or more RAM, Docker will fully leverage it to keep search caches and DB pages warm.

### GPU Acceleration (Dynamic CUDA)
The core AI model (`sourcebuddy-electronics-bert`) runs search embeddings and batch indexing.
- We have introduced a **GPU-optimized Dockerfile** (`Dockerfile.gpu`) that pulls a CUDA-enabled PyTorch build.
- We modified the core engine (`qdrant_manager.py`) to auto-detect the GPU device at runtime:
  ```python
  import torch
  device = "cuda" if torch.cuda.is_available() else "cpu"
  self.model = SentenceTransformer(local_path_str, device=device)
  ```
- If an active NVIDIA GPU is present, it will run fully accelerated. If the GPU is fully busy or missing, it falls back to CPU cleanly.
- No static GPU memory limits are specified, meaning PyTorch will allocate and manage GPU VRAM dynamically.

---

## 3. Host System Prerequisites (For Server Team)

Before running the Docker images, the target server (`164.52.192.57`) must be prepared with the following host-level drivers:

1. **NVIDIA Proprietary Driver**: Ensure the latest NVIDIA host driver is installed. Check with:
   ```bash
   nvidia-smi
   ```
2. **Docker & Docker Compose**: Ensure Docker Engine is installed and active.
3. **NVIDIA Container Toolkit**: This is the crucial bridge that allows Docker containers to interact with the host GPU.
   - **Debian/Ubuntu installation example**:
     ```bash
     # Add the package repositories
     curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg \
       && curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
         sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
         sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
     
     # Install the toolkit
     sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
     
     # Configure Docker runtime to use NVIDIA
     sudo nvidia-container-toolkit-runtime-configure --runtime=docker
     
     # Restart Docker
     sudo systemctl restart docker
     ```

---

## 4. Build & Deployment Procedure

We have provided a dedicated `docker-compose.prod.yml` configured with the custom GPU deployment settings.

### Step 1: Copy Code & Verify Structure
Ensure the folder structure on the server contains the following:
```
AI_Vector_Bom_Sourcing/
├── docker-compose.prod.yml
├── model_cache/
│   └── sourcebuddy-electronics-bert/   <-- Pre-trained model weights must reside here
└── AI_Sourcing_API/
    ├── Dockerfile.gpu
    ├── requirements.txt
    ├── main.py
    └── db_config.json
```

### Step 2: Build the GPU-Optimized Images
Build the production Docker images. This installs system dependencies (Tesseract OCR, compilers) and downloads the CUDA-compatible PyTorch libraries.
```bash
docker compose -f docker-compose.prod.yml build
```

### Step 3: Run the Stack
Start all services in detached background mode:
```bash
docker compose -f docker-compose.prod.yml up -d
```

---

## 5. Post-Deployment Verification

The server team can run the following validation checks to ensure the application is successfully deployed and GPU-accelerated:

### A. Health & Port Check
Verify that the FastAPI service is responding and healthy:
```bash
curl -f http://localhost:8005/health
```
*(Should return `{"status":"healthy", "timestamp": "..."}`)*

### B. Verify CUDA / GPU Recognition in the Container
Run this quick command to confirm that PyTorch inside the running Docker container successfully sees and utilizes the host GPU:
```bash
docker exec -it sourcebuddy-app python -c "import torch; print('🔥 GPU Available:', torch.cuda.is_available()); print('🖥️ GPU Device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'None')"
```
- **Successful GPU Output**:
  ```
  🔥 GPU Available: True
  🖥️ GPU Device: NVIDIA GeForce RTX ... (or A100/L4/etc.)
  ```
- **CPU Fallback Output**:
  ```
  🔥 GPU Available: False
  🖥️ GPU Device: None
  ```

### C. Live Log Inspection
Monitor the application logs to verify the database connections and background AI engine load status:
```bash
docker logs -f sourcebuddy-app
```
*(You should see: `✅ AI Engine Loaded Successfully!` and `🖥️ Using device: CUDA`)*
