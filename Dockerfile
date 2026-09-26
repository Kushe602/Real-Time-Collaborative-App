# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install the app (and its dependencies). Templates and static assets ship as
# package-data (see pyproject.toml), so the installed package is self-contained.
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --upgrade pip && pip install .

# Drop root; make the workdir writable so a standalone SQLite run also works.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Structural reads + the multiplexed workspace WebSocket are both served here.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
