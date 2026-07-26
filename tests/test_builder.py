from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pypdf import PdfReader

from pitch2onepager.builder import build_onepager
from pitch2onepager.models import CustomerJourneyAnalysis
from pitch2onepager.utils import BuildError
from templates import onepager_layout as L


class TestBuildOutput:
    def test_writes_a_readable_pdf(self, analysis, tmp_path: Path) -> None:
        out = tmp_path / "onepager.pdf"
        written = build_onepager(analysis, str(out))
        assert Path(written) == out
        assert out.exists()
        assert out.stat().st_size > 10_000

    def test_fits_exactly_one_letter_page(self, analysis, tmp_path: Path) -> None:
        out = tmp_path / "onepager.pdf"
        build_onepager(analysis, str(out))
        reader = PdfReader(str(out))
        assert len(reader.pages) == 1
        box = reader.pages[0].mediabox
        assert round(float(box.width)) == 612
        assert round(float(box.height)) == 792

    def test_all_six_sections_are_rendered(self, analysis, tmp_path: Path) -> None:
        out = tmp_path / "onepager.pdf"
        build_onepager(analysis, str(out))
        text = PdfReader(str(out)).pages[0].extract_text()
        for label in (
            "PROBLEM AWARENESS",
            "DISCOVERY ODYSSEY",
            "SOLUTION LANDSCAPE",
            "A DAY IN THE LIFE",
            "THE GAPS THAT MATTER",
            "THE INVESTMENT THESIS",
        ):
            assert label in text, f"missing section: {label}"

    def test_content_comes_from_the_analysis(self, analysis, tmp_path: Path) -> None:
        out = tmp_path / "onepager.pdf"
        build_onepager(analysis, str(out))
        text = PdfReader(str(out)).pages[0].extract_text()
        assert "Meridian Health" in text
        assert "HIGH URGENCY" in text
        assert "5–10 vendors evaluated" in text
        assert "OPERATIONAL" in text and "FINANCIAL" in text
        assert "WHY NOW" in text
        assert "tencapital.group" in text

    def test_creates_missing_parent_directories(self, analysis, tmp_path: Path) -> None:
        out = tmp_path / "nested" / "deeper" / "onepager.pdf"
        build_onepager(analysis, str(out))
        assert out.exists()

    def test_custom_logo_is_accepted(self, analysis, tmp_path: Path) -> None:
        from PIL import Image

        logo = tmp_path / "logo.png"
        Image.new("RGBA", (200, 60), (232, 93, 38, 255)).save(logo)
        out = tmp_path / "onepager.pdf"
        build_onepager(analysis, str(out), logo_path=str(logo))
        assert len(PdfReader(str(out)).pages) == 1
        # The custom mark replaces the default TEN Capital wordmark.
        assert "TEN CAPITAL" not in PdfReader(str(out)).pages[0].extract_text()

    def test_missing_logo_falls_back_to_text_mark(self, analysis, tmp_path: Path) -> None:
        out = tmp_path / "onepager.pdf"
        build_onepager(analysis, str(out), logo_path=str(tmp_path / "absent.png"))
        assert "TEN CAPITAL" in PdfReader(str(out)).pages[0].extract_text()


class TestOverflowResistance:
    def _inflate(self, analysis: CustomerJourneyAnalysis) -> CustomerJourneyAnalysis:
        blob = ("An extremely verbose sentence about the customer journey. " * 40).strip()
        data = analysis.model_dump()
        data["tagline"] = blob
        data["company_name"] = "A Company With An Absurdly Long Registered Legal Name LLC"
        data["problem_awareness"]["trigger_moment"] = blob
        data["problem_awareness"]["pain_description"] = blob
        data["discovery_odyssey"]["friction_points"] = [blob] * 8
        data["discovery_odyssey"]["channels_navigated"] = [f"Channel {i} " * 6 for i in range(12)]
        data["solution_landscape"]["existing_solutions"] = [blob] * 8
        data["solution_landscape"]["key_shortfalls"] = [blob] * 8
        data["solution_landscape"]["workarounds_customers_use"] = [blob] * 8
        for key in (
            "operational_burden",
            "emotional_burden",
            "financial_burden",
            "organizational_spillover",
        ):
            data["day_in_the_life"][key] = blob
        data["gaps_that_matter"]["unmet_needs"] = [blob] * 10
        data["gaps_that_matter"]["customer_quotes"] = [blob] * 5
        data["investment_thesis"]["market_opportunity_statement"] = blob
        data["investment_thesis"]["why_now"] = blob
        data["investment_thesis"]["comparable_industries"] = [blob] * 6
        data["investment_thesis"]["estimated_market_size"] = blob
        return CustomerJourneyAnalysis.model_validate(data)

    def test_verbose_analysis_still_fits_one_page(self, analysis, tmp_path: Path) -> None:
        out = tmp_path / "verbose.pdf"
        build_onepager(self._inflate(analysis), str(out))
        assert len(PdfReader(str(out)).pages) == 1

    @pytest.mark.parametrize("inflate", [False, True])
    def test_planned_bands_never_reach_the_footer(self, analysis, inflate: bool) -> None:
        from pitch2onepager import builder

        subject = self._inflate(analysis) if inflate else analysis
        bands = [
            builder._plan_two_columns(subject),
            builder._plan_solution_landscape(subject),
            builder._plan_day_in_the_life(subject),
            builder._plan_gaps_that_matter(subject),
        ]
        assert len(bands) == L.BAND_COUNT
        total = sum(b.height for b in bands)
        gap = L.clamp(
            (L.CONTENT_BOTTOM - L.CONTENT_TOP - total) / len(bands),
            L.GAP_MIN,
            L.GAP_MAX,
        )
        bottom = L.CONTENT_TOP + total + gap * (len(bands) - 1)
        assert bottom <= L.PAGE_H - L.FOOTER_H
        assert all(b.height > 0 for b in bands)

    def test_minimal_analysis_still_renders(self, analysis, tmp_path: Path) -> None:
        data = analysis.model_dump()
        for section in ("discovery_odyssey", "solution_landscape", "gaps_that_matter"):
            for key, value in data[section].items():
                if isinstance(value, list):
                    data[section][key] = []
        data["investment_thesis"]["comparable_industries"] = []
        data["investment_thesis"]["estimated_market_size"] = None
        out = tmp_path / "minimal.pdf"
        build_onepager(CustomerJourneyAnalysis.model_validate(data), str(out))
        assert len(PdfReader(str(out)).pages) == 1


class TestWriteFailures:
    @pytest.mark.skipif(sys.platform == "win32", reason="chmod is a no-op on Windows")
    def test_unwritable_directory_raises_build_error(self, analysis, tmp_path: Path) -> None:
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        try:
            with pytest.raises(BuildError, match="Cannot write"):
                build_onepager(analysis, str(locked / "out.pdf"))
        finally:
            locked.chmod(0o700)

    def test_directory_as_output_path_raises_build_error(
        self, analysis, tmp_path: Path
    ) -> None:
        target = tmp_path / "iam_a_dir.pdf"
        target.mkdir()
        with pytest.raises(BuildError):
            build_onepager(analysis, str(target))


class TestLayoutHelpers:
    def test_worst_case_bands_fit_between_header_and_footer(self) -> None:
        # Every band at its cap, plus the minimum gaps, must still fit.
        min_gaps = (L.BAND_COUNT - 1) * L.GAP_MIN
        assert L.CONTENT_TOP + L.MAX_BAND_TOTAL + min_gaps <= L.CONTENT_BOTTOM
        assert L.CONTENT_TOP >= L.HEADER_H
        assert L.CONTENT_BOTTOM <= L.PAGE_H - L.FOOTER_H

    def test_columns_and_cells_span_the_content_width(self) -> None:
        assert L.COL_W * 2 + L.COL_GAP == pytest.approx(L.CONTENT_W)
        assert L.CELL_W * 4 + L.CELL_GAP * 3 == pytest.approx(L.CONTENT_W)

    def test_clamp_and_lines_that_fit(self) -> None:
        assert L.clamp(5, 10, 20) == 10
        assert L.clamp(25, 10, 20) == 20
        assert L.clamp(15, 10, 20) == 15
        assert L.lines_that_fit(30, 10) == 3
        assert L.lines_that_fit(-5, 10) == 0
        assert L.lines_that_fit(30, 0) == 0

    def test_y_inverts_the_axis(self) -> None:
        assert L.y(0) == L.PAGE_H
        assert L.y(L.PAGE_H) == 0

    def test_wrap_lines_respects_width(self) -> None:
        from reportlab.pdfbase.pdfmetrics import stringWidth

        lines = L.wrap_lines("word " * 60, L.FONT, 10, 200)
        assert len(lines) > 1
        assert all(stringWidth(line, L.FONT, 10) <= 200 for line in lines)

    def test_wrap_lines_hard_breaks_long_words(self) -> None:
        lines = L.wrap_lines("x" * 400, L.FONT, 10, 100)
        assert len(lines) > 1

    def test_fit_lines_clamps_and_ellipsises(self) -> None:
        lines = L.fit_lines("word " * 200, L.FONT, 10, 200, 3)
        assert len(lines) == 3
        assert lines[-1].endswith("…")

    def test_fit_lines_handles_edge_cases(self) -> None:
        assert L.fit_lines("anything", L.FONT, 10, 200, 0) == []
        assert L.fit_lines("", L.FONT, 10, 200, 3) == []
