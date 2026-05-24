#!/bin/bash
set -e

echo "=== Memory Bridge Startup ==="
echo "Python: $(python --version)"
echo "Memory Bridge: $(python -c 'import memory_bridge; print("OK")' 2>&1)"

echo "--- Importing app ---"
python -c "
import sys
sys.stdout.flush()
from memory_bridge.main import app
print('App imported OK')
sys.stdout.flush()
"

echo "--- Starting uvicorn ---"
exec python -m uvicorn memory_bridge.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --log-level info
