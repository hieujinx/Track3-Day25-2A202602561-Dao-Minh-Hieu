"""Run the controlled experiments required by reports/REPORT_TEMPLATE.md."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gameday_lab.client import ResilientClient
from gameday_lab.factory import build_client
from gameday_lab.sim.config import load_config
from gameday_lab.sim.engine import run_scenario


def _pick(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "availability", "latency_p95_s", "amplification", "retry_ratio", "recovery_s",
        "incompatible_routes", "faq_availability", "cache_hit_ratio", "stale_served",
        "wrong_answers", "wrong_answers_by_reason", "outdated_answers", "cost_per_good_turn",
        "give_up_reasons",
    )
    return {key: metrics[key] for key in keys if key in metrics}


def main() -> None:
    output: dict[str, Any] = {}
    cfg = load_config("configs/gameday.yaml", "configs/scenarios.yaml")

    budget_variants = {
        "budget_on": {},
        "budget_off": {
            "retry_budget_ratio": 100.0,
            "retry_budget_max_tokens": 1.0e9,
            "retry_budget_min_per_s": 1.0e6,
        },
    }
    for variant, values in budget_variants.items():
        output[variant] = {}
        for scenario in ("G2_overload_storm", "G3_brownout"):
            settings = deepcopy(cfg.client)
            settings["breaker"]["min_calls"] = 1_000_000_000
            settings["max_attempts_per_provider"] = 5
            settings.update(values)
            client = ResilientClient(cfg.provider_infos(), cfg.fallback_order, settings, seed=1)
            output[variant][scenario] = _pick(run_scenario(cfg, scenario, client).metrics())

    settings = deepcopy(cfg.client)
    settings["attempt_timeout_s"] = 30.0
    for scenario in ("G4_slow_region", "G6_provider_outage"):
        client = ResilientClient(cfg.provider_infos(), cfg.fallback_order, settings, seed=1)
        output.setdefault("timeout_30", {})[scenario] = _pick(run_scenario(cfg, scenario, client).metrics())

    class UnfilteredClient(ResilientClient):
        def ladder(self, task: str) -> list[str]:
            return list(self.order)

    client = UnfilteredClient(cfg.provider_infos(), cfg.fallback_order, deepcopy(cfg.client), seed=1)
    output["unfiltered_tools_G6"] = _pick(run_scenario(cfg, "G6_provider_outage", client).metrics())

    cache_cfg = load_config("configs/gameday.yaml", "configs/scenarios_cache.yaml")
    for threshold in (0.80, 0.90, 0.97):
        cache_cfg.cache.update({"similarity_threshold": threshold, "ttl_s": 60.0, "max_stale_s": 600.0})
        metrics = run_scenario(cache_cfg, "C1_faq_outage", build_client("cached", cache_cfg)).metrics()
        output.setdefault("cache_threshold", {})[str(threshold)] = _pick(metrics)

    for ttl in (30, 60, 300):
        cache_cfg.cache.update({"similarity_threshold": 0.90, "ttl_s": float(ttl), "max_stale_s": 600.0})
        metrics = run_scenario(cache_cfg, "C1_faq_outage", build_client("cached", cache_cfg)).metrics()
        output.setdefault("cache_ttl", {})[str(ttl)] = _pick(metrics)

    cache_cfg.cache.update({"similarity_threshold": 0.90, "ttl_s": 60.0, "max_stale_s": 0.0})
    metrics = run_scenario(cache_cfg, "C1_faq_outage", build_client("cached", cache_cfg)).metrics()
    output["cache_no_stale"] = _pick(metrics)

    destination = Path("reports/experiments.json")
    destination.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))
    print(f"wrote {destination}")


if __name__ == "__main__":
    main()
