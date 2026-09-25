# S2P Score API Contract

## POST `/api/s2p/score`

The score endpoint evaluates one procurement event and returns the recommended
procurement action. It is mounted by the S2P backend at port 8002 in the local
demo environment.

### Request

Required fields:

```json
{
  "event_id": "S2P-INV-NNNN",
  "category": "price_variance",
  "amount": 22426.73,
  "supplier_id": "SUP-001"
}
```

`event_id` should identify an invoice or event from
`data/synthetic_invoices.json`. `amount` is numeric, and `supplier_id`
identifies the supplier.

Valid categories:

```text
price_variance
quantity_mismatch
duplicate_risk
contract_gap
format_compliance
```

Optional enrichment fields include `supplier_name`, `contract_id`,
`approved_categories`, supplier-risk and historical-spend values, vendor
decision counts, and the factor inputs `match_status`,
`amount_variance_ratio`, `duplicate_score`, `supplier_exception_history`,
`payment_terms_impact`, `commodity_index_correlation`,
`tax_regulatory_compliance`, and `environmental_risk`.

### Response (`200`)

The core response shape is:

```json
{
  "event_id": "S2P-INV-NNNN",
  "category": "price_variance",
  "action": "auto_approve",
  "action_index": 0,
  "confidence": 0.87,
  "probabilities": [0.87, 0.08, 0.03, 0.01, 0.01],
  "factor_vector": [0.7, 0.14, 0.06, 0.16, 0.86, 0.19, 0.59, 0.5],
  "factor_names": [
    "match_status",
    "amount_variance_ratio",
    "duplicate_score",
    "supplier_exception_history",
    "payment_terms_impact",
    "commodity_index_correlation",
    "tax_regulatory_compliance",
    "environmental_risk"
  ],
  "decision_id": "..."
}
```

Possible action values are `auto_approve`, `hold_for_review`,
`escalate_to_buyer`, `flag_leakage`, and `refer_to_specialist`.

The response may additionally contain `proposal_id`, `process_context`,
`active_variant`, `auto_approve`, `novelty_score`, `threshold_decision`,
`gate`, `conservation_status`, `evidence_tier`, `learning_applied`, `reason`,
and frozen-twin comparison data when available.

### Errors

- `422`: missing required field, non-numeric `amount`, or unknown category.
- `503`: score path unavailable or busy, including bounded per-invoice
  contention or unavailable scorer/graph state.

Unknown categories return a `422` detail naming the invalid category and the
valid category list. The score path may also return a held response when
conservation governance is not green; that is a valid score response rather
than a request-contract error.

## Stable demo payloads

The Playwright/demo payloads are stored in:

```text
data/demo_fixtures/pw_score_payloads.json
```

Each payload was verified against the live S2P backend and returned HTTP 200:

| Scenario | Event | Category | Amount | Supplier |
|---|---|---|---:|---|
| S2P-01 Rule Said No | S2P-INV-0001 | price_variance | 22426.73 | SUP-001 |
| S2P-02 Day Zero | S2P-INV-0002 | quantity_mismatch | 5000.00 | SUP-002 |
| S2P-03 Paying More | S2P-INV-0009 | duplicate_risk | 42303.00 | SUP-009 |
