"""Layout constants, colours, and text helpers for the one-pager.

All geometry is expressed in PostScript points from the *top* of a US Letter
page; :func:`y` converts to reportlab's bottom-left origin. Every band has a
fixed height so the composed document is guaranteed to fit one page.
"""

from __future__ import annotations

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth

# --------------------------------------------------------------------------- #
# Page geometry
# --------------------------------------------------------------------------- #

PAGE_SIZE = letter
PAGE_W, PAGE_H = letter          # 612 x 792
MARGIN = 36                      # 0.5"
CONTENT_W = PAGE_W - 2 * MARGIN  # 540
COL_GAP = 18
COL_W = (CONTENT_W - COL_GAP) / 2  # ~261 (48% each)

# --------------------------------------------------------------------------- #
# Palette
# --------------------------------------------------------------------------- #

NAVY = HexColor("#1B2A4A")
ORANGE = HexColor("#E85D26")
LIGHT_BLUE = HexColor("#C8D6E8")
BACKGROUND = HexColor("#F7F9FC")
WHITE = HexColor("#FFFFFF")
RULE = HexColor("#E0E4EC")
BODY_TEXT = HexColor("#2A3244")
MUTED_TEXT = HexColor("#5C6880")
AMBER_BG = HexColor("#FDF3E3")
AMBER_EDGE = HexColor("#F0D9AE")

# Accent set for the four "Day in the Life" cells.
CELL_ACCENTS = (
    HexColor("#1B2A4A"),  # operational — navy
    HexColor("#E85D26"),  # emotional  — orange
    HexColor("#2F7D6E"),  # financial  — teal
    HexColor("#7A5AA8"),  # organisational — violet
)

URGENCY_COLORS = {
    "low": HexColor("#4B8F6B"),
    "medium": HexColor("#C89A2B"),
    "high": HexColor("#E85D26"),
    "critical": HexColor("#B3261E"),
}

# --------------------------------------------------------------------------- #
# Typography (Helvetica ships with reportlab — no font files required)
# --------------------------------------------------------------------------- #

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
FONT_ITALIC = "Helvetica-Oblique"

SIZE_COMPANY = 22
SIZE_TAGLINE = 11
SIZE_SECTION_LABEL = 8
SIZE_BODY = 10
SIZE_SMALL = 8
SIZE_TINY = 7
SIZE_METRIC = 12

LEADING_BODY = 12
LEADING_SMALL = 9.6
LEADING_TINY = 8.4

# --------------------------------------------------------------------------- #
# Vertical bands
#
# Bands flow top-to-bottom and size themselves to their content, but each is
# capped at a maximum height. Because the caps plus the minimum inter-band gaps
# always fit between CONTENT_TOP and CONTENT_BOTTOM, the page can never overflow
# no matter how verbose the model's output is. Leftover space is shared out
# evenly across the gaps so short narratives still fill the sheet.
# --------------------------------------------------------------------------- #

# Tall enough for a two-line tagline plus the target-customer strapline.
HEADER_H = 92
FOOTER_H = 92

CONTENT_TOP = HEADER_H + 14          # 106
CONTENT_BOTTOM = PAGE_H - FOOTER_H - 2  # 698

MAX_TWOCOL_H = 150
DIVIDER_H = 1
MAX_SOLUTION_H = 130
MAX_DAY_H = 120
MAX_GAPS_H = 146

GAP_MIN = 12.0
GAP_MAX = 88.0

#: Bands that share the vertical flow (the divider is drawn inside a gap).
BAND_COUNT = 4

#: Sanity check: worst-case content plus minimum gaps must fit the page.
MAX_BAND_TOTAL = MAX_TWOCOL_H + MAX_SOLUTION_H + MAX_DAY_H + MAX_GAPS_H
assert CONTENT_TOP + MAX_BAND_TOTAL + (BAND_COUNT - 1) * GAP_MIN <= CONTENT_BOTTOM

# Four-cell grid inside the "Day in the Life" band.
CELL_GAP = 10
CELL_W = (CONTENT_W - 3 * CELL_GAP) / 4
CELL_ACCENT_H = 3

SECTION_LABEL_GAP = 12  # space below a section label before its body text


def y(from_top: float) -> float:
    """Convert a distance measured from the page top into a reportlab y."""
    return PAGE_H - from_top


# --------------------------------------------------------------------------- #
# Text measurement helpers
# --------------------------------------------------------------------------- #


def wrap_lines(text: str, font: str, size: float, max_width: float) -> list[str]:
    """Greedy word wrap, hard-breaking any single word wider than ``max_width``."""
    if not text:
        return []
    lines: list[str] = []
    for paragraph in str(text).split("\n"):
        words = paragraph.split()
        if not words:
            continue
        current = ""
        for word in words:
            trial = f"{current} {word}".strip()
            if stringWidth(trial, font, size) <= max_width:
                current = trial
                continue
            if current:
                lines.append(current)
                current = ""
            # Hard-break a word that cannot fit on a line by itself.
            while stringWidth(word, font, size) > max_width and len(word) > 1:
                cut = len(word)
                while cut > 1 and stringWidth(word[:cut], font, size) > max_width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            current = word
        if current:
            lines.append(current)
    return lines


def fit_lines(
    text: str,
    font: str,
    size: float,
    max_width: float,
    max_lines: int,
) -> list[str]:
    """Wrap ``text`` and clamp it to ``max_lines``, ellipsising the final line."""
    if max_lines <= 0:
        return []
    lines = wrap_lines(text, font, size, max_width)
    if len(lines) <= max_lines:
        return lines
    kept = lines[:max_lines]
    last = kept[-1]
    while last and stringWidth(last + "…", font, size) > max_width:
        last = last[:-1].rstrip()
    kept[-1] = (last + "…") if last else "…"
    return kept


def text_height(line_count: int, leading: float) -> float:
    """Vertical space consumed by ``line_count`` lines at the given leading."""
    return max(0, line_count) * leading


def lines_that_fit(available: float, leading: float) -> int:
    """How many lines of the given leading fit into ``available`` points."""
    if available <= 0 or leading <= 0:
        return 0
    return int(available // leading)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
