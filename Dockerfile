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

# Ensure static files are present at the right path
RUN python -c "import os, memory_bridge; static = os.path.join(os.path.dirname(memory_bridge.__file__), 'static'); print('Static dir:', static, 'Exists:', os.path.isdir(static)); print(os.listdir(static) if os.path.isdir(static) else 'NOT FOUND')"

# Copy startup script
COPY run.sh .
RUN chmod +x run.sh

EXPOSE 8000

CMD ["./run.sh"]
