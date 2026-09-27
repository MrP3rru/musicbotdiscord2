FROM brainicism/bgutil-ytdlp-pot-provider:2.0.0 AS pot
# Use the same Node 26 Debian environment used by the PO provider.  Copying
# only /usr/local/bin/node into Python's slim image missed Node's shared libs
# and made the provider exit with shell code 127 on Render.
FROM node:26-bookworm-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH POT_SERVER_SCRIPT=/opt/pot/build/main.js
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv
COPY --from=pot /app /opt/pot
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py limits.py provider.py youtube_auth.py ./
RUN useradd --create-home bot && chown -R bot:bot /app /opt/venv
USER bot
CMD ["/opt/venv/bin/python", "bot.py"]
