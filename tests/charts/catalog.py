"""Synthetic catalog inputs, not fetched provider data."""


def catalog(name="remote/openrouter/acme/model", upstream="acme/model"):
    return {
        "enabled": True,
        "excludedModels": [],
        "grantToAccessGroups": False,
        "models": [
            {
                "name": name,
                "upstreamModel": upstream,
                "label": "Friendly Model",
                "group": "Remote-OpenRouter-Acme",
            }
        ],
    }


def agent_values(openrouter, client_models=None):
    return {
        "openrouterCatalog": openrouter,
        "guardrails": {
            "llmPolicyEngine": {
                "enabled": True,
                "models": client_models or [],
                "localTarget": {
                    "name": "local-fallback",
                    "model": "local-model",
                    "provider": "Custom",
                    "custom": {"formats": [{"type": "Completions"}]},
                },
            }
        },
        "infraAgentgatewayWrapper": {
            "llamacpp": {"enabled": True, "host": "ollama.test", "port": 11434}
        },
        "authKeycloak": {"agentgatewayClientRoles": ["llm:invoke"]},
        "monitorPiiEngine": {"policy": {"routing": {"targets": []}}},
    }
