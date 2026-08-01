FROM node:22-slim AS frontend-builder

WORKDIR /app

COPY frontend/package*.json frontend/
RUN cd frontend && npm ci

COPY frontend frontend
RUN cd frontend \
    && mkdir -p ../main/server/static \
    && npm run build


# ffmpeg + ffprobe for on-demand HLS, ca-certificates for HTTPS to Telegram.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         ffmpeg \
         ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

COPY --from=frontend-builder /app/main/server/static/app main/server/static/app

ENV PYTHONUNBUFFERED=1 \
    PORT=8080
EXPOSE 8080

CMD ["python", "-m", "main"]
