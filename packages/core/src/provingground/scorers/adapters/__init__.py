"""Third-party evaluation frameworks as scorers. Registration is import-safe: the frameworks
themselves are imported only when a scorer is constructed (``provingground[adapters]``)."""

from __future__ import annotations

from . import deepeval_adapter, ragas_adapter  # noqa: F401  (registration side effects)
from .common import AdapterNotInstalled

__all__ = ["AdapterNotInstalled"]
