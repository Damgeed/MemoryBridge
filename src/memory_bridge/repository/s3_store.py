"""S3-compatible object storage for large memory values.

Memory values larger than 64KB are offloaded to S3/MinIO
instead of being stored directly in the database.
The DB stores a reference pointer instead.
"""

import json
import logging
import os
from typing import Any, Optional

try:
    import aioboto3
except ImportError:
    aioboto3 = None  # type: ignore[assignment]

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
        """Store a large value in S3 (or local fallback).

        Returns an S3 reference key on success, None on failure.
        The reference is stored in the database instead of the full value.

        When S3 is configured, we log the intent (actual S3 upload via aioboto3
        is left as a TODO for when a real S3 endpoint is wired). In all cases,
        we always write to the local fallback directory so data is never lost.
        """
        serialized = json.dumps(value)
        key = f"memories/{memory_id}.json"

        if self.enabled:
            if aioboto3 is None:
                logger.warning("aioboto3 not installed. Install with: pip install memory-bridge[ml] or pip install aioboto3")
            else:
                try:
                    session = aioboto3.Session()
                    async with session.client(
                        "s3",
                        endpoint_url=self._endpoint or None,
                        aws_access_key_id=self._access_key,
                        aws_secret_access_key=self._secret_key,
                        region_name=self._region,
                    ) as s3:
                        await s3.put_object(
                            Bucket=self._bucket,
                            Key=key,
                            Body=serialized.encode(),
                            ContentType="application/json",
                        )
                    logger.info("S3 store: uploaded %d bytes to %s/%s", len(serialized), self._bucket, key)
                except Exception as e:
                    logger.warning("S3 upload failed for %s: %s — saving locally only", key, e)

        # Always persist to local fallback — guarantees we never lose data
        os.makedirs(self._local_dir, exist_ok=True)
        filepath = os.path.join(self._local_dir, f"{memory_id}.json")
        with open(filepath, "w") as f:
            f.write(serialized)
        logger.info("S3 store: saved %d bytes to %s", len(serialized), filepath)

        return key

    async def retrieve(self, memory_id: str, s3_key: str) -> Optional[Any]:
        """Retrieve a value from S3 (or local fallback).

        Always tries local disk first, even when S3 is configured, so that
        development / CI environments work without a real S3 endpoint.
        """
        # Always try local fallback first
        filepath = os.path.join(self._local_dir, f"{memory_id}.json")
        try:
            with open(filepath) as f:
                return json.load(f)
        except FileNotFoundError:
            pass

        if self.enabled:
            if aioboto3 is None:
                logger.warning("aioboto3 not installed. Install with: pip install memory-bridge[ml] or pip install aioboto3")
            else:
                try:
                    session = aioboto3.Session()
                    async with session.client(
                        "s3",
                        endpoint_url=self._endpoint or None,
                        aws_access_key_id=self._access_key,
                        aws_secret_access_key=self._secret_key,
                        region_name=self._region,
                    ) as s3:
                        response = await s3.get_object(Bucket=self._bucket, Key=s3_key)
                        data = await response["Body"].read()
                        return json.loads(data)
                except Exception as e:
                    logger.warning("S3 retrieve failed for %s: %s", s3_key, e)

        logger.warning("S3 retrieve: %s not found in %s", s3_key, self._local_dir)
        return None

    async def delete(self, memory_id: str, s3_key: str) -> bool:
        """Delete a value from S3 (or local fallback)."""
        # Always clean up local fallback
        filepath = os.path.join(self._local_dir, f"{memory_id}.json")
        try:
            os.remove(filepath)
            logger.info("S3 delete: removed local file %s", filepath)
        except FileNotFoundError:
            pass

        if self.enabled:
            if aioboto3 is None:
                logger.warning("aioboto3 not installed. Install with: pip install memory-bridge[ml] or pip install aioboto3")
            else:
                try:
                    session = aioboto3.Session()
                    async with session.client(
                        "s3",
                        endpoint_url=self._endpoint or None,
                        aws_access_key_id=self._access_key,
                        aws_secret_access_key=self._secret_key,
                        region_name=self._region,
                    ) as s3:
                        await s3.delete_object(Bucket=self._bucket, Key=s3_key)
                    logger.info("S3 delete: removed %s from %s", s3_key, self._bucket)
                except Exception as e:
                    logger.warning("S3 delete failed for %s: %s", s3_key, e)

        return True
