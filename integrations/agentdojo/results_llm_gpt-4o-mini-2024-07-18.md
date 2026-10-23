# AgentDojo banking with a real LLM: `gpt-4o-mini-2024-07-18`, attack `important_instructions`

| Configuration | Utility (no attack) | Utility under attack | Attack success rate | Model resisted / gateway blocked / attack succeeded / tried, failed otherwise | Errors |
|---|---|---|---|---|---|
| undefended | 9/16 (56.2%) | 60/144 (41.7%) | **69/144 (47.9%)** | 52 / 0 / 69 / 23 | 0 |
| ip-known-attentive | 8/16 (50.0%) | 59/144 (41.0%) | **0/144 (0.0%)** | 54 / 87 / 0 / 3 | 0 |
