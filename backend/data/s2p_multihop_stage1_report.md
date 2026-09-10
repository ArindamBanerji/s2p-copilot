# S2P Multihop Stage 1 Evaluation

[PLANTED / POSITIVE CONTROL] Results are not a measurement of real S2P VLD value.

## Acceptance Test

Headline comparison is VLD vs breadth on score_keyed scenarios. `content_rule` is an oracle because it reads authored correct branches directly.

| rho | SP | breadth | content_rule | VLD | d(VLD-breadth) | d(VLD-SP) |
|---:|---:|---:|---:|---:|---:|---:|
| 0.3 | 0.750 | 0.750 | 0.500 | 0.750 | 0.000 | 0.000 |
| 0.5 | 0.800 | 0.800 | 0.800 | 0.200 | -0.600 | -0.600 |
| 0.7 | 0.500 | 0.250 | 0.375 | 0.375 | 0.125 | -0.125 |
| 0.9 | 1.000 | 0.750 | 1.000 | 1.000 | 0.250 | 0.000 |
| 1.0 | 0.500 | 0.000 | 0.500 | 0.500 | 0.500 | 0.000 |

Acceptance test: **FAIL**

## Per-Kind Results

| Kind | SP | breadth | content_rule | VLD | N |
|---|---:|---:|---:|---:|---:|
| content_keyed | 0.600 | 0.600 | 0.600 | 0.600 | 15 |
| prerequisite | 0.700 | 0.600 | 0.900 | 0.900 | 10 |
| score_keyed | 0.680 | 0.480 | 0.600 | 0.520 | 25 |

## Controls

- Flat controls VLD <= SP: PASS (SP=0.600, VLD=0.600)
- rho=0.50 VLD near chance 0.20 +/- 0.15: PASS (VLD=0.200)

## Per-rho Table (score_keyed only)

| rho | N | SP | breadth | content_rule | VLD |
|---:|---:|---:|---:|---:|---:|
| 0.3 | 4 | 0.750 | 0.750 | 0.500 | 0.750 |
| 0.5 | 5 | 0.800 | 0.800 | 0.800 | 0.200 |
| 0.7 | 8 | 0.500 | 0.250 | 0.375 | 0.375 |
| 0.9 | 4 | 1.000 | 0.750 | 1.000 | 1.000 |
| 1.0 | 4 | 0.500 | 0.000 | 0.500 | 0.500 |

## Cross-Copilot Comparison

| Copilot | VLD at rho>=0.70 | SP at rho>=0.70 | Delta | Actions | Factors |
|---|---:|---:|---:|---:|---:|
| SOC | 1.000 | 0.250-0.500 | +0.500-0.750 | 4 | 6 |
| S2P | 0.562 | 0.625 | -0.062 | 5 | 7 |

Headline: S2P VLD at rho>=0.70 on score_keyed = **0.562**.

0 new regressions introduced.
