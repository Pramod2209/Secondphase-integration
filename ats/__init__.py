"""Bulk resume scoring. Public API is imported lazily so that lightweight submodules
(parser/extractor, used by worker processes) do not pull in torch/sentence-transformers."""

__all__ = [
    "screen_bulk",
    "screen_single",
    "build_job_profile",
    "JobProfile",
    "ResumeResult",
    "ATSOutput",
    "ScoreBreakdown",
]


def __getattr__(name):
    if name in ("screen_bulk", "screen_single", "build_job_profile"):
        from . import api

        return getattr(api, name)
    if name in ("JobProfile", "ResumeResult", "ATSOutput", "ScoreBreakdown"):
        from . import schema

        return getattr(schema, name)
    raise AttributeError(name)
