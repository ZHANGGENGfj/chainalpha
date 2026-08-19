"""Stable report-facing API for single-company research outputs.

The detailed report builder remains in ``investment.topic_report`` during the
incremental migration. New callers should import from this module so the
underlying implementation can be split later without another API migration.
"""

from chainalpha.investment.topic_report import (
    build_topic_research_report,
    render_professional_topic_report,
    render_topic_research_markdown,
)

__all__ = [
    "build_topic_research_report",
    "render_professional_topic_report",
    "render_topic_research_markdown",
]
