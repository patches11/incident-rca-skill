FROM python:3.12-slim
RUN pip install --no-cache-dir fastapi==0.115.6 uvicorn==0.34.0 sqlalchemy==2.0.36 "psycopg[binary]==3.2.3" \
    prometheus_client==0.21.1 httpx==0.28.1
WORKDIR /app
COPY common /app/common
ARG SERVICE_DIR
COPY ${SERVICE_DIR} /app/svc
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
CMD ["sh", "-c", "uvicorn svc.app:app --host 0.0.0.0 --port 8080 --workers 1 --no-access-log"]
