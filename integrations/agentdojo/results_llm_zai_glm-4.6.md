# AgentDojo banking with a real LLM: `zai:glm-4.6`, attack `important_instructions`

| Configuration | Utility (no attack) | Utility under attack | Attack success rate | Model resisted / gateway blocked / attack succeeded / tried, failed otherwise | Errors |
|---|---|---|---|---|---|
| undefended | 15/16 (93.8%) | 123/144 (85.4%) | **5/144 (3.5%)** | 139 / 0 / 5 / 0 | 0 |
| ip-known-attentive | 13/16 (81.2%) | 122/144 (84.7%) | **0/144 (0.0%)** | 141 / 3 / 0 / 0 | 0 |
