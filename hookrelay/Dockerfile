FROM python:3.14-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY hookrelay/ hookrelay/
COPY config.example.yaml .

ENV HOOKRELAY_HOST=0.0.0.0 \
    HOOKRELAY_PORT=8100 \
    HOOKRELAY_DB=/data/hookrelay.db \
    HOOKRELAY_CONFIG=/data/config.yaml

EXPOSE 8100
HEALTHCHECK --interval=15s --timeout=3s CMD ["python", "-c", "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"HOOKRELAY_PORT\",\"8100\")}/healthz')"]
CMD ["python", "-m", "hookrelay"]
