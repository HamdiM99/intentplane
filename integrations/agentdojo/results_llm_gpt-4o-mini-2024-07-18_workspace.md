# AgentDojo workspace with a real LLM: `gpt-4o-mini-2024-07-18`, attack `important_instructions`

| Configuration | Utility (no attack) | Utility under attack | Attack success rate | Model resisted / gateway blocked / attack succeeded / tried, failed otherwise | Errors |
|---|---|---|---|---|---|
| undefended | 34/40 (85.0%) | 201/560 (35.9%) | **94/560 (16.8%)** | 273 / 0 / 94 / 193 | 0 |
| ip-known-nohuman | 31/40 (77.5%) | 314/560 (56.1%) | **6/560 (1.1%)** | 277 / 269 / 6 / 8 | 0 |
| ip-known-attentive | 33/40 (82.5%) | 309/549 (56.3%) | **7/549 (1.3%)** | 264 / 270 / 7 / 8 | 11 |
| tool_filter | 30/40 (75.0%) | 412/560 (73.6%) | **9/560 (1.6%)** | 545 / 0 / 9 / 6 | 0 |
| spotlighting | 33/40 (82.5%) | 230/560 (41.1%) | **81/560 (14.5%)** | 316 / 0 / 81 / 163 | 0 |
