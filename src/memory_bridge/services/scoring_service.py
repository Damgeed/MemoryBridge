"""Memory scoring service — recency, relevance, and importance scoring.

Composite score = w1 * recency_score + w2 * relevance_score + w3 * importance_score
Default weights: w1=0.3, w2=0.5, w3=0.2
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from ..models import MemoryEntry
from .embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

# Recency thresholds in seconds
_ONE_HOUR = 3600
_ONE_DAY = 86400
_ONE_WEEK = 604800
_ONE_MONTH = 2592000
_THREE_MONTHS = 7776000

# Importance tag bonuses
_HIGH_IMPORTANCE_TAGS = {"decision", "config", "fact", "preference"}
_LOW_IMPORTANCE_TAGS = {"log", "debug", "temp"}

# Default weights
_DEFAULT_WEIGHTS = {"recency": 0.3, "relevance": 0.5, "importance": 0.2}


class MemoryScoringService:
    """
    Scores memories by recency, relevance, and importance for ranking.

    Composite score = w1 * recency_score + w2 * relevance_score + w3 * importance_score
    Default weights: w1=0.3, w2=0.5, w3=0.2
    """

    def __init__(self):
        self._embedding = EmbeddingService()  # for semantic relevance

    def score_memories(
        self,
        memories: list[MemoryEntry],
        query_context: str = "",
        weights: Optional[dict[str, float]] = None,
    ) -> list[dict]:
        """
        Score and rank a list of memories.

        Returns list of dicts:
        {
            "memory": MemoryEntry,
            "score": 0.85,           # Composite score 0-1
            "recency_score": 0.9,    # How recent (0-1)
            "relevance_score": 0.7,  # Semantic relevance to query (0-1, 0 if no query)
            "importance_score": 0.8, # Inherent importance (0-1)
        }
        Sorted by score descending.
        """
        if not memories:
            return []

        resolved_weights = weights or dict(_DEFAULT_WEIGHTS)

        # Pre-compute query embedding if context provided
        query_embedding = None
        if query_context:
            try:
                # Use the sync embedding approach via keyword/async fallback
                # Since we might be in a sync context, check provider
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                    query_embedding = loop.run_until_complete(
                        self._embedding.embed(query_context)
                    )
                except RuntimeError:
                    # No running loop — create one
                    query_embedding = asyncio.run(
                        self._embedding.embed(query_context)
                    )
            except Exception as e:
                logger.warning("Failed to compute query embedding: %s", e)
                query_embedding = None

        results = []
        for memory in memories:
            recency = self._compute_recency(memory)
            importance = self._compute_importance(memory)
            relevance = self._compute_relevance(
                memory, query_context, query_embedding
            )
            composite = (
                resolved_weights.get("recency", 0.3) * recency
                + resolved_weights.get("relevance", 0.5) * relevance
                + resolved_weights.get("importance", 0.2) * importance
            )
            results.append({
                "memory": memory,
                "score": round(composite, 4),
                "recency_score": round(recency, 4),
                "relevance_score": round(relevance, 4),
                "importance_score": round(importance, 4),
            })

        # Sort by composite score descending
        results.sort(key=lambda r: -r["score"])
        return results

    def _compute_recency(self, memory: MemoryEntry) -> float:
        """Compute recency score (0-1) based on memory's created_at."""
        now = datetime.now(timezone.utc)
        age = (now - memory.created_at).total_seconds()

        if age < _ONE_HOUR:
            return 1.0
        if age < _ONE_DAY:
            return 0.9
        if age < _ONE_WEEK:
            return 0.7
        if age < _ONE_MONTH:
            return 0.5
        if age < _THREE_MONTHS:
            return 0.3
        # Linear decay from 0.3 to 0.1 over the next year
        # After 3 months, linearly decay: 0.3 at 3 months → 0.1 at 15 months+
        extra_age = age - _THREE_MONTHS
        decay_period = 365 * 86400  # 1 year
        decay = 0.2 * (extra_age / decay_period)
        score = 0.3 - decay
        return max(0.1, score)

    def _compute_importance(self, memory: MemoryEntry) -> float:
        """Compute importance score (0-1) based on tags and key patterns."""
        score = 0.5  # Base score

        key_lower = memory.key.lower()
        tags_lower = {t.lower() for t in memory.tags}

        # Tag bonuses
        for tag in tags_lower:
            if tag in _HIGH_IMPORTANCE_TAGS:
                score += 0.3
            elif tag in _LOW_IMPORTANCE_TAGS:
                score -= 0.2

        # Key pattern bonuses
        if key_lower.startswith("fact:") or "extracted" in key_lower:
            score += 0.2
        if "log" in key_lower or "debug" in key_lower:
            score -= 0.1

        # Clamp to [0, 1]
        return max(0.0, min(1.0, score))

    def _compute_relevance(
        self,
        memory: MemoryEntry,
        query_context: str,
        query_embedding: Optional[list[float]],
    ) -> float:
        """Compute relevance score (0-1) between memory and query.

        Uses semantic embedding comparison if available, otherwise
        falls back to simple keyword matching.
        """
        if not query_context:
            return 0.0

        # Build the memory text for comparison (key + first 500 chars of value)
        value_str = str(memory.value) if not isinstance(memory.value, str) else memory.value
        memory_text = f"{memory.key} {value_str[:500]}"

        # Try semantic embedding comparison
        if query_embedding:
            try:
                import asyncio
                mem_embedding = None
                try:
                    loop = asyncio.get_running_loop()
                    mem_embedding = loop.run_until_complete(
                        self._embedding.embed(memory_text)
                    )
                except RuntimeError:
                    mem_embedding = asyncio.run(
                        self._embedding.embed(memory_text)
                    )

                if mem_embedding and len(mem_embedding) > 0:
                    similarity = self._embedding.cosine_similarity(
                        query_embedding, mem_embedding
                    )
                    # Normalize from [-1, 1] to [0, 1]
                    return max(0.0, (similarity + 1.0) / 2.0)
            except Exception as e:
                logger.debug("Semantic relevance failed, falling back to keyword: %s", e)
                # Fall through to keyword matching

        # Keyword fallback
        query_words = query_context.lower().split()
        memory_text_lower = memory_text.lower()
        for word in query_words:
            if len(word) > 2 and word in memory_text_lower:
                return 0.5
        return 0.0

    async def score_memories_async(
        self,
        memories: list[MemoryEntry],
        query_context: str = "",
        weights: Optional[dict[str, float]] = None,
    ) -> list[dict]:
        """
        Async version of score_memories. Same logic but uses await for embedding calls.
        """
        if not memories:
            return []

        resolved_weights = weights or dict(_DEFAULT_WEIGHTS)

        # Pre-compute query embedding
        query_embedding = None
        if query_context:
            try:
                query_embedding = await self._embedding.embed(query_context)
            except Exception as e:
                logger.warning("Failed to compute query embedding: %s", e)

        results = []
        for memory in memories:
            recency = self._compute_recency(memory)
            importance = self._compute_importance(memory)
            relevance = await self._compute_relevance_async(
                memory, query_context, query_embedding
            )
            composite = (
                resolved_weights.get("recency", 0.3) * recency
                + resolved_weights.get("relevance", 0.5) * relevance
                + resolved_weights.get("importance", 0.2) * importance
            )
            results.append({
                "memory": memory,
                "score": round(composite, 4),
                "recency_score": round(recency, 4),
                "relevance_score": round(relevance, 4),
                "importance_score": round(importance, 4),
            })

        results.sort(key=lambda r: -r["score"])
        return results

    async def _compute_relevance_async(
        self,
        memory: MemoryEntry,
        query_context: str,
        query_embedding: Optional[list[float]],
    ) -> float:
        """Async variant of _compute_relevance."""
        if not query_context:
            return 0.0

        value_str = str(memory.value) if not isinstance(memory.value, str) else memory.value
        memory_text = f"{memory.key} {value_str[:500]}"

        if query_embedding:
            try:
                mem_embedding = await self._embedding.embed(memory_text)
                if mem_embedding and len(mem_embedding) > 0:
                    similarity = self._embedding.cosine_similarity(
                        query_embedding, mem_embedding
                    )
                    return max(0.0, (similarity + 1.0) / 2.0)
            except Exception as e:
                logger.debug("Semantic relevance failed, falling back to keyword: %s", e)

        # Keyword fallback
        query_words = query_context.lower().split()
        memory_text_lower = memory_text.lower()
        for word in query_words:
            if len(word) > 2 and word in memory_text_lower:
                return 0.5
        return 0.0
