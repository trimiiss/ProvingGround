"""Token pricing (USD per 1M tokens), loaded from ``pricing.yaml`` plus optional overrides."""

from __future__ import annotations

import fnmatch
from functools import lru_cache
from importlib import resources
from pathlib import Path

import yaml
from pydantic import BaseModel

from ..models import Usage


class LongContextTier(BaseModel):
    threshold_tokens: int
    input: float
    output: float


class ModelPrice(BaseModel):
    input: float
    output: float
    cache_read: float | None = None
    cache_write: float | None = None
    long_context: LongContextTier | None = None

    def cost(self, usage: Usage) -> float:
        inp, out = self.input, self.output
        if self.long_context and usage.total_input_tokens > self.long_context.threshold_tokens:
            inp, out = self.long_context.input, self.long_context.output
        cache_read = self.cache_read if self.cache_read is not None else inp * 0.1
        cache_write = self.cache_write if self.cache_write is not None else inp * 1.25
        return (
            usage.input_tokens * inp
            + usage.output_tokens * out
            + usage.cache_read_tokens * cache_read
            + usage.cache_write_tokens * cache_write
        ) / 1_000_000


class PriceTable(BaseModel):
    as_of: str = ""
    models: dict[str, ModelPrice]

    def lookup(self, model_ref: str) -> ModelPrice | None:
        if model_ref in self.models:
            return self.models[model_ref]
        for pattern, price in self.models.items():
            if "*" in pattern and fnmatch.fnmatch(model_ref, pattern):
                return price
        return None

    def cost(self, model_ref: str | None, usage: Usage) -> float | None:
        if not model_ref:
            return None
        price = self.lookup(model_ref)
        return None if price is None else price.cost(usage)


@lru_cache(maxsize=1)
def default_price_table() -> PriceTable:
    text = resources.files("provingground.providers").joinpath("pricing.yaml").read_text(encoding="utf-8")
    return PriceTable.model_validate(yaml.safe_load(text))


def load_price_table(override: str | Path | None = None) -> PriceTable:
    table = default_price_table()
    if override is None or not Path(override).is_file():
        return table
    extra = PriceTable.model_validate(yaml.safe_load(Path(override).read_text(encoding="utf-8")))
    return PriceTable(as_of=extra.as_of or table.as_of, models={**table.models, **extra.models})


def cost_usd(model_ref: str | None, usage: Usage) -> float | None:
    return default_price_table().cost(model_ref, usage)
