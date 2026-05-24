FROM python:3.12-slim AS builder

WORKDIR /app
COPY pyproject.toml setup.cfg .
COPY src/ ./src/

RUN pip install --no-cache-dir build && \
    python -m build --wheel

# ─── Runtime ───────────────────────────────────────────────

FROM python:3.12-slim

WORKDIR /app
COPY --from=builder /app/dist/*.whl .

RUN pip install --no-cache-dir *.whl && rm *.whl

# Copy static files directly into the installed package
# (setuptools wheels don't include non-Python files)
COPY src/memory_bridge/static/ /usr/local/lib/python3.12/site-packages/memory_bridge/static/

# Copy startup script
COPY run.sh .
RUN chmod +x run.sh

EXPOSE 8000

CMD ["./run.sh"]
