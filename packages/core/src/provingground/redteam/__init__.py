"""Red-team taxonomy and attack-case generation."""

from __future__ import annotations

from .converters import CONVERTERS, apply_converter
from .generator import build_attack_cases
from .taxonomy import DEFAULT_CONVERTERS, DIRECT_TECHNIQUES, GOALS, INDIRECT_TEMPLATES, Goal

__all__ = [
    "CONVERTERS",
    "DEFAULT_CONVERTERS",
    "DIRECT_TECHNIQUES",
    "GOALS",
    "INDIRECT_TEMPLATES",
    "Goal",
    "apply_converter",
    "build_attack_cases",
]
