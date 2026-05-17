FROM python:3.12-slim

# ffmpeg + ffprobe for on-demand HLS, plus build deps for any C extensions
# (pytgcrypto ships wheels for cp312 so no compiler should be needed in
# practice, but keep gcc around as a defensive measure for small deps).
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

ENV PYTHONUNBUFFERED=1 \
    PORT=8080
EXPOSE 8080

CMD ["python", "-m", "main"]
