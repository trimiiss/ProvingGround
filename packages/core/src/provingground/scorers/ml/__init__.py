"""Local PyTorch scorers. Importing registers them; torch itself loads lazily on first use."""

from __future__ import annotations

from . import classifiers, nli  # noqa: F401  (registration side effects)
from .models import MLNotInstalled, ml_available

__all__ = ["MLNotInstalled", "ml_available"]
