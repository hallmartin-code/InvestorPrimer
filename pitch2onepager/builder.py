"""Compose the single-page Customer Journey one-pager with reportlab.

The page is built in two passes. Each section is first *planned* — its text is
wrapped and clamped to the number of lines its band can hold — which yields the
exact height that section will occupy. The planner then distributes the leftover
vertical space evenly across the gaps between bands and renders. Because every
band's cap plus the minimum gaps fits the sheet by construction, the output is
always exactly one page regardless of how verbose the model was.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as rl_canvas

from templates import onepager_layout as L

from .models import CustomerJourneyAnalysis
from .utils import BuildError

DEFAULT_LOGO = Path(__file__).resolve().parent.parent / "assets" / "logo_placeholder.png"

LABEL_H = 12.0       # section label plus the gap beneath it
SUBLABEL_H = 11.0    # sub-heading plus the gap beneath it
PILL_H = 13.0
PILL_GAP = 4.0


@dataclass
class Band:
    """A planned section: a known height and a closure that draws it."""

    height: float
    render: Callable[[rl_canvas.Canvas, float], None]


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def build_onepager(
    analysis: CustomerJourneyAnalysis,
    output_path: str,
    logo_path: str | None = None,
) -> str:
    """Render ``analysis`` to a one-page Letter PDF at ``output_path``.

    Returns the path written.

    Raises:
        BuildError: the destination is not writable.
    """
    out = Path(output_path)
    try:
        if out.parent and not out.parent.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
        c = rl_canvas.Canvas(str(out), pagesize=L.PAGE_SIZE)
    except (OSError, PermissionError) as exc:
        raise BuildError(
            f"Cannot write to '{out}': {exc}. Try --output with a path you can write to, "
            "e.g. --output ./onepager.pdf"
        ) from exc

    c.setTitle(f"{analysis.company_name} — Customer Journey Market Narrative")
    c.setAuthor("TEN Capital Network")
    c.setSubject("Customer Journey Market Narrative")

    _draw_background(c)
    _draw_header(c, analysis, logo_path)
    _draw_footer(c, analysis)

    bands = [
        _plan_two_columns(analysis),
        _plan_solution_landscape(analysis),
        _plan_day_in_the_life(analysis),
        _plan_gaps_that_matter(analysis),
    ]
    _render_bands(c, bands, rule_after=0)

    try:
        c.showPage()
        c.save()
    except (OSError, PermissionError) as exc:
        raise BuildError(f"Failed while writing '{out}': {exc}") from exc

    return str(out)


def _render_bands(
    c: rl_canvas.Canvas, bands: list[Band], *, rule_after: int | None = None
) -> None:
    """Lay planned bands out top-to-bottom, sharing slack evenly.

    The space above the footer counts as one more slot, so a sparse narrative
    spreads out evenly instead of stacking every spare point at the bottom.
    ``rule_after`` draws the horizontal divider centred in the gap that follows
    the given band, rather than letting it consume a slot of its own.
    """
    total = sum(b.height for b in bands)
    free = L.CONTENT_BOTTOM - L.CONTENT_TOP - total
    gap = L.clamp(free / max(1, len(bands)), L.GAP_MIN, L.GAP_MAX)

    top = L.CONTENT_TOP
    for i, band in enumerate(bands):
        band.render(c, top)
        top += band.height
        if i < len(bands) - 1:
            if i == rule_after:
                _draw_rule(c, top + gap / 2)
            top += gap


# --------------------------------------------------------------------------- #
# Drawing primitives
# --------------------------------------------------------------------------- #


def _draw_lines(
    c: rl_canvas.Canvas,
    lines: list[str],
    x: float,
    top: float,
    *,
    font: str,
    size: float,
    leading: float,
    color,
) -> float:
    """Draw pre-wrapped lines downward from ``top``; return the new top."""
    c.setFont(font, size)
    c.setFillColor(color)
    for line in lines:
        top += leading
        c.drawString(x, L.y(top - leading * 0.25), line)
    return top


def _section_label(c: rl_canvas.Canvas, text: str, x: float, top: float) -> float:
    c.setFont(L.FONT_BOLD, L.SIZE_SECTION_LABEL)
    c.setFillColor(L.ORANGE)
    c.drawString(x, L.y(top + L.SIZE_SECTION_LABEL), text.upper())
    return top + LABEL_H


def _sub_label(c: rl_canvas.Canvas, text: str, x: float, top: float) -> float:
    c.setFont(L.FONT_BOLD, L.SIZE_TINY)
    c.setFillColor(L.MUTED_TEXT)
    c.drawString(x, L.y(top + L.SIZE_TINY), text.upper())
    return top + SUBLABEL_H


def _draw_pill(
    c: rl_canvas.Canvas,
    text: str,
    x: float,
    top: float,
    *,
    fill,
    text_color,
    size: float = L.SIZE_TINY,
    padding: float = 6,
) -> float:
    """Draw one rounded pill; return its total width."""
    width = stringWidth(text, L.FONT, size) + padding * 2
    c.setFillColor(fill)
    c.roundRect(x, L.y(top + PILL_H), width, PILL_H, 3, stroke=0, fill=1)
    c.setFillColor(text_color)
    c.setFont(L.FONT, size)
    c.drawString(x + padding, L.y(top + PILL_H - 3.6), text)
    return width


def _layout_pills(items: list[str], max_width: float, max_rows: int) -> list[tuple[str, int, float]]:
    """Assign each pill a row and an x-offset; drop anything past ``max_rows``."""
    placed: list[tuple[str, int, float]] = []
    cursor, row = 0.0, 0
    for item in items:
        label = _clip_to_width(str(item).strip(), L.FONT, L.SIZE_TINY, max_width - 12)
        if not label:
            continue
        width = stringWidth(label, L.FONT, L.SIZE_TINY) + 12
        if cursor + width > max_width and cursor > 0:
            row += 1
            if row >= max_rows:
                break
            cursor = 0.0
        placed.append((label, row, cursor))
        cursor += width + PILL_GAP
    return placed


def _pill_rows_height(placed: list[tuple[str, int, float]]) -> float:
    if not placed:
        return 0.0
    rows = max(row for _, row, _ in placed) + 1
    return rows * (PILL_H + PILL_GAP)


def _wrap_items(
    items: list[str],
    width: float,
    max_lines: int,
    *,
    font: str = L.FONT,
    size: float = L.SIZE_SMALL,
) -> list[list[str]]:
    """Wrap each item, stopping once ``max_lines`` total lines are used."""
    wrapped: list[list[str]] = []
    remaining = max_lines
    for item in items:
        if remaining <= 0:
            break
        lines = L.fit_lines(str(item), font, size, width, remaining)
        if not lines:
            continue
        wrapped.append(lines)
        remaining -= len(lines)
    return wrapped


def _wrapped_height(wrapped: list[list[str]], leading: float, item_gap: float = 0.0) -> float:
    if not wrapped:
        return 0.0
    lines = sum(len(item) for item in wrapped)
    return lines * leading + item_gap * (len(wrapped) - 1)


def _draw_bullets(
    c: rl_canvas.Canvas,
    wrapped: list[list[str]],
    x: float,
    top: float,
    *,
    marker: str = "•",
    marker_color=None,
    size: float = L.SIZE_SMALL,
    leading: float = L.LEADING_SMALL,
    text_color=None,
) -> float:
    """Draw pre-wrapped bullet items; return the new top."""
    marker_color = marker_color or L.ORANGE
    text_color = text_color or L.BODY_TEXT
    indent = stringWidth(marker + " ", L.FONT, size)
    for lines in wrapped:
        c.setFont(L.FONT, size)
        c.setFillColor(marker_color)
        c.drawString(x, L.y(top + leading * 0.75), marker)
        top = _draw_lines(
            c, lines, x + indent, top,
            font=L.FONT, size=size, leading=leading, color=text_color,
        )
    return top


def _clip_to_width(text: str, font: str, size: float, max_width: float) -> str:
    if stringWidth(text, font, size) <= max_width:
        return text
    clipped = text
    while clipped and stringWidth(clipped + "…", font, size) > max_width:
        clipped = clipped[:-1]
    return (clipped.rstrip() + "…") if clipped else ""


def _first(values: list[str] | None, count: int) -> list[str]:
    return [str(v).strip() for v in (values or []) if str(v).strip()][:count]


# --------------------------------------------------------------------------- #
# Fixed chrome: background, header, footer
# --------------------------------------------------------------------------- #


def _draw_background(c: rl_canvas.Canvas) -> None:
    c.setFillColor(L.BACKGROUND)
    c.rect(0, 0, L.PAGE_W, L.PAGE_H, stroke=0, fill=1)


def _draw_header(
    c: rl_canvas.Canvas, a: CustomerJourneyAnalysis, logo_path: str | None
) -> None:
    c.setFillColor(L.NAVY)
    c.rect(0, L.y(L.HEADER_H), L.PAGE_W, L.HEADER_H, stroke=0, fill=1)

    logo_w = _draw_logo(c, logo_path)
    text_w = L.CONTENT_W - logo_w - (12 if logo_w else 0)

    c.setFont(L.FONT_BOLD, L.SIZE_COMPANY)
    c.setFillColor(L.WHITE)
    c.drawString(
        L.MARGIN, L.y(38), _clip_to_width(a.company_name, L.FONT_BOLD, L.SIZE_COMPANY, text_w)
    )

    top = 44
    c.setFont(L.FONT_ITALIC, L.SIZE_TAGLINE)
    c.setFillColor(L.LIGHT_BLUE)
    for line in L.fit_lines(a.tagline, L.FONT_ITALIC, L.SIZE_TAGLINE, text_w, 2):
        top += 13
        c.drawString(L.MARGIN, L.y(top), line)

    # Follows the tagline, whether it wrapped to one line or two, without ever
    # running past the bottom of the band.
    c.setFont(L.FONT_BOLD, L.SIZE_TINY)
    c.setFillColor(L.ORANGE)
    c.drawString(
        L.MARGIN,
        L.y(min(top + 13, L.HEADER_H - 10)),
        _clip_to_width(
            f"TARGET CUSTOMER: {a.target_customer}", L.FONT_BOLD, L.SIZE_TINY, text_w
        ),
    )


def _draw_logo(c: rl_canvas.Canvas, logo_path: str | None) -> float:
    """Draw the logo in the top-right of the header. Returns the width used."""
    path = Path(logo_path) if logo_path else DEFAULT_LOGO
    max_w, max_h = 110.0, 34.0
    if path.exists():
        try:
            image = ImageReader(str(path))
            iw, ih = image.getSize()
            scale = min(max_w / iw, max_h / ih)
            w, h = iw * scale, ih * scale
            c.drawImage(
                image,
                L.PAGE_W - L.MARGIN - w,
                L.y(24 + h),
                width=w,
                height=h,
                mask="auto",
                preserveAspectRatio=True,
            )
            return w
        except Exception:  # noqa: BLE001 - fall through to the text mark
            pass

    # Text fallback so the header never looks broken.
    c.setFont(L.FONT_BOLD, 10)
    c.setFillColor(L.WHITE)
    c.drawRightString(L.PAGE_W - L.MARGIN, L.y(32), "TEN CAPITAL")
    c.setFont(L.FONT, L.SIZE_TINY)
    c.setFillColor(L.LIGHT_BLUE)
    c.drawRightString(L.PAGE_W - L.MARGIN, L.y(44), "NETWORK")
    return 90.0


def _draw_footer(c: rl_canvas.Canvas, a: CustomerJourneyAnalysis) -> None:
    t = a.investment_thesis
    c.setFillColor(L.NAVY)
    c.rect(0, 0, L.PAGE_W, L.FOOTER_H, stroke=0, fill=1)

    left_x = L.MARGIN
    left_w = L.CONTENT_W * 0.56
    right_x = L.MARGIN + left_w + 16
    right_w = L.CONTENT_W - left_w - 16

    # Distances from the top of the page, consistent with the rest of the layout.
    label_top = L.PAGE_H - L.FOOTER_H + 14
    c.setFont(L.FONT_BOLD, L.SIZE_SECTION_LABEL)
    c.setFillColor(L.ORANGE)
    c.drawString(left_x, L.y(label_top), "THE INVESTMENT THESIS")

    _draw_lines(
        c,
        L.fit_lines(t.market_opportunity_statement, L.FONT, L.SIZE_TINY, left_w, 3),
        left_x,
        label_top + 12,
        font=L.FONT, size=L.SIZE_TINY, leading=L.LEADING_TINY, color=L.LIGHT_BLUE,
    )

    right_top = _draw_lines(
        c,
        L.fit_lines(f"WHY NOW: {t.why_now}", L.FONT_BOLD, L.SIZE_TINY, right_w, 2),
        right_x,
        label_top,
        font=L.FONT_BOLD, size=L.SIZE_TINY, leading=L.LEADING_TINY, color=L.WHITE,
    ) + 2

    if t.estimated_market_size:
        right_top = _draw_lines(
            c,
            L.fit_lines(
                f"MARKET SIZE: {t.estimated_market_size}", L.FONT, L.SIZE_TINY, right_w, 2
            ),
            right_x,
            right_top,
            font=L.FONT, size=L.SIZE_TINY, leading=L.LEADING_TINY, color=L.LIGHT_BLUE,
        ) + 2

    comparables = _first(t.comparable_industries, 4)
    if comparables:
        _draw_lines(
            c,
            L.fit_lines(
                "COMPARABLES: " + ", ".join(comparables), L.FONT, L.SIZE_TINY, right_w, 2
            ),
            right_x,
            right_top,
            font=L.FONT, size=L.SIZE_TINY, leading=L.LEADING_TINY, color=L.LIGHT_BLUE,
        )

    c.setFont(L.FONT, L.SIZE_TINY)
    c.setFillColor(L.LIGHT_BLUE)
    c.drawRightString(
        L.PAGE_W - L.MARGIN, 12, "Prepared by TEN Capital · tencapital.group"
    )


# --------------------------------------------------------------------------- #
# Band 1 — Problem Awareness | Discovery Odyssey
# --------------------------------------------------------------------------- #


def _plan_two_columns(a: CustomerJourneyAnalysis) -> Band:
    p, d = a.problem_awareness, a.discovery_odyssey
    right_x = L.MARGIN + L.COL_W + L.COL_GAP

    # -- Left: problem awareness ------------------------------------------- #
    trigger = L.fit_lines(p.trigger_moment, L.FONT_BOLD, L.SIZE_BODY, L.COL_W, 3)
    pill_reserve = 8 + PILL_H
    used = LABEL_H + L.text_height(len(trigger), L.LEADING_BODY) + 3
    pain_budget = L.MAX_TWOCOL_H - used - pill_reserve
    pain = L.fit_lines(
        p.pain_description,
        L.FONT,
        L.SIZE_SMALL,
        L.COL_W,
        L.lines_that_fit(pain_budget, L.LEADING_SMALL),
    )
    left_h = used + L.text_height(len(pain), L.LEADING_SMALL) + pill_reserve

    # -- Right: discovery odyssey ------------------------------------------ #
    metric = _clip_to_width(
        f"{d.vendor_count_estimate}  ·  {d.avg_time_to_find_fit}",
        L.FONT_BOLD,
        L.SIZE_METRIC,
        L.COL_W,
    )
    pills = _layout_pills(_first(d.channels_navigated, 6), L.COL_W, max_rows=2)
    used = LABEL_H + L.SIZE_METRIC + 6 + _pill_rows_height(pills) + 2
    friction = _wrap_items(
        _first(d.friction_points, 5),
        L.COL_W - stringWidth("• ", L.FONT, L.SIZE_TINY),
        L.lines_that_fit(L.MAX_TWOCOL_H - used, L.LEADING_TINY),
        size=L.SIZE_TINY,
    )
    right_h = used + _wrapped_height(friction, L.LEADING_TINY)

    height = min(max(left_h, right_h), L.MAX_TWOCOL_H)

    def render(c: rl_canvas.Canvas, top: float) -> None:
        # Left column.
        y = _section_label(c, "Problem Awareness", L.MARGIN, top)
        y = _draw_lines(
            c, trigger, L.MARGIN, y,
            font=L.FONT_BOLD, size=L.SIZE_BODY, leading=L.LEADING_BODY, color=L.NAVY,
        ) + 3
        y = _draw_lines(
            c, pain, L.MARGIN, y,
            font=L.FONT, size=L.SIZE_SMALL, leading=L.LEADING_SMALL, color=L.BODY_TEXT,
        ) + 8
        used_w = _draw_pill(
            c,
            f"TIME TO ACT: {p.time_before_seeking_solution}",
            L.MARGIN, y,
            fill=L.NAVY, text_color=L.WHITE,
        )
        _draw_pill(
            c,
            f"{p.urgency_level.upper()} URGENCY",
            L.MARGIN + used_w + 5, y,
            fill=L.URGENCY_COLORS.get(p.urgency_level, L.ORANGE),
            text_color=L.WHITE,
        )

        # Right column.
        y = _section_label(c, "Discovery Odyssey", right_x, top)
        c.setFont(L.FONT_BOLD, L.SIZE_METRIC)
        c.setFillColor(L.NAVY)
        c.drawString(right_x, L.y(y + L.SIZE_METRIC), metric)
        y += L.SIZE_METRIC + 6
        for label, row, offset in pills:
            _draw_pill(
                c, label, right_x + offset, y + row * (PILL_H + PILL_GAP),
                fill=L.LIGHT_BLUE, text_color=L.NAVY,
            )
        y += _pill_rows_height(pills) + 2
        _draw_bullets(
            c, friction, right_x, y, size=L.SIZE_TINY, leading=L.LEADING_TINY
        )

    return Band(height=height, render=render)


# --------------------------------------------------------------------------- #
# Divider
# --------------------------------------------------------------------------- #


def _draw_rule(c: rl_canvas.Canvas, top: float) -> None:
    c.setStrokeColor(L.RULE)
    c.setLineWidth(1)
    c.line(L.MARGIN, L.y(top), L.PAGE_W - L.MARGIN, L.y(top))


# --------------------------------------------------------------------------- #
# Band 2 — Solution Landscape
# --------------------------------------------------------------------------- #


def _plan_solution_landscape(a: CustomerJourneyAnalysis) -> Band:
    s = a.solution_landscape
    right_x = L.MARGIN + L.COL_W + L.COL_GAP
    body_budget = L.MAX_SOLUTION_H - LABEL_H - SUBLABEL_H

    # Left: each of today's options annotated with the shortfall that kills it.
    solutions = _first(s.existing_solutions, 4)
    shortfalls = _first(s.key_shortfalls, 4)
    rows = [
        f"{sol}  ×  {shortfalls[i]}" if i < len(shortfalls) else sol
        for i, sol in enumerate(solutions)
    ]
    rows.extend(f"×  {sf}" for sf in shortfalls[len(solutions):])
    left = _wrap_items(
        rows,
        L.COL_W - stringWidth("– ", L.FONT, L.SIZE_TINY),
        L.lines_that_fit(body_budget, L.LEADING_TINY),
        size=L.SIZE_TINY,
    )
    left_h = LABEL_H + SUBLABEL_H + _wrapped_height(left, L.LEADING_TINY)

    # Right: workarounds in a soft amber callout.
    box_pad = 7.0
    workarounds = _wrap_items(
        _first(s.workarounds_customers_use, 4),
        L.COL_W - 16 - stringWidth("• ", L.FONT, L.SIZE_TINY),
        L.lines_that_fit(body_budget - 2 * box_pad, L.LEADING_TINY),
        size=L.SIZE_TINY,
    )
    box_h = 2 * box_pad + SUBLABEL_H + _wrapped_height(workarounds, L.LEADING_TINY)
    right_h = LABEL_H + box_h

    height = min(max(left_h, right_h, LABEL_H + 24), L.MAX_SOLUTION_H)
    box_h = min(box_h, height - LABEL_H)

    def render(c: rl_canvas.Canvas, top: float) -> None:
        body_top = _section_label(c, "Solution Landscape", L.MARGIN, top)

        y = _sub_label(c, "Today's Options", L.MARGIN, body_top)
        _draw_bullets(
            c, left, L.MARGIN, y, marker="–", size=L.SIZE_TINY, leading=L.LEADING_TINY
        )

        c.setFillColor(L.AMBER_BG)
        c.setStrokeColor(L.AMBER_EDGE)
        c.setLineWidth(0.75)
        c.roundRect(right_x, L.y(body_top + box_h), L.COL_W, box_h, 4, stroke=1, fill=1)

        inner_top = _sub_label(
            c, "Customer Workarounds", right_x + 8, body_top + box_pad
        )
        _draw_bullets(
            c, workarounds, right_x + 8, inner_top, size=L.SIZE_TINY, leading=L.LEADING_TINY
        )

    return Band(height=height, render=render)


# --------------------------------------------------------------------------- #
# Band 3 — A Day in the Life
# --------------------------------------------------------------------------- #


def _plan_day_in_the_life(a: CustomerJourneyAnalysis) -> Band:
    d = a.day_in_the_life
    cells = (
        ("Operational", d.operational_burden),
        ("Emotional", d.emotional_burden),
        ("Financial", d.financial_burden),
        ("Organisational", d.organizational_spillover),
    )

    inner_w = L.CELL_W - 12
    chrome = L.CELL_ACCENT_H + 6 + L.SIZE_TINY + 4  # accent bar, label, gap
    pad_bottom = 6.0
    body_budget = L.MAX_DAY_H - LABEL_H - chrome - pad_bottom
    max_lines = L.lines_that_fit(body_budget, L.LEADING_TINY)

    wrapped = [
        L.fit_lines(body, L.FONT, L.SIZE_TINY, inner_w, max_lines) for _, body in cells
    ]
    tallest = max((len(lines) for lines in wrapped), default=0)
    cell_h = chrome + L.text_height(tallest, L.LEADING_TINY) + pad_bottom
    height = min(LABEL_H + cell_h, L.MAX_DAY_H)

    def render(c: rl_canvas.Canvas, top: float) -> None:
        cells_top = _section_label(c, "A Day in the Life", L.MARGIN, top)
        for i, (label, _) in enumerate(cells):
            x = L.MARGIN + i * (L.CELL_W + L.CELL_GAP)
            accent = L.CELL_ACCENTS[i]

            c.setFillColor(L.WHITE)
            c.rect(x, L.y(cells_top + cell_h), L.CELL_W, cell_h, stroke=0, fill=1)
            c.setFillColor(accent)
            c.rect(
                x, L.y(cells_top + L.CELL_ACCENT_H), L.CELL_W, L.CELL_ACCENT_H,
                stroke=0, fill=1,
            )

            label_top = cells_top + L.CELL_ACCENT_H + 6
            c.setFont(L.FONT_BOLD, L.SIZE_TINY)
            c.setFillColor(accent)
            c.drawString(x + 6, L.y(label_top + L.SIZE_TINY), label.upper())

            _draw_lines(
                c, wrapped[i], x + 6, label_top + L.SIZE_TINY + 4,
                font=L.FONT, size=L.SIZE_TINY, leading=L.LEADING_TINY, color=L.BODY_TEXT,
            )

    return Band(height=height, render=render)


# --------------------------------------------------------------------------- #
# Band 4 — The Gaps That Matter
# --------------------------------------------------------------------------- #


def _plan_gaps_that_matter(a: CustomerJourneyAnalysis) -> Band:
    g = a.gaps_that_matter
    indent = 16.0
    quotes = _first(g.customer_quotes, 2)

    body_budget = L.MAX_GAPS_H - LABEL_H
    quote_reserve = min(58.0, body_budget * 0.42) if quotes else 0.0
    needs_gap = 2.0

    needs = _wrap_items(
        _first(g.unmet_needs, 6),
        L.CONTENT_W - indent,
        L.lines_that_fit(body_budget - quote_reserve, L.LEADING_SMALL),
    )
    needs_h = _wrapped_height(needs, L.LEADING_SMALL, needs_gap)

    quote_blocks: list[list[str]] = []
    if quotes:
        available = body_budget - needs_h - 6
        remaining = L.lines_that_fit(available, L.LEADING_TINY)
        for quote in quotes:
            if remaining <= 0:
                break
            text = quote if quote.strip().startswith(("“", '"')) else f"“{quote}”"
            lines = L.fit_lines(text, L.FONT_ITALIC, L.SIZE_TINY, L.CONTENT_W - 14, remaining)
            if not lines:
                break
            quote_blocks.append(lines)
            remaining -= len(lines)

    quotes_h = (
        _wrapped_height(quote_blocks, L.LEADING_TINY, 4.0) + 6 if quote_blocks else 0.0
    )
    height = min(LABEL_H + needs_h + quotes_h, L.MAX_GAPS_H)

    def render(c: rl_canvas.Canvas, top: float) -> None:
        y = _section_label(c, "The Gaps That Matter", L.MARGIN, top)

        for i, lines in enumerate(needs, start=1):
            c.setFont(L.FONT_BOLD, L.SIZE_SMALL)
            c.setFillColor(L.ORANGE)
            c.drawString(L.MARGIN, L.y(y + L.LEADING_SMALL * 0.75), f"{i}.")
            y = _draw_lines(
                c, lines, L.MARGIN + indent, y,
                font=L.FONT, size=L.SIZE_SMALL, leading=L.LEADING_SMALL, color=L.BODY_TEXT,
            ) + needs_gap

        if not quote_blocks:
            return
        y += 6 - needs_gap
        for lines in quote_blocks:
            block_h = L.text_height(len(lines), L.LEADING_TINY)
            c.setFillColor(L.ORANGE)
            c.rect(L.MARGIN, L.y(y + block_h), 2, block_h, stroke=0, fill=1)
            y = _draw_lines(
                c, lines, L.MARGIN + 10, y,
                font=L.FONT_ITALIC, size=L.SIZE_TINY, leading=L.LEADING_TINY,
                color=L.MUTED_TEXT,
            ) + 4

    return Band(height=height, render=render)


__all__ = ["build_onepager", "BuildError"]
