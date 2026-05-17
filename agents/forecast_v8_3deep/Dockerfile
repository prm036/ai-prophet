FROM python:3.13-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY agents/forecast_v8_3deep/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install ai-prophet-core (for Kalshi client)
# In deployment, copy the package and install it
COPY packages/core /tmp/ai-prophet-core
RUN pip install --no-cache-dir /tmp/ai-prophet-core && rm -rf /tmp/ai-prophet-core

# Copy agent code
COPY agents/forecast_v8_3deep/ .

# Create cache directories
RUN mkdir -p /app/.cache/ballotpedia

# Port exposed by the server
EXPOSE 8000

# Start with 1 worker (agent is CPU-bound on LLM API calls, not compute)
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
