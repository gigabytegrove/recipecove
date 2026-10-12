FROM python:3.12-slim
ARG RI_VERSION=2.1.7
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/models DATA_DIR=/data RI_IMAGE_VERSION=${RI_VERSION}
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr tesseract-ocr-eng ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home importer \
    && mkdir -p /models /data && chown -R importer:importer /models /data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
COPY update_release.json Dockerfile ./
COPY web ./web
USER importer
EXPOSE 8999
CMD ["python", "-u", "/app/launcher.py"]
