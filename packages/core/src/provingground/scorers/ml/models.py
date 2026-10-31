"""Lazy, cached loading of local Hugging Face models (CPU).

torch / transformers are optional (``pip install provingground[ml]``) and imported only when a
model is first used, so importing ProvingGround never needs them.
"""

from __future__ import annotations

import importlib.util
import os
import threading
from functools import lru_cache
from typing import Any

DEFAULT_NLI = "cross-encoder/nli-deberta-v3-small"
DEFAULT_INJECTION = "protectai/deberta-v3-base-prompt-injection-v2"  # the small variant is gated
DEFAULT_TOXICITY = "martin-ha/toxic-comment-model"


class MLNotInstalled(RuntimeError):
    pass


def ml_available() -> bool:
    return all(importlib.util.find_spec(m) is not None for m in ("torch", "transformers"))


def require_ml() -> None:
    if not ml_available():
        raise MLNotInstalled(
            "This scorer needs the local-model extra: install it with `pip install 'provingground[ml]'` "
            "(or `uv sync --extra ml`)."
        )


_load_lock = threading.Lock()
_infer_lock = threading.Lock()  # one batch at a time: avoids oversubscribing CPU threads


class LocalClassifier:
    """A sequence-classification model with a thread-safe batched ``predict``."""

    def __init__(self, name: str, max_length: int = 512) -> None:
        require_ml()
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.name = name
        self.max_length = max_length
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(name)
        self.model = AutoModelForSequenceClassification.from_pretrained(name).eval()
        self.labels: dict[int, str] = {
            int(i): str(label).lower() for i, label in self.model.config.id2label.items()
        }

    def predict(
        self, texts: list[str], pairs: list[str] | None = None, batch_size: int = 16
    ) -> list[dict[str, float]]:
        """Class probabilities per input (or per (text, pair) for sentence-pair models)."""
        out: list[dict[str, float]] = []
        for start in range(0, len(texts), batch_size):
            chunk = texts[start : start + batch_size]
            second = pairs[start : start + batch_size] if pairs is not None else None
            with _infer_lock, self.torch.no_grad():
                enc = self.tokenizer(
                    chunk,
                    second,
                    padding=True,
                    truncation=True,
                    max_length=self.max_length,
                    return_tensors="pt",
                )
                probs = self.model(**enc).logits.softmax(-1)
            for row in probs:
                out.append({self.labels[i]: float(p) for i, p in enumerate(row)})
        return out


@lru_cache(maxsize=8)
def _cached(name: str, max_length: int) -> LocalClassifier:
    with _load_lock:
        return LocalClassifier(name, max_length)


def get_model(name: str, max_length: int = 512) -> LocalClassifier:
    return _cached(name, max_length)


def positive_probability(
    probs: dict[str, float], positive: tuple[str, ...], negative: tuple[str, ...]
) -> float:
    """Probability mass on the 'bad' label, identified by name hints (labels differ per model)."""
    pos = sum(
        p
        for label, p in probs.items()
        if any(h in label for h in positive) and not any(n in label for n in negative)
    )
    return float(pos)


def warm(names: list[str]) -> dict[str, Any]:
    """Pre-download/load models (used by `pground doctor` and CI cache warmers)."""
    return {n: get_model(n).labels for n in names}
