FROM python:3.12-slim

WORKDIR /app

COPY services/collector/requirements.txt services/collector/requirements.txt
RUN pip install --no-cache-dir -r services/collector/requirements.txt

COPY capman/__init__.py capman/__init__.py
COPY capman/web/ capman/web/
COPY services/collector/ services/collector/

ENV PYTHONPATH=/app:/app/services/collector \
    PORT=8080

EXPOSE 8080

WORKDIR /app/services/collector
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
