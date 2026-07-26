# pitch2onepager

Turn an investor pitch deck (`.pdf` or `.pptx`) into a polished, single-page
**Customer Journey Market Narrative** PDF.

The output frames the company's market opportunity by tracking the target
customer's journey — from the moment they first feel the pain, through the
inadequacy of what exists today — so the investment thesis reads as a story of
deep, unmet need rather than a feature list.

---

## Quickstart

```bash
# 1. Install
pip install -e .

# 2. Configure your API key
cp .env.example .env
# then edit .env and set ANTHROPIC_API_KEY=sk-ant-...

# 3. Generate
pitch2onepager generate deck.pdf
```

Requires Python 3.11+.

---

## Usage

```bash
# Output defaults to {company_name}_onepager.pdf in the current directory
pitch2onepager generate deck.pdf

# Explicit output path
pitch2onepager generate deck.pdf --output onepager.pdf

# PPTX input with a custom header logo
pitch2onepager generate deck.pptx --output onepager.pdf --logo ./logo.png

# Override the model
pitch2onepager generate deck.pdf --model claude-sonnet-4-6
```

Run `pitch2onepager generate --help` for the full option list.

### Exit codes

| Code | Meaning |
| ---- | ------- |
| `0`  | Success |
| `1`  | File error — missing file, unsupported type, or unwritable output path |
| `2`  | Extraction error — no usable text in the deck, or the model returned unparseable output |
| `3`  | API error — `ANTHROPIC_API_KEY` missing/rejected, or the API failed after a retry |

---

## `.env` setup

The CLI loads `.env` from the working directory via `python-dotenv`.

```
ANTHROPIC_API_KEY=sk-ant-...        # required
PITCH2ONEPAGER_MODEL=claude-sonnet-4-6   # optional model override
```

No API key is ever read from or written to source. `.env` is gitignored.

---

## How it works

```
deck.pdf / deck.pptx
        │
        ▼
  extractor.py   pypdf / python-pptx → text per page/slide, boilerplate stripped
        │
        ▼
  analyzer.py    one Claude call → JSON validated against the Pydantic schema
        │              (retries once with a corrective prompt on bad JSON)
        ▼
  builder.py     reportlab → one Letter page, fixed-height bands, text clamped to fit
        │
        ▼
  onepager.pdf
```

### The six narrative sections

| Section | What it answers |
| ------- | --------------- |
| **Problem Awareness** | When does the customer first feel this, and how urgent is it? |
| **Discovery Odyssey** | How many vendors do they evaluate, over how long, and what makes the search hard? |
| **Solution Landscape** | What exists today, where does it fall short, and what do buyers do instead? |
| **A Day in the Life** | The operational, emotional, financial, and organisational cost of the status quo |
| **The Gaps That Matter** | What customers still suffer despite existing solutions, in their own words |
| **The Investment Thesis** | Why this unmet need is a market, why now, and what it's comparable to |

Fields the deck does not address are inferred by the model and prefixed with
`(inferred)` so a reader can tell sourced claims from analytical ones.

---

## Project layout

```
pitch2onepager/
├── cli.py         Click entry point, progress spinners, summary table
├── extractor.py   PDF/PPTX → DeckContent
├── analyzer.py    DeckContent → CustomerJourneyAnalysis (Claude)
├── builder.py     CustomerJourneyAnalysis → one-page PDF (reportlab)
├── models.py      Pydantic v2 schema for every structure above
└── utils.py       Errors, file-type detection, text cleaning

templates/
└── onepager_layout.py   Colours, typography, band geometry, wrap/fit helpers

assets/
└── logo_placeholder.png Default header mark
```

---

## Development

```bash
pip install -e ".[dev]"
pytest --cov=pitch2onepager --cov=templates --cov-report=term-missing
```

Tests mock the Anthropic client end-to-end — running the suite makes no network
calls and needs no API key. Fixture decks (a synthetic PDF and PPTX) are
generated in-process by `tests/conftest.py`.

### Design notes

- **One page, always.** Every band in `templates/onepager_layout.py` has a fixed
  height, and `fit_lines()` clamps text to the lines that band can hold,
  ellipsising the overflow. Long LLM output cannot push content onto a page 2.
- **Helvetica only.** No font files to ship; text is restricted to Latin-1
  glyphs so nothing renders as a black box.
- **Graceful degradation.** Image-only slides are marked `text_extracted=False`
  rather than crashing the run; a deck with too little text overall exits with
  code 2 and a specific suggestion (re-export or OCR).
