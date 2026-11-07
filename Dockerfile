FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends git libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Downloads SKAB (and the UCI hydraulic set when reachable) on first start, then serves.
CMD ["sh", "-c", "python -m lineguard download && python -m uvicorn service.app:app --host 0.0.0.0 --port 8000"]
EXPOSE 8000
