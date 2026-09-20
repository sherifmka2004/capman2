FROM python:3.12-slim

WORKDIR /app

COPY services/analyst/requirements.txt services/analyst/requirements.txt
RUN pip install --no-cache-dir -r services/analyst/requirements.txt

COPY capman/__init__.py capman/__init__.py
COPY capman/events.py capman/events.py
COPY capman/web/ capman/web/
COPY capman/knowledge/__init__.py capman/knowledge/__init__.py
COPY capman/knowledge/privacy.py capman/knowledge/privacy.py
COPY capman/pipeline/__init__.py capman/pipeline/__init__.py
COPY capman/pipeline/session.py capman/pipeline/session.py
COPY capman/pipeline/prompts.py capman/pipeline/prompts.py
COPY capman/pipeline/prompts_web.py capman/pipeline/prompts_web.py
COPY capman/pipeline/analyzer.py capman/pipeline/analyzer.py
COPY capman/pipeline/web_analyzer.py capman/pipeline/web_analyzer.py
COPY services/analyst/ services/analyst/

ENV PYTHONPATH=/app:/app/services/analyst

WORKDIR /app/services/analyst
CMD ["sh", "-c", "exec python run.py --manifests ${CAPMAN_WEB_MANIFESTS}"]
