from __future__ import annotations

import pytest

from provingground.config import ConfigError, load_config
from provingground.models import Usage
from provingground.providers import parse_model_ref, same_model
from provingground.providers.pricing import default_price_table


def test_pricing_uses_cache_rates() -> None:
    table = default_price_table()
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000, cache_read_tokens=1_000_000)
    cost = table.cost("anthropic/claude-opus-5-5", usage)
    # 4 (input) + 20 (output) + 0.20 (cache read) = 24.20
    assert cost is not None and abs(cost - 24.20) < 1e-6


def test_pricing_unknown_model_is_none_not_zero() -> None:
    assert default_price_table().cost("mystery/model", Usage(input_tokens=1000)) is None
    # Wildcard local models are priced at zero.
    assert default_price_table().cost("ollama/llama3.2", Usage(input_tokens=1000)) == 0.0


def test_haiku_long_context_tier() -> None:
    table = default_price_table()
    # 150k input tokens cross the 100k threshold and are billed at the 5x long-context rate.
    long = table.cost("anthropic/claude-haiku-5-5", Usage(input_tokens=150_000))
    assert long is not None and abs(long - 150_000 * 0.50 / 1_000_000) < 1e-9
    assert long > 150_000 * 0.10 / 1_000_000  # more than the short-context input rate


def test_parse_and_same_model() -> None:
    assert parse_model_ref("anthropic/claude-opus-5-5") == ("anthropic", "claude-opus-5-5")
    with pytest.raises(ValueError):
        parse_model_ref("bare-name")
    assert same_model("anthropic/claude-opus-5-5", "claude-opus-5-5")
    assert same_model("anthropic/claude-opus-5-5", "claude-opus-5-5-20260401")
    assert not same_model("sim/model-a", "model-b")


def test_config_rejects_unknown_keys(tmp_path) -> None:
    (tmp_path / "provingground.yaml").write_text("project: x\nbogus_key: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path / "provingground.yaml")


def test_config_env_interpolation(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MY_MODEL", "sim/model-c")
    (tmp_path / "provingground.yaml").write_text(
        "project: x\ntargets:\n  t:\n    kind: model\n    model: ${MY_MODEL}\n    suites: [q]\n"
        "suites:\n  q:\n    dataset: d.jsonl\n    scorers: [correctness]\n",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path / "provingground.yaml")
    assert cfg.target("t").model == "sim/model-c"


def test_target_overrides() -> None:
    from provingground.config import TargetConfig

    tc = TargetConfig(kind="model", model="sim/model-a")
    assert tc.with_overrides({"model": "sim/model-b"}).model == "sim/model-b"
    py = TargetConfig(kind="python", entrypoint="m:f", args={"agent": "x"})
    assert py.with_overrides({"model": "sim/model-b"}).args["model"] == "sim/model-b"


def test_gate_rule_requires_threshold() -> None:
    from provingground.config import GateRule

    with pytest.raises(ValueError):
        GateRule(metric="quality.correctness")  # no threshold
    GateRule(metric="quality.correctness", min=0.8)  # ok
    GateRule(critical_cases="must_pass")  # ok
