# AgentDojo workspace with a real LLM: `claude-haiku-5-5`, attack `important_instructions`

| Configuration | Utility (no attack) | Utility under attack | Attack success rate | Model resisted / gateway blocked / attack succeeded / tried, failed otherwise | Errors |
|---|---|---|---|---|---|
| undefended | 37/40 (92.5%) | 511/560 (91.2%) | **0/560 (0.0%)** | 560 / 0 / 0 / 0 | 0 |
| ip-known-nohuman | 34/40 (85.0%) | 480/560 (85.7%) | **0/560 (0.0%)** | 560 / 0 / 0 / 0 | 0 |
| ip-known-attentive | 36/40 (90.0%) | 494/560 (88.2%) | **0/560 (0.0%)** | 560 / 0 / 0 / 0 | 0 |
