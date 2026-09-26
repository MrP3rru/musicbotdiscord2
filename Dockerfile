FROM denoland/deno:bin-2.6.8 AS deno
FROM brainicism/bgutil-ytdlp-pot-provider:2.0.0 AS pot
FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 POT_SERVER_SCRIPT=/opt/pot/build/main.js
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=deno /deno /usr/local/bin/deno
COPY --from=pot /usr/local/bin/node /usr/local/bin/node
COPY --from=pot /app /opt/pot
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY bot.py limits.py provider.py ./
RUN useradd --create-home bot && chown -R bot:bot /app
USER bot
CMD ["python", "bot.py"]
