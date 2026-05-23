import sys
sys.path.insert(0, "src")
try:
    from memory_bridge.repository.postgres_repo import PostgresMemoryRepository
    print("OK")
except Exception as e:
    print(f"ERROR: {e}")
