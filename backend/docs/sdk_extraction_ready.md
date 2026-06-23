# S2P BuyerOracle SDK Extraction Ready

Status: SDK_EXTRACTION_READY

The S2P BuyerOracle pipeline is ready for SDK extraction. The reusable
implementation lives in `app/oracle/` and the extraction marker is backed by
`tests/test_buyer_oracle_pipeline.py`.

Covered experiments:

- EXP1: known buyer hold-rate lift is recovered.
- EXP2: zero-lift control does not report a material signal.
- EXP3: Gaussian lower-bound sample size is computed for the lift floor.
- EXP4: positive hold lift with negative accuracy is rejected by the quality gate.
- EXP5: conditional holdout coverage stays near the expected effective rate.

Extraction shape:

- SOC uses unconditional per-alert holdout keyed by `campaign_context_shown`.
- S2P uses holdout conditional on enrichment availability, keyed by
  `enrichment_shown`.
- SDK extraction must absorb both shapes through a `HoldoutAssigner` protocol
  with an optional gate predicate, so domains can decide whether an entity is
  eligible before assignment.
