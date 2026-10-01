FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

WORKDIR /app

# Optimize Linux memory allocation to avoid OOM on 512MB Free tier
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV MALLOC_ARENA_MAX=2

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium browser binary
RUN playwright install chromium

# Copy application source code
COPY . .

# Expose Render default port
EXPOSE 10000

# Start uvicorn server binding to 0.0.0.0 and honoring Render's dynamic PORT
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
