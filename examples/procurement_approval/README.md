# Procurement Approval — S2P Copilot Worked Example

Demonstrates the Compounding Intelligence Platform applied to
procurement risk scoring. Same GAE engine as SOC Copilot —
different domain config, different factor computers.

## The 10 Scenarios

| # | Supplier | Risk Profile | Expected Action |
|---|---|---|---|
| 1 | TechCorp SaaS | Low risk, established vendor | approve |
| 2 | StartupCo | High concentration risk | defer |
| 3 | OffshoreManuf | Compliance + geo risk | reject |
| 4 | RegionalBank | Financial health concern | defer |
| 5 | CriticalSole | Single source dependency | defer |
| 6 | GreenChem | ESG compliance risk | defer |
| 7 | FastTrack | Process bypass attempt | reject |
| 8 | EstablishedERP | Renewal, known vendor | approve |
| 9 | NewEntrant | No track record | defer |
| 10 | EmergencyPO | Crisis procurement | approve |

## Actual API Schema

POST /api/s2p/score requires:
- event_id, category, amount, supplier_id
- supplier_risk_rating (0=high risk, 1=low risk)
- approved_categories, contract_id (optional)
- vendor_decisions, vendor_approvals (for trust score)

S2P Categories: maverick_spend, supplier_risk, contract_breach,
                budget_overrun, approval_bypass, data_quality

S2P Actions: approve, escalate, reject, review

The scenario factors (financial_health, compliance_score, etc.)
are mapped to the API fields in run_scenarios.py.

## Running the Example

  cd examples/procurement_approval
  python run_scenarios.py

## Key Point

The same ProfileScorer, IKS service, and conservation law
that power SOC alert triage power this procurement decision.
"Same engine. Different domain. One conservation law."
