FROM node:22-alpine AS console
WORKDIR /src/web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim
WORKDIR /app
RUN pip install --no-cache-dir uv==0.9.8
COPY pyproject.toml uv.lock ./
COPY backend/ ./backend/
RUN uv sync --frozen --no-dev
COPY --from=console /src/web/dist /app/web/dist
RUN useradd --uid 10001 --create-home appuser && mkdir -p /data/sources && chown -R appuser:appuser /data
USER appuser
ENV ONTOBRIDGE_DATA=/data ONTOBRIDGE_SOURCE_ROOT=/data/sources ONTOBRIDGE_DEMO=0
EXPOSE 8787
CMD ["/app/.venv/bin/ontobridge", "--host", "0.0.0.0", "--port", "8787"]
