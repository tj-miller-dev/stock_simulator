FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Every module at the api/ root, not a hand-maintained list. The allowlist
# this replaces silently shipped an image missing actions.py, apidocs.py and
# effects.py -- it built fine, pushed fine, and crashed on import in the
# cluster, where the old pods kept serving and hid it. Adding a module should
# never require remembering to edit a Dockerfile.
# tests/ is a directory and .venv/__pycache__ are in .dockerignore, so neither
# is picked up here.
COPY *.py ./
COPY engine/ ./engine/
COPY providers/ ./providers/

RUN useradd --create-home appuser
USER appuser

EXPOSE 8000

CMD ["python", "api.py"]
