FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api.py .

RUN useradd --create-home appuser
USER appuser

EXPOSE 8000

CMD ["python", "api.py"]
