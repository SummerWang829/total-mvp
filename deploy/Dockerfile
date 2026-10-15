# Production image for the web app (FastAPI + ffmpeg for recording mix / clips / watermark).
FROM python:3.12-slim

# ffmpeg: recording mix, clips, watermark.  fonts-noto-cjk: Chinese glyphs for the watermark (app/media.py finds it).
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/total
COPY requirements.txt .
# test-only packages are not needed at runtime
RUN grep -viE '^(pytest|playwright|pillow|numpy|websockets)' requirements.txt > /tmp/requirements.runtime.txt \
    && pip install --no-cache-dir -r /tmp/requirements.runtime.txt

COPY app ./app
# The AI moderator reads its demo interview guides (eval/guides) and the offline scripted brain (eval/mock_brain.py) at runtime.
COPY eval ./eval

ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--log-level", "warning"]
