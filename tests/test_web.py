"""HTTP layer tests. The Anthropic call is always stubbed — never a real request."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pitch2onepager import web
from pitch2onepager.utils import AnalysisError, APIError


@pytest.fixture
def client() -> TestClient:
    return TestClient(web.app)


@pytest.fixture
def stub_analyze(monkeypatch, analysis):
    """Replace the LLM call with the canned analysis fixture."""
    calls: list[object] = []

    def _fake(deck, **kwargs):  # noqa: ANN001, ANN003
        calls.append(deck)
        return analysis

    monkeypatch.setattr(web, "analyze_deck", _fake)
    return calls


def _upload(path: Path) -> dict:
    media = (
        "application/pdf"
        if path.suffix == ".pdf"
        else "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    return {"file": (path.name, path.read_bytes(), media)}


# --------------------------------------------------------------------------- #
# Health and index
# --------------------------------------------------------------------------- #


def test_healthz_reports_ok(client: TestClient) -> None:
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert "version" in body


def test_index_serves_the_upload_form(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 200
    assert 'type="file"' in res.text


def test_index_hides_password_field_when_unset(client: TestClient, monkeypatch) -> None:
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    assert 'name="password"' not in client.get("/").text


def test_index_shows_password_field_when_set(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("APP_PASSWORD", "hunter2")
    assert 'name="password"' in client.get("/").text


# --------------------------------------------------------------------------- #
# Generate
# --------------------------------------------------------------------------- #


def test_generate_returns_a_pdf(client: TestClient, sample_pdf: Path, stub_analyze) -> None:
    res = client.post("/generate", files=_upload(sample_pdf))

    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF")
    assert "Meridian_Health_onepager.pdf" in res.headers["content-disposition"]
    assert len(stub_analyze) == 1


def test_generate_accepts_pptx(client: TestClient, sample_pptx: Path, stub_analyze) -> None:
    res = client.post("/generate", files=_upload(sample_pptx))
    assert res.status_code == 200
    assert res.content.startswith(b"%PDF")


def test_generate_rejects_unsupported_extension(client: TestClient) -> None:
    res = client.post("/generate", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert res.status_code == 400
    assert ".pdf" in res.json()["error"]


def test_generate_rejects_empty_upload(client: TestClient) -> None:
    res = client.post("/generate", files={"file": ("deck.pdf", b"", "application/pdf")})
    assert res.status_code == 400


def test_generate_rejects_oversized_upload(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(web, "MAX_UPLOAD_BYTES", 10)
    res = client.post("/generate", files={"file": ("deck.pdf", b"x" * 64, "application/pdf")})
    assert res.status_code == 413


def test_image_only_deck_is_a_422(client: TestClient, image_only_pdf: Path) -> None:
    res = client.post("/generate", files=_upload(image_only_pdf))
    assert res.status_code == 422
    assert "image-only" in res.json()["error"]


def test_missing_api_key_is_a_503(client: TestClient, sample_pdf: Path, monkeypatch) -> None:
    def _boom(deck, **kwargs):  # noqa: ANN001, ANN003
        raise APIError("ANTHROPIC_API_KEY is not set.")

    monkeypatch.setattr(web, "analyze_deck", _boom)
    res = client.post("/generate", files=_upload(sample_pdf))
    assert res.status_code == 503


def test_unparseable_model_output_is_a_422(
    client: TestClient, sample_pdf: Path, monkeypatch
) -> None:
    def _boom(deck, **kwargs):  # noqa: ANN001, ANN003
        raise AnalysisError("Response was not valid JSON")

    monkeypatch.setattr(web, "analyze_deck", _boom)
    assert client.post("/generate", files=_upload(sample_pdf)).status_code == 422


# --------------------------------------------------------------------------- #
# Password gate
# --------------------------------------------------------------------------- #


def test_password_required_when_configured(
    client: TestClient, sample_pdf: Path, stub_analyze, monkeypatch
) -> None:
    monkeypatch.setenv("APP_PASSWORD", "hunter2")
    res = client.post("/generate", files=_upload(sample_pdf))
    assert res.status_code == 401
    assert not stub_analyze


def test_correct_password_is_accepted(
    client: TestClient, sample_pdf: Path, stub_analyze, monkeypatch
) -> None:
    monkeypatch.setenv("APP_PASSWORD", "hunter2")
    res = client.post("/generate", files=_upload(sample_pdf), data={"password": "hunter2"})
    assert res.status_code == 200
    assert res.content.startswith(b"%PDF")
