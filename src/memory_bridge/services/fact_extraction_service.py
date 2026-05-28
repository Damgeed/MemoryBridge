"""Fact extraction service for Memory Bridge.

Extracts atomic, structured facts from raw text using a configurable
LLM provider (OpenAI or disabled fallback).
"""

import asyncio
import json
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a fact extraction system for a multi-agent memory store. "
    "Extract atomic, standalone facts from the following text.\n\n"
    "Rules:\n"
    "1. Each fact must be a complete, standalone statement "
    '(e.g. "Project Alpha deadline moved to Friday June 30th")\n'
    "2. Break compound statements into multiple facts\n"
    "3. Remove filler words, keep the essential meaning\n"
    "4. Categorize each fact as: preference, fact, decision, log, or other\n"
    "5. Extract key entities (people, projects, dates, systems)\n"
    "6. Rate confidence: 1.0 = explicitly stated, 0.7 = strongly implied, "
    "0.4 = weakly implied\n\n"
    "Respond with a JSON array of objects: "
    '[{"fact": "...", "category": "...", "confidence": 0.9, '
    '"entities": ["entity1", "entity2"]}]'
)


class FactExtractionService:
    """Extracts atomic facts from raw text using a configurable LLM provider.

    Provider detection order:
    1. MEMORY_BRIDGE_FACT_EXTRACTOR env var ('openai' | 'disabled')
    2. If OPENAI_API_KEY is set -> 'openai'
    3. If none of the above -> 'disabled' (returns raw text as a single fact)
    """

    def __init__(self):
        self._provider = self._detect_provider()
        self._http = httpx.Client(timeout=30.0)
        logger.info(
            "FactExtractionService initialized with provider: %s",
            self._provider,
        )

    def _detect_provider(self) -> str:
        """Check env vars in priority order."""
        # 1. Explicit env var
        explicit = os.environ.get(
            "MEMORY_BRIDGE_FACT_EXTRACTOR", ""
        ).strip().lower()
        if explicit:
            valid = {"openai", "disabled"}
            if explicit in valid:
                return explicit
            logger.warning(
                "Unknown MEMORY_BRIDGE_FACT_EXTRACTOR=%r, "
                "falling back to auto-detect",
                explicit,
            )

        # 2. Check if OPENAI_API_KEY is set
        if os.environ.get("OPENAI_API_KEY", "").strip():
            logger.info("Auto-detected fact extraction provider: openai")
            return "openai"

        # 3. Fallback to disabled
        logger.info(
            "No fact extraction provider configured, using disabled fallback"
        )
        return "disabled"

    async def extract_facts(
        self,
        text: str,
        source_key: str = "",
        max_facts: int = 10,
    ) -> list[dict]:
        """Extract atomic facts from raw text.

        Args:
            text: Raw text to extract facts from.
            source_key: Optional key for the source memory (for traceability).
            max_facts: Maximum number of facts to extract (default: 10, max: 25).

        Returns:
            List of dicts with keys: fact, category, confidence, entities.

            When disabled, returns
            [{"fact": text, "category": "other", "confidence": 1.0, "entities": []}].
        """
        if not text or not text.strip():
            return []

        # Clamp max_facts
        max_facts = min(max(max_facts, 1), 25)

        if self._provider == "disabled":
            return self._fallback(text)

        if self._provider == "openai":
            return await self._extract_openai(text, max_facts)

        # Unknown provider — fallback
        logger.warning(
            "Unknown provider %r, falling back to disabled", self._provider
        )
        return self._fallback(text)

    def _fallback(self, text: str) -> list[dict]:
        """Return raw text as a single fact when no LLM provider is configured."""
        return [
            {
                "fact": text.strip(),
                "category": "other",
                "confidence": 1.0,
                "entities": [],
            }
        ]

    async def _extract_openai(
        self, text: str, max_facts: int
    ) -> list[dict]:
        """Extract facts via OpenAI chat completion with exponential backoff."""
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            logger.warning("OPENAI_API_KEY not set, falling back to disabled")
            return self._fallback(text)

        # Limit input text to prevent token overflow
        truncated = text[:10000]

        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Extract up to {max_facts} facts from this text:\n\n"
                        f"{truncated}"
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "max_tokens": 2048,
        }

        last_exception = None
        for attempt in range(4):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )

                if resp.status_code == 429:
                    # Rate limited — exponential backoff
                    wait = 2 ** attempt
                    logger.warning(
                        "OpenAI rate limited (429), retrying in %ds "
                        "(attempt %d/4)",
                        wait,
                        attempt + 1,
                    )
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code == 401:
                    logger.error(
                        "OpenAI authentication failed (401) — bad API key"
                    )
                    return self._fallback(text)

                if resp.status_code >= 500:
                    wait = 2 ** attempt
                    logger.warning(
                        "OpenAI server error (%d), retrying in %ds "
                        "(attempt %d/4)",
                        resp.status_code,
                        wait,
                        attempt + 1,
                    )
                    await asyncio.sleep(wait)
                    continue

                resp.raise_for_status()
                data = resp.json()

                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                if not content:
                    logger.warning(
                        "OpenAI returned empty content, falling back"
                    )
                    return self._fallback(text)

                return self._parse_openai_response(content, max_facts)

            except httpx.TimeoutException as e:
                last_exception = e
                wait = 2 ** attempt
                logger.warning(
                    "OpenAI request timed out, retrying in %ds "
                    "(attempt %d/4)",
                    wait,
                    attempt + 1,
                )
                await asyncio.sleep(wait)

            except httpx.HTTPStatusError as e:
                last_exception = e
                logger.warning(
                    "OpenAI HTTP error: %s (attempt %d/4)",
                    e,
                    attempt + 1,
                )
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)

            except Exception as e:
                last_exception = e
                logger.warning(
                    "OpenAI request failed: %s (attempt %d/4)",
                    e,
                    attempt + 1,
                )
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)

        # All retries exhausted
        logger.error(
            "OpenAI extraction failed after 4 attempts: %s", last_exception
        )
        return self._fallback(text)

    def _parse_openai_response(
        self, content: str, max_facts: int
    ) -> list[dict]:
        """Parse the JSON response from OpenAI into fact dicts."""
        # Strip markdown code fences if present
        cleaned = content.strip()
        if cleaned.startswith("```"):
            # Remove opening fence (possibly with language hint)
            first_newline = cleaned.find("\n")
            if first_newline != -1:
                cleaned = cleaned[first_newline + 1 :]
            # Remove closing fence
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3].strip()
            elif "```" in cleaned:
                cleaned = cleaned[: cleaned.rindex("```")].strip()

        # The response_format is json_object so the content should be JSON
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.warning(
                "Failed to parse OpenAI response as JSON: %s — %s",
                e,
                content[:200],
            )
            return self._fallback(content)

        # Handle both direct array and { "facts": [...] } wrapper
        if isinstance(parsed, dict):
            for key in ("facts", "extractions", "results", "data"):
                if key in parsed and isinstance(parsed[key], list):
                    parsed = parsed[key]
                    break

        if not isinstance(parsed, list):
            logger.warning(
                "OpenAI response is not a list after unwrapping: %s",
                type(parsed),
            )
            return self._fallback(content)

        # Validate and normalize each fact
        validated = []
        valid_categories = {
            "preference", "fact", "decision", "log", "other",
        }
        for item in parsed[:max_facts]:
            if not isinstance(item, dict):
                continue
            fact_str = item.get("fact") or item.get("statement") or ""
            if not isinstance(fact_str, str) or not fact_str.strip():
                continue

            category = str(
                item.get("category", "other")
            ).strip().lower()
            if category not in valid_categories:
                category = "other"

            confidence_raw = item.get("confidence", 0.7)
            try:
                confidence = float(confidence_raw)
            except (ValueError, TypeError):
                confidence = 0.7
            confidence = max(0.0, min(1.0, confidence))

            entities_raw = item.get("entities", [])
            if not isinstance(entities_raw, list):
                entities_raw = []
            entities = [
                str(e) for e in entities_raw if isinstance(e, (str, int, float))
            ]

            validated.append(
                {
                    "fact": fact_str.strip(),
                    "category": category,
                    "confidence": confidence,
                    "entities": entities,
                }
            )

        if not validated:
            logger.warning(
                "No valid facts extracted from OpenAI response"
            )
            return self._fallback(content)

        return validated

    @property
    def provider_name(self) -> str:
        """Human-readable provider name."""
        names = {
            "openai": "openai",
            "disabled": "disabled",
        }
        return names.get(self._provider, self._provider)
