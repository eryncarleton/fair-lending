# Image for the `app` service in docker-compose.yaml (Docker-only mode).
# The project folder is mounted at /workspace at run time, so code edits don't need a rebuild.
# Rebuild only when requirements.txt changes: docker compose build app
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /workspace
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.headless=true"]
