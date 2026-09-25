# S2P-FINAL implementation plan
1. Preserve the six Playwright specs and canonical scorer/audit actions; never manufacture confidence or accuracy gains.
2. Enable explicit demo operations only with `S2P_DEMO_MODE=true`; keep production scoring and freeze behavior unchanged.
3. In demo mode resolve the two existing special invoice fixtures by matching event/category/supplier; label their synthetic provenance.
4. Explain copper clause 7.3 only for its documented fixture contract; expose `accept` as its presentation alias only when the real scorer returns `auto_approve`.
5. Explain container demurrage versus working-capital timing using supplied context; retain the real scorer's recommendation.
6. Route the reserved `PW-DAY-ZERO-001` demo request to a disposable canonical ProfileScorer, with no writes, learning, or persistence-outbox access.
7. Keep extinction's existing live-store last-pending-outcome relationship; seed format compliance early, then stop, and report unrelated unresolved history honestly.
8. Expose guarded, read-only ambiguity candidates derived from live centroid boundaries; do not confuse high risk with low confidence.
9. Score these candidates and investigate using returned decision IDs/vectors before learning changes their confidence.
10. Add an explicitly confirmed demo re-freeze endpoint: archive the previous immutable snapshot, create a new immutable snapshot, atomically select it with provenance.
11. In demo mode compare live and selected frozen scorers on the same actual verified cohort; never clamp a negative/zero gap.
12. Extend preseed with bounded synthetic boundary-review learning after re-freeze and report whether actual divergence was achieved.
13. Test happy/guard/edge paths with real scorers and stores; run mypy after each Python edit and the complete backend suite.
14. Record measured results, unchanged frozen hashes, and any restart/seed prerequisites in session state.
