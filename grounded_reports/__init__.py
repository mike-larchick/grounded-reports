"""grounded-reports: LLM narratives that are not allowed to be wrong about the numbers.

Pipeline:  data -> fact sheet -> LLM draft -> claim validation -> retry w/ feedback -> fallback
"""

from .facts import FactSheet, Metric, build_fact_sheet, load_csv
from .validate import ValidationResult, Violation, validate_narrative
from .pipeline import PipelineResult, run_pipeline

__all__ = [
    "FactSheet",
    "Metric",
    "build_fact_sheet",
    "load_csv",
    "ValidationResult",
    "Violation",
    "validate_narrative",
    "PipelineResult",
    "run_pipeline",
]
