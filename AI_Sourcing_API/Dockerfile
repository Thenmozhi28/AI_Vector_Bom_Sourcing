# Simple, working Dockerfile for SourceBuddy
FROM python:3.11-slim

WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# Install Tesseract and all dependencies in one layer
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libtesseract-dev \
    python3-dev \
    build-essential \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    libssl-dev \
    libffi-dev \
    libgl1 \
    libmariadb-dev \
    ca-certificates \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python packages
COPY AI_Sourcing_API/requirements.txt .
ENV PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir torch==2.3.0 && \
    pip install --no-cache-dir -r requirements.txt

# Copy backend and frontend code into the container
COPY AI_Sourcing_API/ .
COPY AI_Sourcing_UI/ UI/

# Create necessary directories
RUN mkdir -p uploads uploaded_images qdrant_db model_cache && chmod 755 uploads uploaded_images qdrant_db model_cache

# Clean up cached files
RUN rm -f *.pkl search_cache.pkl 2>/dev/null || true

# Expose port 8005
EXPOSE 8005

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8005/health || exit 1

# Start the application
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8005"]
