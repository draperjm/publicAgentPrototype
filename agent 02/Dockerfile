# Use Python 3.9 Slim
FROM python:3.9-slim

WORKDIR /app

# Install system build tools (needed for some python packages)
RUN apt-get update && apt-get install -y build-essential curl

# 1. Upgrade pip
RUN pip install --upgrade pip

# 2. Install PyTorch CPU-only FIRST (Prevents timeout & keeps image small)
# We set a high timeout (1000s) just in case your internet is slow
RUN pip install --no-cache-dir --timeout=1000 torch torchvision --index-url https://download.pytorch.org/whl/cpu

# 3. Copy requirements and install the rest
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy the application code
COPY agent.py .
COPY tasks.json .
# Copy the registry if you use it, or map it via volumes (we map it in compose, but good to have)
COPY registry.json . 

# Expose the port
EXPOSE 8000

# Start the application
CMD ["uvicorn", "agent:app", "--host", "0.0.0.0", "--port", "8000"]