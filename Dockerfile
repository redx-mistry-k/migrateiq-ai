FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY frontend ./frontend
RUN --mount=type=cache,target=/root/.cache/pip pip install .
COPY . .
CMD ["uvicorn", "migrateiq.api:app", "--host", "0.0.0.0", "--port", "8000"]
