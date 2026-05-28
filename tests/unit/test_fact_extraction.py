"""Tests for FactExtractionService."""

import json
import os
import sys

# Ensure the project is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_bridge.services.fact_extraction_service import (
    FactExtractionService,
)


def test_detect_provider_disabled_when_no_key():
    """When no key is set, provider should be 'disabled'."""
    # Clear any OpenAI key
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ.pop("MEMORY_BRIDGE_FACT_EXTRACTOR", None)

    service = FactExtractionService()
    assert service._provider == "disabled"
    assert service.provider_name == "disabled"


def test_detect_provider_openai_when_key_set():
    """When OPENAI_API_KEY is set, provider should be 'openai'."""
    os.environ["OPENAI_API_KEY"] = "sk-test123"
    os.environ.pop("MEMORY_BRIDGE_FACT_EXTRACTOR", None)

    service = FactExtractionService()
    assert service._provider == "openai"
    assert service.provider_name == "openai"

    # Cleanup
    os.environ.pop("OPENAI_API_KEY", None)


def test_detect_provider_explicit_env_var():
    """MEMORY_BRIDGE_FACT_EXTRACTOR env var should take priority."""
    os.environ["MEMORY_BRIDGE_FACT_EXTRACTOR"] = "disabled"
    os.environ["OPENAI_API_KEY"] = "sk-test123"

    service = FactExtractionService()
    assert service._provider == "disabled"

    os.environ["MEMORY_BRIDGE_FACT_EXTRACTOR"] = "openai"
    service2 = FactExtractionService()
    assert service2._provider == "openai"

    # Cleanup
    os.environ.pop("MEMORY_BRIDGE_FACT_EXTRACTOR", None)
    os.environ.pop("OPENAI_API_KEY", None)


def test_fallback_returns_single_fact():
    """Disabled provider returns the raw text as a single fact."""
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ["MEMORY_BRIDGE_FACT_EXTRACTOR"] = "disabled"

    import asyncio

    service = FactExtractionService()
    result = asyncio.run(
        service.extract_facts("We decided to push the deadline.")
    )

    assert len(result) == 1
    assert result[0]["fact"] == "We decided to push the deadline."
    assert result[0]["category"] == "other"
    assert result[0]["confidence"] == 1.0
    assert result[0]["entities"] == []

    os.environ.pop("MEMORY_BRIDGE_FACT_EXTRACTOR", None)


def test_parse_openai_response_direct_array():
    """Parse a direct JSON array response."""
    service = FactExtractionService()
    content = json.dumps([
        {
            "fact": "Project deadline moved to Friday",
            "category": "decision",
            "confidence": 0.95,
            "entities": ["Project Alpha"],
        },
        {
            "fact": "User prefers dark mode",
            "category": "preference",
            "confidence": 0.85,
            "entities": [],
        },
    ])
    result = service._parse_openai_response(content, 10)
    assert len(result) == 2
    assert result[0]["fact"] == "Project deadline moved to Friday"
    assert result[0]["category"] == "decision"
    assert result[0]["confidence"] == 0.95
    assert result[0]["entities"] == ["Project Alpha"]
    assert result[1]["fact"] == "User prefers dark mode"
    assert result[1]["category"] == "preference"


def test_parse_openai_response_wrapped():
    """Parse a wrapped JSON object response."""
    service = FactExtractionService()
    content = json.dumps({
        "facts": [
            {
                "fact": "Server migration scheduled for June",
                "category": "decision",
                "confidence": 0.8,
                "entities": ["server"],
            },
        ],
    })
    result = service._parse_openai_response(content, 10)
    assert len(result) == 1
    assert result[0]["fact"] == "Server migration scheduled for June"


def test_parse_openai_response_markdown_fence():
    """Parse a response with markdown code fences."""
    service = FactExtractionService()
    content = "```json\n[\n  {\n    \"fact\": \"API rate limit is 100 req/min\",\n    \"category\": \"fact\",\n    \"confidence\": 1.0,\n    \"entities\": [\"API\"]\n  }\n]\n```"
    result = service._parse_openai_response(content, 10)
    assert len(result) == 1
    assert result[0]["fact"] == "API rate limit is 100 req/min"


def test_parse_openai_response_invalid_fallback():
    """When parsing fails, fallback to returning text as a single fact."""
    service = FactExtractionService()
    result = service._parse_openai_response("not valid json at all", 10)
    assert len(result) == 1
    assert result[0]["fact"] == "not valid json at all"
    assert result[0]["category"] == "other"


def test_parse_openai_response_empty_facts():
    """When the JSON array is empty, fallback."""
    service = FactExtractionService()
    result = service._parse_openai_response("[]", 10)
    assert len(result) == 1  # fallback


def test_parse_openai_response_max_facts():
    """Only return up to max_facts items."""
    service = FactExtractionService()
    facts = [{"fact": "Fact {}".format(i), "category": "log", "confidence": 0.5, "entities": []} for i in range(20)]
    content = json.dumps(facts)
    result = service._parse_openai_response(content, 5)
    assert len(result) == 5


def test_extract_facts_empty_text():
    """Empty text returns an empty list."""
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ["MEMORY_BRIDGE_FACT_EXTRACTOR"] = "disabled"

    import asyncio
    service = FactExtractionService()
    result = asyncio.run(service.extract_facts(""))
    assert result == []

    result = asyncio.run(service.extract_facts("   "))
    assert result == []

    os.environ.pop("MEMORY_BRIDGE_FACT_EXTRACTOR", None)


def test_max_facts_clamping():
    """max_facts should be clamped between 1 and 25."""
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ["MEMORY_BRIDGE_FACT_EXTRACTOR"] = "disabled"

    import asyncio
    service = FactExtractionService()
    # Should clamp to 1
    result = asyncio.run(service.extract_facts("Hello world", max_facts=0))
    assert len(result) == 1

    # Should clamp to 25
    result = asyncio.run(service.extract_facts("Hello world", max_facts=100))
    assert len(result) == 1  # disabled only returns 1

    os.environ.pop("MEMORY_BRIDGE_FACT_EXTRACTOR", None)


if __name__ == "__main__":
    test_detect_provider_disabled_when_no_key()
    test_detect_provider_openai_when_key_set()
    test_detect_provider_explicit_env_var()
    test_fallback_returns_single_fact()
    test_parse_openai_response_direct_array()
    test_parse_openai_response_wrapped()
    test_parse_openai_response_markdown_fence()
    test_parse_openai_response_invalid_fallback()
    test_parse_openai_response_empty_facts()
    test_parse_openai_response_max_facts()
    test_extract_facts_empty_text()
    test_max_facts_clamping()
    print("All tests passed!")
