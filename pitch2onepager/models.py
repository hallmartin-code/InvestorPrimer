"""Pydantic models for deck ingestion and LLM-extracted narrative structure."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Deck ingestion
# --------------------------------------------------------------------------- #


class SlideContent(BaseModel):
    slide_number: int
    title: str | None = None
    raw_text: str = ""
    text_extracted: bool = True


class DeckContent(BaseModel):
    source_file: str
    file_type: Literal["pdf", "pptx"]
    slide_count: int
    slides: list[SlideContent] = Field(default_factory=list)
    full_text: str = ""

    @property
    def extracted_slide_count(self) -> int:
        """Number of slides that yielded usable text."""
        return sum(1 for s in self.slides if s.text_extracted)


# --------------------------------------------------------------------------- #
# Customer journey narrative (LLM output)
# --------------------------------------------------------------------------- #


class ProblemAwareness(BaseModel):
    trigger_moment: str
    pain_description: str
    time_before_seeking_solution: str
    urgency_level: Literal["low", "medium", "high", "critical"]


class DiscoveryOdyssey(BaseModel):
    channels_navigated: list[str] = Field(default_factory=list)
    vendor_count_estimate: str
    avg_time_to_find_fit: str
    friction_points: list[str] = Field(default_factory=list)


class SolutionLandscape(BaseModel):
    existing_solutions: list[str] = Field(default_factory=list)
    key_shortfalls: list[str] = Field(default_factory=list)
    workarounds_customers_use: list[str] = Field(default_factory=list)


class DayInTheLife(BaseModel):
    operational_burden: str
    emotional_burden: str
    financial_burden: str
    organizational_spillover: str


class GapsThatMatter(BaseModel):
    unmet_needs: list[str] = Field(default_factory=list)
    customer_quotes: list[str] = Field(default_factory=list)


class InvestmentThesis(BaseModel):
    market_opportunity_statement: str
    comparable_industries: list[str] = Field(default_factory=list)
    why_now: str
    estimated_market_size: str | None = None


class CustomerJourneyAnalysis(BaseModel):
    company_name: str
    tagline: str
    target_customer: str
    problem_awareness: ProblemAwareness
    discovery_odyssey: DiscoveryOdyssey
    solution_landscape: SolutionLandscape
    day_in_the_life: DayInTheLife
    gaps_that_matter: GapsThatMatter
    investment_thesis: InvestmentThesis
