"""AgentGateway cost-catalog contracts."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "charts/agentgateway/files/catalog-overrides.json"

EXPECTED_OPENROUTER_RATES: dict[str, dict[str, str]] = {
    "anthropic/claude-opus-5": {
        "input": "5",
        "output": "25",
        "cacheRead": "0.5",
        "cacheWrite": "6.25",
    },
    "anthropic/claude-sonnet-5": {
        "input": "2",
        "output": "10",
        "cacheRead": "0.2",
        "cacheWrite": "2.5",
    },
    "deepseek/deepseek-v4-flash": {
        "input": "0.088606",
        "output": "0.177212",
        "cacheRead": "0.017721",
    },
    "deepseek/deepseek-v4-flash-0731": {
        "input": "0.14",
        "output": "0.28",
        "cacheRead": "0.028",
    },
    "deepseek/deepseek-v4-pro": {
        "input": "0.790308",
        "output": "1.580616",
        "cacheRead": "0.065859",
    },
    "deepseek/deepseek-v4-pro-0813": {
        "input": "1.122",
        "output": "3.366",
        "cacheRead": "0.0374",
    },
    "google/gemini-2.5-flash-lite": {
        "input": "0.1",
        "output": "0.4",
        "cacheRead": "0.01",
        "cacheWrite": "0.083333",
        "reasoning": "0.4",
        "inputAudio": "0.3",
    },
    "google/gemini-3-flash-preview": {
        "input": "0.5",
        "output": "3",
        "cacheRead": "0.05",
        "cacheWrite": "0.083333",
        "reasoning": "3",
        "inputAudio": "1",
    },
    "google/gemini-3.7-flash": {
        "input": "0.375",
        "output": "1.875",
        "cacheRead": "0.0375",
        "cacheWrite": "0.020833",
        "reasoning": "1.875",
        "inputAudio": "0.375",
    },
    "minimax/minimax-m3": {
        "input": "0.3",
        "output": "1.2",
        "cacheRead": "0.06",
    },
    "moonshotai/kimi-k3": {
        "input": "3",
        "output": "15",
        "cacheRead": "0.3",
    },
    "nvidia/nemotron-3-ultra-550b-a55b:free": {
        "input": "0",
        "output": "0",
    },
    "nvidia/nemotron-3.5-lightning:free": {
        "input": "0",
        "output": "0",
    },
    "openai/gpt-5.6-luna": {
        "input": "0.2",
        "output": "1.2",
        "cacheRead": "0.02",
        "cacheWrite": "0.25",
    },
    "openai/gpt-5.6-sol": {
        "input": "2",
        "output": "10",
        "cacheRead": "0.2",
        "cacheWrite": "2.5",
    },
    "poolside/laguna-s-2.1:free": {"input": "0", "output": "0"},
    "stealth/ox-alpha": {"input": "0", "output": "0"},
    "tencent/hy3": {
        "input": "0.132",
        "output": "0.528",
        "cacheRead": "0.033",
    },
    "xiaomi/mimo-v2.5": {
        "input": "0.14",
        "output": "0.28",
        "cacheRead": "0.0028",
    },
    "z-ai/glm-5.2": {
        "input": "1.19",
        "output": "3.74",
        "cacheRead": "0.221",
    },
}

EXPECTED_TIERS: dict[str, list[dict[str, Any]]] = {
    "openai/gpt-5.6-luna": [
        {
            "contextOver": 272000,
            "rates": {
                "input": "0.4",
                "output": "1.8",
                "cacheRead": "0.04",
                "cacheWrite": "0.5",
            },
        }
    ],
    "openai/gpt-5.6-sol": [
        {
            "contextOver": 272000,
            "rates": {
                "input": "4",
                "output": "15",
                "cacheRead": "0.4",
                "cacheWrite": "5",
            },
        }
    ],
}


class AgentGatewayCostCatalogTests(unittest.TestCase):
    """Keep reviewed OpenRouter pricing exact and complete."""

    def test_openrouter_rates_and_long_context_tiers_are_exact(self) -> None:
        catalog = json.loads(CATALOG.read_text(encoding="ascii"))
        openrouter_models = catalog["providers"]["openrouter"]["models"]

        self.assertEqual(set(openrouter_models), set(EXPECTED_OPENROUTER_RATES))
        self.assertEqual(
            {name: entry["rates"] for name, entry in openrouter_models.items()},
            EXPECTED_OPENROUTER_RATES,
        )
        self.assertEqual(
            {
                name: entry["tiers"]
                for name, entry in openrouter_models.items()
                if "tiers" in entry
            },
            EXPECTED_TIERS,
        )

    def test_all_rates_fit_agentgateway_decimal_precision(self) -> None:
        catalog = json.loads(CATALOG.read_text(encoding="ascii"))

        for provider_name, provider in catalog["providers"].items():
            for model_name, model in provider["models"].items():
                rate_sets = [model.get("rates", {})]
                rate_sets.extend(tier["rates"] for tier in model.get("tiers", []))
                for rates in rate_sets:
                    for rate_name, rate in rates.items():
                        fractional_digits = len(rate.partition(".")[2])
                        self.assertLessEqual(
                            fractional_digits,
                            6,
                            f"{provider_name}/{model_name} {rate_name} rate {rate!r}",
                        )


if __name__ == "__main__":
    unittest.main()
