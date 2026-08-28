FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY app ./app
COPY static ./static
RUN mkdir -p /app/runtime/reports

EXPOSE 39057
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:39057/api/health', timeout=3)" || exit 1

# OAuth authorization codes and robot callback tokens arrive in query strings.
# Disable generic access logs so those short-lived credentials never enter the
# container log; application errors and lifecycle logs remain enabled.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "39057", "--workers", "1", "--no-access-log"]
