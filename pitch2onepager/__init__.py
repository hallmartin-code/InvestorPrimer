"""pitch2onepager — pitch deck to Customer Journey Market Narrative one-pager."""

from .models import (
    CustomerJourneyAnalysis,
    DayInTheLife,
    DeckContent,
    DiscoveryOdyssey,
    GapsThatMatter,
    InvestmentThesis,
    ProblemAwareness,
    SlideContent,
    SolutionLandscape,
)
from .utils import (
    AnalysisError,
    APIError,
    BuildError,
    ExtractionError,
    FileError,
    Pitch2OnePagerError,
)

__version__ = "0.1.0"

__all__ = [
    "CustomerJourneyAnalysis",
    "DayInTheLife",
    "DeckContent",
    "DiscoveryOdyssey",
    "GapsThatMatter",
    "InvestmentThesis",
    "ProblemAwareness",
    "SlideContent",
    "SolutionLandscape",
    "AnalysisError",
    "APIError",
    "BuildError",
    "ExtractionError",
    "FileError",
    "Pitch2OnePagerError",
    "__version__",
]
