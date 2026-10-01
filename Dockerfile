FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y ffmpeg fontconfig fonts-dejavu-core && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["gunicorn", "--bind", "0.0.0.0:10000", "--workers", "1", "--timeout", "600", "--graceful-timeout", "30", "--worker-tmp-dir", "/dev/shm", "app:app"]
