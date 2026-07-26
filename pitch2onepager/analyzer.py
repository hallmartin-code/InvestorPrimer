"""LLM extraction: turn raw deck text into a structured customer-journey narrative."""

from __future__ import annotations

import json
import os
import random
import re
import time

import anthropic
from dotenv import load_dotenv
from pydantic import ValidationError

from .models import CustomerJourneyAnalysis, DeckContent
from .utils import MIN_DECK_CHARS, AnalysisError, APIError

DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096

# Deck text sent to the model. Comfortably inside the context window while
# keeping cost predictable; decks longer than this are rare.
MAX_DECK_CHARS = 120_000

PROMPT_TEMPLATE = """\
You are an expert venture capital analyst specializing in market sizing and customer journey mapping.

Below is the full text extracted from an investor pitch deck. Your task is to synthesize this content into a structured Customer Journey Market Narrative that frames the company's opportunity through the lens of the target customer's lived experience.

PITCH DECK CONTENT:
{full_text}

Extract and return a JSON object that EXACTLY matches this schema:
{schema}

RULES:
- Base every field on evidence from the deck. If the deck does not address a field, make a reasonable, analytically grounded inference and mark it with "(inferred)" at the start of the value.
- customer_quotes should contain only language that appears verbatim or near-verbatim in the deck. If none exist, return an empty list.
- Be concrete and specific — avoid vague language like "significant impact". Prefer measurable claims.
- investment_thesis.market_opportunity_statement should read as a single, compelling paragraph suitable for an investor memo.
- comparable_industries should name real markets with similar supply/demand dynamics (e.g., "enterprise HR tech before Workday", "healthcare data interoperability pre-2015").
- Return ONLY valid JSON. No markdown, no commentary, no code fences.
"""

CORRECTIVE_PROMPT = """\
Your previous response could not be parsed as valid JSON matching the required schema.

Parser error:
{error}

Your previous response was:
{previous}

Return ONLY the corrected JSON object. It must match this schema exactly:
{schema}

No markdown, no code fences, no commentary — the first character of your response must be `{{`.
"""

_CODE_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def analyze_deck(
    deck: DeckContent,
    *,
    client: anthropic.Anthropic | None = None,
    model: str | None = None,
) -> CustomerJourneyAnalysis:
    """Send the deck text to Claude and parse the structured narrative back.

    Args:
        deck: Parsed deck content.
        client: Optional pre-built Anthropic client (used by tests).
        model: Optional model override.

    Raises:
        AnalysisError: the deck has too little text, or the model returned
            unparseable output twice in a row.
        APIError: the API key is missing or the API failed after a retry.
    """
    _assert_deck_has_content(deck)

    client = client or build_client()
    model = model or os.environ.get("PITCH2ONEPAGER_MODEL") or DEFAULT_MODEL
    schema = json.dumps(CustomerJourneyAnalysis.model_json_schema(), indent=2)

    prompt = PROMPT_TEMPLATE.format(
        full_text=deck.full_text[:MAX_DECK_CHARS],
        schema=schema,
    )
    raw = _call_model(client, model, prompt)

    try:
        return _parse_analysis(raw)
    except AnalysisError as first_error:
        corrective = CORRECTIVE_PROMPT.format(
            error=str(first_error),
            previous=raw[:4000],
            schema=schema,
        )
        retry_raw = _call_model(client, model, corrective)
        try:
            return _parse_analysis(retry_raw)
        except AnalysisError as second_error:
            raise AnalysisError(
                "The model did not return valid JSON after a corrective retry. "
                f"Last parser error: {second_error}"
            ) from second_error


def build_client() -> anthropic.Anthropic:
    """Construct an Anthropic client, loading ``.env`` if present."""
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise APIError(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key, "
            "or export ANTHROPIC_API_KEY in your shell."
        )
    return anthropic.Anthropic()


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _assert_deck_has_content(deck: DeckContent) -> None:
    text = (deck.full_text or "").strip()
    if len(text) < MIN_DECK_CHARS:
        raise AnalysisError(
            f"Only {len(text)} characters of text were extracted from "
            f"'{deck.source_file}' — not enough to analyse. The deck is most likely "
            "image-only (slides exported as pictures). Re-export it as a text-based "
            "PDF or PPTX, or run it through OCR first."
        )


def _call_model(client: anthropic.Anthropic, model: str, prompt: str) -> str:
    """One model call, with a single exponential-backoff retry on transient errors."""
    transient = (
        anthropic.RateLimitError,
        anthropic.APITimeoutError,
        anthropic.APIConnectionError,
        anthropic.InternalServerError,
    )
    last_exc: Exception | None = None

    for attempt in range(2):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            return _response_text(response)
        except transient as exc:
            last_exc = exc
            if attempt == 0:
                time.sleep(2.0 + random.uniform(0, 1.0))
                continue
        except anthropic.AuthenticationError as exc:
            raise APIError(
                "Anthropic rejected the API key. Check ANTHROPIC_API_KEY in your .env file."
            ) from exc
        except anthropic.APIStatusError as exc:
            raise APIError(f"Anthropic API error ({exc.status_code}): {exc.message}") from exc

    raise APIError(
        f"Anthropic API did not respond after a retry: {last_exc}"
    ) from last_exc


def _response_text(response: object) -> str:
    """Concatenate the text blocks of a Messages API response."""
    content = getattr(response, "content", None)
    if not content:
        raise AnalysisError("The model returned an empty response.")
    parts = [
        block.text
        for block in content
        if getattr(block, "type", None) == "text" and getattr(block, "text", None)
    ]
    if not parts:
        raise AnalysisError("The model response contained no text blocks.")
    return "\n".join(parts)


def _strip_fences(text: str) -> str:
    """Remove markdown code fences the model may have added despite instructions."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = _CODE_FENCE_RE.sub("", cleaned).strip()
    # Fall back to the outermost JSON object if there is prose around it.
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    return cleaned


def _parse_analysis(raw: str) -> CustomerJourneyAnalysis:
    payload = _strip_fences(raw)
    try:
        return CustomerJourneyAnalysis.model_validate_json(payload)
    except ValidationError as exc:
        raise AnalysisError(f"Response did not match the schema: {exc.errors()[:3]}") from exc
    except ValueError as exc:  # invalid JSON
        raise AnalysisError(f"Response was not valid JSON: {exc}") from exc


__all__ = ["analyze_deck", "build_client", "AnalysisError", "DEFAULT_MODEL"]
