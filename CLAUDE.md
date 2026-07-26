# pitch2onepager — project context

Python 3.11+ CLI that converts an investor pitch deck (`.pdf` / `.pptx`) into a
one-page **Customer Journey Market Narrative** PDF.

## Pipeline

`extractor.py` (pypdf / python-pptx) → `analyzer.py` (Claude, JSON → Pydantic) →
`builder.py` (reportlab) → one Letter page.

Two front ends drive that pipeline: `cli.py` (Click) and `web.py` (FastAPI,
deployed via `railpack.json`). Both are thin — analysis logic stays in the
pipeline modules.

## Conventions

- **Model:** `claude-sonnet-4-6`, `max_tokens=4096`, one call per deck.
  Overridable via `--model` or `PITCH2ONEPAGER_MODEL`. Never hardcode API keys —
  `ANTHROPIC_API_KEY` comes from the environment or `.env`.
- **Errors:** all defined in `utils.py`. `FileError`/`BuildError` → exit 1,
  `ExtractionError`/`AnalysisError` → exit 2, `APIError` → exit 3. The CLI is the
  only place that calls `sys.exit`. `web.py` maps the same errors to HTTP:
  `FileError` → 400, `Extraction`/`AnalysisError` → 422, `APIError` → 503,
  `BuildError` → 500.
- **Layout:** all geometry, colour, and typography constants live in
  `templates/onepager_layout.py` — never hardcode a coordinate or hex value in
  `builder.py`. Positions are measured from the *page top*; `L.y()` converts to
  reportlab's bottom-left origin.
- **One page is a hard invariant.** Each band has a fixed height and text is
  clamped with `L.fit_lines(...)`. If you add content, take the space from an
  existing band's budget — do not let a section grow with its input.
- **Fonts:** Helvetica / Helvetica-Bold / Helvetica-Oblique only (built into
  reportlab). Keep drawn text within Latin-1 — `·`, `×`, `–`, `—`, `…`, `“ ”` are
  safe; `✗`, `⏱`, and emoji are not.
- **Tests:** mock `anthropic.Anthropic`; the suite must never make a network call
  or require a key. Fixture decks are generated in `tests/conftest.py`.

## Palette

Navy `#1B2A4A` · Orange `#E85D26` · Light blue `#C8D6E8` · Background `#F7F9FC`
