"""agent-fetch-kit — resilient web fetch escalation ladder.

Public API:
    from fetchkit import Config, fetch, probe, FetchResult, History, is_challenged, CHALLENGE_MARKERS
"""
from .config import Config
from .core import fetch, probe
from .backends import FetchResult
from .detect import is_challenged, CHALLENGE_MARKERS
from .history import History

__all__ = ["Config", "fetch", "probe", "FetchResult", "History", "is_challenged", "CHALLENGE_MARKERS"]
__version__ = "1.0.0"
