FROM python:3.11-slim

WORKDIR /app

# Install system utilities if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependencies
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY main.py .

# Expose port
EXPOSE 8010

# Command to run
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010"]
