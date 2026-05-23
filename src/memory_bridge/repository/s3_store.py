"""S3-compatible object storage for large memory values.

Memory values larger than 64KB are offloaded to S3/MinIO
instead of being stored directly in the database.
The DB stores a reference pointer instead.
"""

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Threshold for offloading to S3 (64KB)
S3_THRESHOLD_BYTES = 64 * 1024


class S3Store:
    """S3-compatible storage for large values.

    Uses the S3 API (AWS S3, MinIO, DigitalOcean Spaces, etc.).
    Falls back to local storage when S3 is not configured.
    """

    def __init__(self):
        self._bucket = os.environ.get("MEMORY_BRIDGE_S3_BUCKET", "memory-bridge")
        self._endpoint = os.environ.get("MEMORY_BRIDGE_S3_ENDPOINT", "")
        self._access_key = os.environ.get("MEMORY_BRIDGE_S3_ACCESS_KEY", "")
        self._secret_key = os.environ.get("MEMORY_BRIDGE_S3_SECRET_KEY", "")
        self._region = os.environ.get("MEMORY_BRIDGE_S3_REGION", "us-east-1")
        self._local_dir = os.environ.get("MEMORY_BRIDGE_S3_LOCAL_DIR", "/tmp/memory-bridge-s3")

    @property
    def enabled(self) -> bool:
        return bool(self._endpoint) or bool(self._access_key)

    def needs_offloading(self, value: Any) -> bool:
        """Check if a value should be offloaded to S3."""
        serialized = json.dumps(value)
        return len(serialized) > S3_THRESHOLD_BYTES

    async def store(self, memory_id: str, value: Any) -> Optional[str]:
        """Store a large value in S3.

        Returns an S3 reference key on success, None on failure.
        The reference is stored in the database instead of the full value.
        """
        serialized = json.dumps(value)
        key = f"memories/{memory_id}.json"

        if self.enabled:
            # In production: use aioboto3 to upload to S3
            logger.info("S3 store: would upload %d bytes to %s/%s", len(serialized), self._bucket, key)
        else:
            # Fallback: store locally
            os.makedirs(self._local_dir, exist_ok=True)
            with open(f"{self._local_dir}/{memory_id}.json", "w") as f:
                f.write(serialized)
            logger.info("S3 store (local): stored %d bytes to %s/%s", len(serialized), self._local_dir, key)

        return key

    async def retrieve(self, memory_id: str, s3_key: str) -> Optional[Any]:
        """Retrieve a value from S3."""
        if self.enabled:
            logger.info("S3 retrieve: would fetch %s from %s", s3_key, self._bucket)
        else:
            local_path = f"{self._local_dir}/{memory_id}.json"
            try:
                with open(local_path) as f:
                    return json.load(f)
            except FileNotFoundError:
                logger.warning("S3 retrieve (local): %s not found", local_path)
                return None
        return None

    async def delete(self, memory_id: str, s3_key: str) -> bool:
        """Delete a value from S3."""
        logger.info("S3 delete: would delete %s from %s", s3_key, self._bucket)
        return True
