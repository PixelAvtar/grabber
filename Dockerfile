FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=5050
EXPOSE 5050

# 2 workers is enough for personal use; each download runs in a background thread
CMD ["sh", "-c", "gunicorn --workers 2 --threads 4 --timeout 600 --bind 0.0.0.0:$PORT app:app"]
