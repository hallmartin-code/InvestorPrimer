"""Command-line entry point."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich import box
from rich.console import Console
from rich.table import Table

from . import __version__
from .analyzer import analyze_deck
from .builder import build_onepager
from .extractor import extract_deck
from .models import CustomerJourneyAnalysis, DeckContent
from .utils import (
    AnalysisError,
    APIError,
    BuildError,
    ExtractionError,
    FileError,
    slugify,
    truncate,
)

EXIT_OK = 0
EXIT_FILE = 1
EXIT_EXTRACTION = 2
EXIT_API = 3


def _make_output_lenient() -> None:
    """Never let an unencodable character in model output kill the run.

    The narrative text comes from an LLM and can contain any glyph; on a
    cp1252 console that would otherwise raise UnicodeEncodeError mid-print.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):  # pragma: no cover - detached/odd streams
            pass


def _supports_unicode() -> bool:
    """Whether stdout can encode the box-drawing and tick glyphs rich prefers.

    Legacy Windows consoles run cp1252, which cannot represent U+2713 or the
    default table borders — printing them there raises UnicodeEncodeError.
    """
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        "✓─".encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


_make_output_lenient()
_UNICODE_OK = _supports_unicode()
TICK = "✓" if _UNICODE_OK else "+"
TABLE_BOX = box.HEAVY_HEAD if _UNICODE_OK else box.ASCII

console = Console(stderr=False)
err_console = Console(stderr=True)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="pitch2onepager")
def main() -> None:
    """Turn an investor pitch deck into a one-page Customer Journey narrative."""


@main.command()
@click.argument("deck", type=click.Path(path_type=Path))
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output PDF path. Defaults to {company_name}_onepager.pdf",
)
@click.option(
    "--logo",
    type=click.Path(path_type=Path),
    default=None,
    help="Logo image to place in the header (defaults to the TEN Capital mark).",
)
@click.option("--model", default=None, help="Override the Claude model used for extraction.")
def generate(
    deck: Path, output: Path | None, logo: Path | None, model: str | None
) -> None:
    """Generate a one-pager PDF from DECK (.pdf or .pptx)."""
    # 1. Ingest ---------------------------------------------------------------
    try:
        with console.status("[bold cyan]Reading deck…", spinner="dots"):
            content = extract_deck(str(deck))
    except FileError as exc:
        _fail(str(exc), EXIT_FILE)
    except ExtractionError as exc:
        _fail(str(exc), EXIT_EXTRACTION)

    console.print(
        f"[green]{TICK}[/green] Parsed {content.slide_count} "
        f"{'page' if content.file_type == 'pdf' else 'slide'}(s) from "
        f"[bold]{Path(content.source_file).name}[/bold] "
        f"({content.extracted_slide_count} with extractable text)"
    )

    # 2. Analyse --------------------------------------------------------------
    try:
        with console.status("[bold cyan]Analysing with Claude…", spinner="dots"):
            analysis = analyze_deck(content, model=model)
    except APIError as exc:
        _fail(str(exc), EXIT_API)
    except AnalysisError as exc:
        _fail(str(exc), EXIT_EXTRACTION)

    console.print(f"[green]{TICK}[/green] Customer journey narrative extracted")

    # 3. Render ---------------------------------------------------------------
    out_path = output or Path(f"{slugify(analysis.company_name)}_onepager.pdf")
    try:
        with console.status("[bold cyan]Building PDF…", spinner="dots"):
            written = build_onepager(analysis, str(out_path), str(logo) if logo else None)
    except BuildError as exc:
        _fail(str(exc), EXIT_FILE)

    console.print(f"[green]{TICK}[/green] Wrote [bold]{written}[/bold]")
    console.print()
    _print_summary(analysis, content)
    sys.exit(EXIT_OK)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _fail(message: str, code: int) -> None:
    err_console.print(f"[bold red]Error:[/bold red] {message}")
    sys.exit(code)


def _print_summary(analysis: CustomerJourneyAnalysis, deck: DeckContent) -> None:
    table = Table(
        title="Extracted narrative",
        title_justify="left",
        show_lines=False,
        box=TABLE_BOX,
    )
    table.add_column("Field", style="bold cyan", no_wrap=True)
    table.add_column("Value", overflow="fold")

    p = analysis.problem_awareness
    d = analysis.discovery_odyssey
    s = analysis.solution_landscape
    g = analysis.gaps_that_matter
    t = analysis.investment_thesis

    rows = [
        ("Company", analysis.company_name),
        ("Tagline", truncate(analysis.tagline, 90)),
        ("Target customer", truncate(analysis.target_customer, 90)),
        ("Trigger moment", truncate(p.trigger_moment, 90)),
        ("Urgency", f"{p.urgency_level} (acts after {p.time_before_seeking_solution})"),
        ("Discovery", f"{d.vendor_count_estimate} / {d.avg_time_to_find_fit}"),
        ("Channels", ", ".join(d.channels_navigated[:4]) or "-"),
        ("Existing solutions", str(len(s.existing_solutions))),
        ("Unmet needs", str(len(g.unmet_needs))),
        ("Customer quotes", str(len(g.customer_quotes))),
        ("Why now", truncate(t.why_now, 90)),
        ("Market size", t.estimated_market_size or "not stated in deck"),
        ("Source", f"{Path(deck.source_file).name} ({deck.slide_count} slides)"),
    ]
    for label, value in rows:
        table.add_row(label, str(value))

    console.print(table)


if __name__ == "__main__":  # pragma: no cover
    main()
