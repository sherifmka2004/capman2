FROM python:3.12-slim

WORKDIR /app

COPY services/insights/requirements.txt services/insights/requirements.txt
RUN pip install --no-cache-dir -r services/insights/requirements.txt

COPY services/insights/ services/insights/

ENV PYTHONPATH=/app/services/insights \
    PORT=8080

EXPOSE 8080

WORKDIR /app/services/insights
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
