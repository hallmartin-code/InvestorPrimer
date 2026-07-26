"""End-to-end CLI tests with the Anthropic client mocked out."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from pitch2onepager import analyzer, cli
from pitch2onepager.utils import APIError


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def patched_client(monkeypatch, analysis_json, fake_client_factory):
    """Make ``analyzer.build_client`` return a canned-response fake."""
    client = fake_client_factory([analysis_json])
    monkeypatch.setattr(analyzer, "build_client", lambda: client)
    return client


class TestGenerate:
    def test_pdf_deck_produces_a_onepager(
        self, runner, patched_client, sample_pdf: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "out.pdf"
        result = runner.invoke(
            cli.main, ["generate", str(sample_pdf), "--output", str(out)]
        )
        assert result.exit_code == 0, result.output
        assert out.exists()
        assert "Extracted narrative" in result.output
        assert "Meridian Health" in result.output

    def test_pptx_deck_produces_a_onepager(
        self, runner, patched_client, sample_pptx: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "out.pdf"
        result = runner.invoke(
            cli.main, ["generate", str(sample_pptx), "--output", str(out)]
        )
        assert result.exit_code == 0, result.output
        assert out.exists()

    def test_output_defaults_to_company_slug(
        self, runner, patched_client, sample_pdf: Path, tmp_path: Path
    ) -> None:
        with runner.isolated_filesystem(temp_dir=tmp_path) as workdir:
            result = runner.invoke(cli.main, ["generate", str(sample_pdf)])
            assert result.exit_code == 0, result.output
            assert (Path(workdir) / "Meridian_Health_onepager.pdf").exists()

    def test_model_override_is_passed_through(
        self, runner, patched_client, sample_pdf: Path, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            cli.main,
            [
                "generate",
                str(sample_pdf),
                "--output",
                str(tmp_path / "o.pdf"),
                "--model",
                "claude-opus-4-8",
            ],
        )
        assert result.exit_code == 0, result.output
        assert patched_client.messages.calls[0]["model"] == "claude-opus-4-8"


class TestExitCodes:
    def test_missing_file_exits_1(self, runner, tmp_path: Path) -> None:
        result = runner.invoke(cli.main, ["generate", str(tmp_path / "nope.pdf")])
        assert result.exit_code == 1
        assert "File not found" in result.output

    def test_unsupported_extension_exits_1(self, runner, tmp_path: Path) -> None:
        bad = tmp_path / "deck.key"
        bad.write_text("nope")
        result = runner.invoke(cli.main, ["generate", str(bad)])
        assert result.exit_code == 1
        assert "Supported formats" in result.output

    def test_image_only_deck_exits_2(
        self, runner, patched_client, image_only_pdf: Path
    ) -> None:
        result = runner.invoke(cli.main, ["generate", str(image_only_pdf)])
        assert result.exit_code == 2
        assert "image-only" in result.output

    def test_missing_api_key_exits_3(self, runner, monkeypatch, sample_pdf: Path) -> None:
        def no_key() -> None:
            raise APIError("ANTHROPIC_API_KEY is not set. See .env.example")

        monkeypatch.setattr(analyzer, "build_client", no_key)
        result = runner.invoke(cli.main, ["generate", str(sample_pdf)])
        assert result.exit_code == 3
        assert ".env.example" in result.output

    def test_unwritable_output_exits_1(
        self, runner, patched_client, sample_pdf: Path, tmp_path: Path
    ) -> None:
        target = tmp_path / "adir.pdf"
        target.mkdir()
        result = runner.invoke(
            cli.main, ["generate", str(sample_pdf), "--output", str(target)]
        )
        assert result.exit_code == 1


class TestConsoleEncoding:
    """A cp1252 console must not crash on the tick glyph or model output."""

    def test_unicode_detection_falls_back_on_legacy_encoding(self, monkeypatch) -> None:
        class FakeStdout:
            encoding = "cp1252"

        monkeypatch.setattr(cli.sys, "stdout", FakeStdout())
        assert cli._supports_unicode() is False

    def test_unicode_detection_accepts_utf8(self, monkeypatch) -> None:
        class FakeStdout:
            encoding = "utf-8"

        monkeypatch.setattr(cli.sys, "stdout", FakeStdout())
        assert cli._supports_unicode() is True

    def test_markers_are_ascii_when_unicode_unsupported(self) -> None:
        # Whatever this machine reports, the chosen marker must be encodable.
        cli.TICK.encode("ascii") if not cli._UNICODE_OK else cli.TICK.encode("utf-8")

    def test_lenient_streams_are_applied(self, monkeypatch) -> None:
        seen: dict[str, str] = {}

        class FakeStream:
            def reconfigure(self, **kwargs: str) -> None:
                seen.update(kwargs)

        monkeypatch.setattr(cli.sys, "stdout", FakeStream())
        monkeypatch.setattr(cli.sys, "stderr", FakeStream())
        cli._make_output_lenient()
        assert seen == {"errors": "replace"}

    def test_summary_survives_unencodable_output(
        self, runner, monkeypatch, analysis_json, fake_client_factory,
        sample_pdf: Path, tmp_path: Path,
    ) -> None:
        import json

        payload = json.loads(analysis_json)
        payload["tagline"] = "Emoji 🚀 and CJK 中文 in the model's output"
        client = fake_client_factory([json.dumps(payload)])
        monkeypatch.setattr(analyzer, "build_client", lambda: client)
        result = runner.invoke(
            cli.main, ["generate", str(sample_pdf), "--output", str(tmp_path / "o.pdf")]
        )
        assert result.exit_code == 0, result.output


class TestCliSurface:
    def test_version_flag(self, runner) -> None:
        result = runner.invoke(cli.main, ["--version"])
        assert result.exit_code == 0
        assert "pitch2onepager" in result.output

    def test_help_lists_generate(self, runner) -> None:
        result = runner.invoke(cli.main, ["--help"])
        assert result.exit_code == 0
        assert "generate" in result.output
