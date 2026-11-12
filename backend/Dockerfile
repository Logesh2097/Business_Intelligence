# One image for the API and (from Phase 8) the MCP server. Build from the repository root:
#   docker build -f backend/Dockerfile .
# Layout and reasons: docs/design/deployment.md §3.

# 1. Build the web app.
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# 2. The Python image, with the built web app inside it.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN pip install .

COPY --from=web /web/dist ./app/static

RUN useradd --system --uid 10001 bi
USER bi

EXPOSE 8110
CMD ["uvicorn", "app.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8110"]
