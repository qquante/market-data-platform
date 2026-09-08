FROM python:3.13.15-slim-trixie@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy HOME=/app
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.11
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project
ENV PATH="/app/.venv/bin:$PATH"
RUN python -c "import duckdb; c=duckdb.connect(); c.execute('INSTALL ducklake'); c.execute('INSTALL postgres'); c.close()"
COPY midas_data ./midas_data
COPY tests ./tests
COPY docker/entrypoint.sh /entrypoint.sh
RUN uv sync --locked && chmod -R a+rX /app/midas_data /app/tests /app/.duckdb && chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "midas_data.cli", "api"]
