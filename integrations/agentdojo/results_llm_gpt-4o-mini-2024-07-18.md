# AgentDojo banking with a real LLM: `gpt-4o-mini-2024-07-18`, attack `important_instructions`

| Configuration | Utility (no attack) | Utility under attack | Attack success rate | Model resisted / gateway blocked / attack succeeded / tried, failed otherwise | Errors |
|---|---|---|---|---|---|
| undefended | 9/16 (56.2%) | 60/144 (41.7%) | **69/144 (47.9%)** | 52 / 0 / 69 / 23 | 0 |
| ip-prompt-nohuman | 5/16 (31.2%) | 48/144 (33.3%) | **0/144 (0.0%)** | 56 / 88 / 0 / 0 | 0 |
| ip-known-nohuman | 5/16 (31.2%) | 46/144 (31.9%) | **0/144 (0.0%)** | 55 / 89 / 0 / 0 | 0 |
| ip-known-attentive | 8/16 (50.0%) | 59/144 (41.0%) | **0/144 (0.0%)** | 55 / 89 / 0 / 0 | 0 |
| tool_filter | 7/16 (43.8%) | 62/144 (43.1%) | **23/144 (16.0%)** | 98 / 0 / 23 / 23 | 0 |
| spotlighting | 8/16 (50.0%) | 57/144 (39.6%) | **71/144 (49.3%)** | 54 / 0 / 71 / 19 | 0 |
| repeat_user_prompt | 7/16 (43.8%) | 71/144 (49.3%) | **26/144 (18.1%)** | 105 / 0 / 26 / 13 | 0 |
| pi_detector | 7/16 (43.8%) | 47/144 (32.6%) | **16/144 (11.1%)** | 122 / 0 / 16 / 6 | 0 |
