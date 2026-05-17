FROM python:3.12-slim

ARG TAILWINDCSS_VERSION=v3.4.17

# ffmpeg + ffprobe for on-demand HLS, ca-certificates for HTTPS to Telegram,
# curl to grab the Tailwind standalone binary (removed after).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         ffmpeg \
         ca-certificates \
         curl \
    && curl -fsSL -o /usr/local/bin/tailwindcss \
       "https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWINDCSS_VERSION}/tailwindcss-linux-x64" \
    && chmod +x /usr/local/bin/tailwindcss \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

# Pre-compile Tailwind. Scans template/**/*.html for class usage and emits a
# small minified CSS instead of running the JIT engine in every browser.
RUN mkdir -p main/server/static \
    && tailwindcss \
        -c tailwind.config.js \
        -i static_src/input.css \
        -o main/server/static/tailwind.css \
        --minify

ENV PYTHONUNBUFFERED=1 \
    PORT=8080
EXPOSE 8080

CMD ["python", "-m", "main"]
