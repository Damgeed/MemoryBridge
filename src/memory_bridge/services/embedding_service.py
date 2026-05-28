"""Embedding service for semantic search.

Provides a pluggable interface for generating text embeddings.
Supports sentence-transformers (local), OpenAI API, and a keyword fallback.
"""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Abstracts embedding generation. Supports multiple providers.

    Provider auto-detection order:
    1. MEMORY_BRIDGE_EMBEDDING_PROVIDER env var (explicit)
    2. sentence_transformers (if package is importable)
    3. OPENAI_API_KEY (if set)
    4. 'keyword' (basic fallback — no real embeddings)
    """

    def __init__(self):
        self._model = None  # lazy-loaded for sentence-transformers
        self._provider = self._detect_provider()
        self._dimensions = int(os.environ.get(
            "MEMORY_BRIDGE_EMBEDDING_DIMENSIONS", "384"
        ))
        logger.info(
            "EmbeddingService initialized with provider: %s (dims=%d)",
            self._provider, self._dimensions,
        )

    def _detect_provider(self) -> str:
        """Detect which embedding provider to use."""
        # 1. Explicit env var
        explicit = os.environ.get("MEMORY_BRIDGE_EMBEDDING_PROVIDER", "").strip()
        if explicit:
            valid = {"sentence_transformers", "openai", "keyword"}
            if explicit in valid:
                return explicit
            logger.warning(
                "Unknown MEMORY_BRIDGE_EMBEDDING_PROVIDER=%r, falling back to auto-detect",
                explicit,
            )

        # 2. sentence_transformers
        try:
            import sentence_transformers  # noqa: F401
            logger.info("Auto-detected provider: sentence_transformers")
            return "sentence_transformers"
        except ImportError:
            pass

        # 3. OpenAI
        if os.environ.get("OPENAI_API_KEY", "").strip():
            logger.info("Auto-detected provider: openai")
            return "openai"

        # 4. Keyword fallback
        logger.info("No embedding model available, using keyword fallback")
        return "keyword"

    async def embed(self, text: str) -> Optional[list[float]]:
        """Convert text to an embedding vector.

        Returns:
            List of floats (embedding vector) if a real provider is available.
            Returns None if only keyword search is available.
        """
        if self._provider == "sentence_transformers":
            return await self._embed_sentence_transformers(text)
        elif self._provider == "openai":
            return await self._embed_openai(text)
        else:
            # keyword provider — no real embeddings
            return None

    async def _embed_sentence_transformers(self, text: str) -> list[float]:
        """Generate embedding via sentence-transformers (local model)."""
        try:
            import numpy as np
            import sentence_transformers  # noqa: F811

            if self._model is None:
                model_name = os.environ.get(
                    "MEMORY_BRIDGE_EMBEDDING_MODEL",
                    "all-MiniLM-L6-v2",
                )
                logger.info("Loading sentence-transformers model: %s", model_name)
                self._model = sentence_transformers.SentenceTransformer(model_name)

            embedding = self._model.encode(text, convert_to_numpy=True)
            # Normalise to unit vector for cosine similarity
            norm = np.linalg.norm(embedding)
            if norm > 0:
                embedding = embedding / norm
            return embedding.tolist()
        except Exception as e:
            logger.warning("sentence-transformers embedding failed: %s", e)
            return []

    async def _embed_openai(self, text: str) -> list[float]:
        """Generate embedding via OpenAI API."""
        import httpx

        api_key = os.environ.get("OPENAI_API_KEY", "")
        model = os.environ.get(
            "MEMORY_BRIDGE_EMBEDDING_MODEL", "text-embedding-3-small"
        )
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "input": text,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return data["data"][0]["embedding"]
        except Exception as e:
            logger.warning("OpenAI embedding failed: %s", e)
            return []

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors.

        Returns a float between -1 and 1. Returns 0.0 if either vector
        is zero-length or dimensions don't match.
        """
        if not a or not b or len(a) != len(b):
            return 0.0
        try:
            import numpy as np
            a_arr = np.array(a, dtype=np.float64)
            b_arr = np.array(b, dtype=np.float64)
            dot = np.dot(a_arr, b_arr)
            norm_a = np.linalg.norm(a_arr)
            norm_b = np.linalg.norm(b_arr)
            if norm_a == 0 or norm_b == 0:
                return 0.0
            return float(dot / (norm_a * norm_b))
        except ImportError:
            # Pure Python fallback (no numpy)
            dot = sum(x * y for x, y in zip(a, b))
            norm_a = sum(x * x for x in a) ** 0.5
            norm_b = sum(x * x for x in b) ** 0.5
            if norm_a == 0 or norm_b == 0:
                return 0.0
            return dot / (norm_a * norm_b)

    @property
    def provider_name(self) -> str:
        """Human-readable provider name."""
        names = {
            "sentence_transformers": "sentence-transformers",
            "openai": "openai",
            "keyword": "keyword",
        }
        return names.get(self._provider, self._provider)
