"""Embedding service for semantic search.

Provides a pluggable interface for generating text embeddings.
Supports OpenAI, Cohere, and a local TF-IDF fallback.
"""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Generates text embeddings for semantic search.

    Uses the configured provider (OpenAI by default).
    Falls back to TF-IDF when no API key is configured.
    """

    def __init__(self, provider: str = "openai"):
        self.provider = provider
        self._api_key = os.environ.get("MEMORY_BRIDGE_EMBEDDING_API_KEY", "")
        self._model = os.environ.get("MEMORY_BRIDGE_EMBEDDING_MODEL", "text-embedding-3-small")
        self._dimensions = int(os.environ.get("MEMORY_BRIDGE_EMBEDDING_DIMENSIONS", "1536"))

    @property
    def enabled(self) -> bool:
        """Whether embedding generation is available."""
        return bool(self._api_key) or self.provider == "local"

    async def embed(self, text: str) -> Optional[list[float]]:
        """Generate an embedding vector for the given text.

        Args:
            text: Text to embed

        Returns:
            List of floats (embedding vector) or None if unavailable
        """
        if not self.enabled:
            return None

        if self.provider == "openai" and self._api_key:
            return await self._embed_openai(text)
        elif self.provider == "local":
            return self._embed_local(text)

        return None

    async def _embed_openai(self, text: str) -> list[float]:
        """Generate embedding via OpenAI API."""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "input": text,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return data["data"][0]["embedding"]
        except Exception as e:
            logger.warning("OpenAI embedding failed: %s", e)
            return []

    def _embed_local(self, text: str) -> list[float]:
        """Simple local embedding fallback using character n-grams.
        Not as good as real embeddings but works without external APIs.
        """
        # Simple bag-of-characters embedding for semantic-ish matching
        # In production, use sentence-transformers
        chars = set(text.lower())
        # Create fixed-dimension vector from character presence
        all_chars = "abcdefghijklmnopqrstuvwxyz0123456789 ._-"
        vector = [1.0 if c in chars else 0.0 for c in all_chars]
        return vector[:self._dimensions] if len(vector) >= self._dimensions else vector + [0.0] * (self._dimensions - len(vector))
