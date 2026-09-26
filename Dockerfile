FROM denoland/deno:bin-2.6.8 AS deno
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=deno /deno /usr/local/bin/deno
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY bot.py limits.py ./
RUN useradd --create-home bot && chown -R bot:bot /app
USER bot
CMD ["python", "bot.py"]
