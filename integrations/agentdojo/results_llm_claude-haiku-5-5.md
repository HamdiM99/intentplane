# AgentDojo banking with a real LLM: `claude-haiku-5-5`, attack `important_instructions`

| Configuration | Utility (no attack) | Utility under attack | Attack success rate | Model resisted / gateway blocked / attack succeeded / tried, failed otherwise | Errors |
|---|---|---|---|---|---|
| undefended | 15/16 (93.8%) | 131/144 (91.0%) | **0/144 (0.0%)** | 144 / 0 / 0 / 0 | 0 |
| ip-prompt-nohuman | 9/16 (56.2%) | 81/144 (56.2%) | **0/144 (0.0%)** | 144 / 0 / 0 / 0 | 0 |
| ip-known-nohuman | 10/16 (62.5%) | 90/144 (62.5%) | **0/144 (0.0%)** | 144 / 0 / 0 / 0 | 0 |
| ip-known-attentive | 13/16 (81.2%) | 122/144 (84.7%) | **0/144 (0.0%)** | 144 / 0 / 0 / 0 | 0 |
