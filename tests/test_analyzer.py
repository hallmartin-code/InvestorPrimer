from __future__ import annotations

import json

import anthropic
import pytest

from pitch2onepager import analyzer
from pitch2onepager.models import CustomerJourneyAnalysis, DeckContent, SlideContent
from pitch2onepager.utils import AnalysisError, APIError


def make_deck(text: str = "x" * 500) -> DeckContent:
    return DeckContent(
        source_file="deck.pdf",
        file_type="pdf",
        slide_count=1,
        slides=[SlideContent(slide_number=1, raw_text=text)],
        full_text=text,
    )


def _api_response(status: int = 500) -> anthropic.APIStatusError:
    class _Resp:
        status_code = status
        headers: dict = {}
        request = None

    return anthropic.APIStatusError(
        "boom", response=_Resp(), body={"error": {"message": "boom"}}
    )


class TestHappyPath:
    def test_returns_validated_analysis(self, analysis_json, fake_client_factory) -> None:
        client = fake_client_factory([analysis_json])
        result = analyzer.analyze_deck(make_deck(), client=client)
        assert isinstance(result, CustomerJourneyAnalysis)
        assert result.company_name == "Meridian Health"
        assert result.problem_awareness.urgency_level == "high"
        assert len(client.messages.calls) == 1

    def test_uses_expected_model_and_token_budget(
        self, analysis_json, fake_client_factory
    ) -> None:
        client = fake_client_factory([analysis_json])
        analyzer.analyze_deck(make_deck(), client=client)
        call = client.messages.calls[0]
        assert call["model"] == analyzer.DEFAULT_MODEL == "claude-sonnet-4-6"
        assert call["max_tokens"] == 4096

    def test_model_override_is_honoured(self, analysis_json, fake_client_factory) -> None:
        client = fake_client_factory([analysis_json])
        analyzer.analyze_deck(make_deck(), client=client, model="claude-opus-4-8")
        assert client.messages.calls[0]["model"] == "claude-opus-4-8"

    def test_prompt_embeds_deck_text_and_schema(
        self, analysis_json, fake_client_factory
    ) -> None:
        client = fake_client_factory([analysis_json])
        analyzer.analyze_deck(make_deck("READMISSION EVIDENCE " * 30), client=client)
        prompt = client.messages.calls[0]["messages"][0]["content"]
        assert "READMISSION EVIDENCE" in prompt
        assert "market_opportunity_statement" in prompt

    def test_long_decks_are_capped(self, analysis_json, fake_client_factory) -> None:
        client = fake_client_factory([analysis_json])
        deck_text = "HEAD_MARKER " + ("filler " * 40_000) + " TAIL_MARKER"
        assert len(deck_text) > analyzer.MAX_DECK_CHARS
        analyzer.analyze_deck(make_deck(deck_text), client=client)
        prompt = client.messages.calls[0]["messages"][0]["content"]
        assert "HEAD_MARKER" in prompt
        assert "TAIL_MARKER" not in prompt


class TestResponseCleaning:
    def test_strips_code_fences(self, analysis_json, fake_client_factory) -> None:
        client = fake_client_factory([f"```json\n{analysis_json}\n```"])
        assert analyzer.analyze_deck(make_deck(), client=client).company_name

    def test_strips_surrounding_prose(self, analysis_json, fake_client_factory) -> None:
        client = fake_client_factory([f"Here you go:\n{analysis_json}\nHope that helps!"])
        assert analyzer.analyze_deck(make_deck(), client=client).company_name


class TestRetryBehaviour:
    def test_retries_once_with_corrective_prompt(
        self, analysis_json, fake_client_factory
    ) -> None:
        client = fake_client_factory(["not json at all", analysis_json])
        result = analyzer.analyze_deck(make_deck(), client=client)
        assert result.company_name == "Meridian Health"
        assert len(client.messages.calls) == 2
        assert "could not be parsed" in client.messages.calls[1]["messages"][0]["content"]

    def test_gives_up_after_second_bad_response(self, fake_client_factory) -> None:
        client = fake_client_factory(["garbage", "still garbage"])
        with pytest.raises(AnalysisError, match="corrective retry"):
            analyzer.analyze_deck(make_deck(), client=client)
        assert len(client.messages.calls) == 2

    def test_schema_violation_triggers_retry(
        self, analysis_json, fake_client_factory
    ) -> None:
        broken = json.loads(analysis_json)
        broken["problem_awareness"]["urgency_level"] = "apocalyptic"
        client = fake_client_factory([json.dumps(broken), analysis_json])
        assert analyzer.analyze_deck(make_deck(), client=client).company_name
        assert len(client.messages.calls) == 2

    def test_transient_error_retries_then_succeeds(
        self, analysis_json, fake_client_factory, monkeypatch
    ) -> None:
        monkeypatch.setattr(analyzer.time, "sleep", lambda _: None)
        client = fake_client_factory([analysis_json])
        original = client.messages.create
        state = {"first": True}

        def flaky(**kwargs):  # noqa: ANN003, ANN202
            if state["first"]:
                state["first"] = False
                raise anthropic.APITimeoutError(request=None)
            return original(**kwargs)

        client.messages.create = flaky
        assert analyzer.analyze_deck(make_deck(), client=client).company_name

    def test_transient_error_twice_raises_api_error(
        self, fake_client_factory, monkeypatch
    ) -> None:
        monkeypatch.setattr(analyzer.time, "sleep", lambda _: None)
        client = fake_client_factory([])

        def always_timeout(**kwargs):  # noqa: ANN003, ANN202
            raise anthropic.APITimeoutError(request=None)

        client.messages.create = always_timeout
        with pytest.raises(APIError, match="did not respond after a retry"):
            analyzer.analyze_deck(make_deck(), client=client)


class TestErrorSurfaces:
    def test_thin_deck_raises_analysis_error(self, fake_client_factory) -> None:
        client = fake_client_factory([])
        with pytest.raises(AnalysisError, match="image-only"):
            analyzer.analyze_deck(make_deck("too short"), client=client)

    def test_empty_response_triggers_retry_then_fails(self, fake_client_factory) -> None:
        client = fake_client_factory(["", ""])
        with pytest.raises(AnalysisError):
            analyzer.analyze_deck(make_deck(), client=client)

    def test_api_status_error_becomes_api_error(self, fake_client_factory) -> None:
        client = fake_client_factory([])

        def boom(**kwargs):  # noqa: ANN003, ANN202
            raise _api_response(400)

        client.messages.create = boom
        with pytest.raises(APIError, match="Anthropic API error"):
            analyzer.analyze_deck(make_deck(), client=client)

    def test_missing_key_raises_api_error(self, monkeypatch) -> None:
        monkeypatch.setattr(analyzer, "load_dotenv", lambda *a, **k: None)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(APIError, match=".env.example"):
            analyzer.build_client()

    def test_build_client_with_key(self, monkeypatch) -> None:
        monkeypatch.setattr(analyzer, "load_dotenv", lambda *a, **k: None)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        assert isinstance(analyzer.build_client(), anthropic.Anthropic)
