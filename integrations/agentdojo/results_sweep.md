# Noisy human and ablations (worst-case replay, mandate prompt+known)

## banking: noisy human (q = 0.1, 3 seeds, mean)

| p (approves an operation the user did not ask for) | Utility | Attacks that succeed |
|---|---|---|
| 0.0 | 14.0/16 (88%) | 0.0/144 (0.0%) |
| 0.05 | 14.0/16 (88%) | 4.7/144 (3.2%) |
| 0.1 | 14.0/16 (88%) | 11.3/144 (7.9%) |
| 0.2 | 14.0/16 (88%) | 18.3/144 (12.7%) |
| 0.5 | 14.0/16 (88%) | 47.0/144 (32.6%) |
| 1.0 | 14.0/16 (88%) | 111.0/144 (77.1%) |

## banking: ablations (no human)

| Gateway | Utility | Attacks that succeed |
|---|---|---|
| full gateway | 9/16 | 0/144 (0.0%) |
| without resolution | 10/16 | 96/144 (66.7%) |
| without budget | 9/16 | 0/144 (0.0%) |
| without approval levels | 14/16 | 15/144 (10.4%) |
| without all three | 15/16 | 127/144 (88.2%) |

## workspace: noisy human (q = 0.1, 3 seeds, mean)

| p (approves an operation the user did not ask for) | Utility | Attacks that succeed |
|---|---|---|
| 0.0 | 39.7/40 (99%) | 0.0/240 (0.0%) |
| 0.05 | 39.7/40 (99%) | 11.7/240 (4.9%) |
| 0.1 | 39.7/40 (99%) | 23.3/240 (9.7%) |
| 0.2 | 39.7/40 (99%) | 39.3/240 (16.4%) |
| 0.5 | 39.7/40 (99%) | 97.0/240 (40.4%) |
| 1.0 | 39.7/40 (99%) | 240.0/240 (100.0%) |

## workspace: ablations (no human)

| Gateway | Utility | Attacks that succeed |
|---|---|---|
| full gateway | 37/40 | 0/240 (0.0%) |
| without resolution | 38/40 | 160/240 (66.7%) |
| without budget | 37/40 | 0/240 (0.0%) |
| without approval levels | 39/40 | 40/240 (16.7%) |
| without all three | 40/40 | 240/240 (100.0%) |

