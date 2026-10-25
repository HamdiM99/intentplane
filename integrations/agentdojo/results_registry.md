# Incomplete systems of record (worst-case replay, mandate prompt+known)

Each known counterparty is missing with probability f (3 seeds, mean). f = 1 is the strict mandate (only values written in the request).

## banking

| f (records missing) | Utility, no human | Utility, attentive human | Confirmations per legitimate run (attentive) | Attacks that succeed (attentive) |
|---|---|---|---|---|
| 0.0 | 9.0/16 (56%) | 15.0/16 (94%) | 0.62 | 0.0/144 |
| 0.1 | 9.0/16 (56%) | 15.0/16 (94%) | 0.62 | 0.0/144 |
| 0.25 | 9.0/16 (56%) | 15.0/16 (94%) | 0.69 | 0.0/144 |
| 0.5 | 9.0/16 (56%) | 15.0/16 (94%) | 0.77 | 0.0/144 |
| 0.75 | 9.0/16 (56%) | 15.0/16 (94%) | 0.81 | 0.0/144 |
| 1.0 | 9.0/16 (56%) | 15.0/16 (94%) | 0.88 | 0.0/144 |

## workspace

| f (records missing) | Utility, no human | Utility, attentive human | Confirmations per legitimate run (attentive) | Attacks that succeed (attentive) |
|---|---|---|---|---|
| 0.0 | 37.0/40 (92%) | 40.0/40 (100%) | 0.12 | 0.0/240 |
| 0.1 | 36.7/40 (92%) | 40.0/40 (100%) | 0.13 | 0.0/240 |
| 0.25 | 36.0/40 (90%) | 40.0/40 (100%) | 0.15 | 0.0/240 |
| 0.5 | 34.3/40 (86%) | 40.0/40 (100%) | 0.19 | 0.0/240 |
| 0.75 | 33.0/40 (82%) | 40.0/40 (100%) | 0.23 | 0.0/240 |
| 1.0 | 32.0/40 (80%) | 40.0/40 (100%) | 0.25 | 0.0/240 |

