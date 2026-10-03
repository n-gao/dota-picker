FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DOTA_PICKER_DB=/data/dota.sqlite \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY dota_picker ./dota_picker
RUN pip install . \
 && useradd --system --uid 10001 --no-create-home picker \
 && mkdir /data && chown picker /data

USER picker
# SQLite cache: mount a volume here to keep data across container updates.
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/status', timeout=4)"

CMD ["dota-picker", "serve"]
