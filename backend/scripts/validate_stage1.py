#!/usr/bin/env python3
import json, collections, sys

d = json.load(open("data/s2p_multihop_stage1.json"))
S = d["scenarios"]
fails, warns = [], []
def chk(cond, msg):
    if not cond: fails.append(msg)

ACTIONS = {"approve", "dispute", "partial_approve", "hold_for_review", "escalate_to_procurement"}
CATS = {"price_mismatch", "quantity_mismatch", "supplier_pattern", "duplicate_suspect", "compliance_risk"}
FACTORS = {"receipt_match", "price_conformance", "supplier_trust", "duplicate_risk",
           "policy_conformance", "amount_materiality", "contract_coverage"}

KIND = {"S2P-MH-001": "score_keyed", "S2P-MH-002": "score_keyed", "S2P-MH-003": "score_keyed",
        "S2P-MH-004": "content_keyed", "S2P-MH-005": "content_keyed", "S2P-MH-009": "score_keyed",
        "S2P-MH-007": "prerequisite", "S2P-MH-008": "prerequisite"}

chk(len(S) == 50, f"count is {len(S)}, expected 50")
ids = [x["scenario_id"] for x in S]
chk(len(set(ids)) == 50, "duplicate scenario_ids")

flat = [x for x in S if x["surface_only_resolvable"]]
nonflat = [x for x in S if not x["surface_only_resolvable"]]
budget_lt_sum = 0

for x in S:
    sid = x["scenario_id"]
    # 1 branching_kind matches template classification
    stem = sid.rsplit("-", 1)[0]
    if stem in KIND:
        chk(x["branching_kind"] == KIND[stem], f"{sid}: kind {x['branching_kind']} != {KIND[stem]}")
    # 2 rho set correctly
    if x["branching_kind"] in ("content_keyed", "prerequisite"):
        chk(x["rho_planted"] == 1.00, f"{sid}: non-score_keyed rho={x['rho_planted']}, expected 1.0")
    else:
        chk(x["rho_planted"] in (0.30, 0.50, 0.70, 0.90, 1.00), f"{sid}: bad rho {x['rho_planted']}")
    # 3 budget vs cost
    tot = sum(x["read_costs"].values())
    if x["budget"] < tot: budget_lt_sum += 1
    # 6 graph consistency
    nodes = {n["id"] for n in x["graph_nodes"]}
    for e in x["graph_edges"]:
        chk(e["from"] in nodes and e["to"] in nodes, f"{sid}: dangling edge {e}")
    chk(len(nodes) == len(x["graph_nodes"]), f"{sid}: duplicate node ids")
    # 7 factors in range + complete
    sf = x["alert"]["surface_factors"]
    chk(set(sf) == FACTORS, f"{sid}: factor key mismatch")
    for k, v in sf.items():
        chk(0 <= v <= 1, f"{sid}: factor {k}={v} out of range")
    # actions / categories valid
    chk(x["decision_tree"]["ground_truth_action"] in ACTIONS, f"{sid}: bad action")
    chk(x["alert"]["category"] in CATS, f"{sid}: bad category {x['alert']['category']}")
    for a in x["alternative_branches"]:
        chk(a["ground_truth_action"] in ACTIONS, f"{sid}: bad alt action")
    # forbidden single-pass fields
    for f in ("single_pass_action", "single_pass_correct", "why_single_pass_fails", "single_pass_analysis"):
        chk(f not in x, f"{sid}: forbidden field {f}")
    # why_* present per kind
    key = {"score_keyed": "why_score_keyed", "content_keyed": "why_content_keyed",
           "prerequisite": "why_prerequisite"}[x["branching_kind"]]
    chk(key in x, f"{sid}: missing {key}")
    # correct subset of available; hop numbering
    for h, cs in x["correct_branches"].items():
        chk(h in x["available_branches"], f"{sid}: correct hop {h} not in available")
        chk(set(cs) <= set(x["available_branches"][h]), f"{sid}: correct not subset at hop {h}")
    for b in x["misleading_branches"]:
        chk(any(b in v for v in x["available_branches"].values()), f"{sid}: misleading {b} not an available branch")
    for b, c in x["read_costs"].items():
        chk(any(b in v for v in x["available_branches"].values()), f"{sid}: cost for unknown branch {b}")
    steps = [h["step"] for h in x["decision_tree"]["hops"]]
    chk(steps == list(range(1, len(steps) + 1)), f"{sid}: hop steps not 1..n")
    for h in x["decision_tree"]["hops"]:
        chk(h["factor_enriched"] in FACTORS, f"{sid}: bad factor_enriched")
        chk(0 <= h["factor_new_value"] <= 1, f"{sid}: factor_new_value out of range")
        chk(h["narration"].endswith("."), f"{sid}: narration not a sentence")

# 3 aggregate
pct = 100.0 * budget_lt_sum / len(S)
chk(pct >= 60, f"budget<sum only {pct:.0f}% of instances (need >=60)")

# 4 at least one misleading branch per template
for stem in KIND:
    grp = [x for x in S if x["scenario_id"].startswith(stem)]
    chk(any(x["misleading_branches"] for x in grp), f"{stem}: no misleading branch anywhere")

# 5 alternative_branches >= 2 distinct actions (non-flat only)
for x in nonflat:
    acts = {a["ground_truth_action"] for a in x["alternative_branches"]}
    chk(len(acts) >= 2, f"{x['scenario_id']}: alt branches have {len(acts)} distinct actions")

# 8 >=4 distinct leaf actions across each template's 5 variations
for stem in KIND:
    grp = [x for x in S if x["scenario_id"].startswith(stem)]
    chk(len(grp) == 5, f"{stem}: {len(grp)} variations, expected 5")
    acts = {x["decision_tree"]["ground_truth_action"] for x in grp}
    chk(len(acts) == 5, f"{stem}: only {len(acts)} distinct actions {sorted(acts)} (DataOps has 5 actions)")
    # v1-v3 different branches
    v13 = [x["decision_tree"]["ground_truth_action"] for x in grp[:3]]
    chk(len(set(v13)) == 3, f"{stem}: v1-v3 actions not distinct {v13}")
    # v4 rho differs from v1-v3 (score_keyed only)
    if KIND[stem] == "score_keyed":
        chk(grp[3]["rho_planted"] not in {g["rho_planted"] for g in grp[:3]},
            f"{stem}: v4 rho not distinct from v1-v3")

# 9 flat controls
chk(len(flat) == 5, f"{len(flat)} flat controls, expected 5")
for x in flat:
    chk(x["budget"] == 0 and x["decision_tree"]["hops"] == [], f"{x['scenario_id']}: flat control not empty")
    chk(x["available_branches"] == {}, f"{x['scenario_id']}: flat control has branches")

# 10 rho=0.50 controls
r50 = [x for x in S if x["scenario_id"].startswith("S2P-CTRL-RHO50")]
chk(len(r50) == 5, f"{len(r50)} rho=0.50 controls, expected 5")
for x in r50:
    chk(x["rho_planted"] == 0.50, f"{x['scenario_id']}: rho != 0.50")
    chk(all(v == 0.50 for v in x["alert"]["surface_factors"].values()),
        f"{x['scenario_id']}: surface factors not all 0.50")
    chk(x["branching_kind"] == "score_keyed", f"{x['scenario_id']}: not score_keyed")

# spec distribution over score_keyed
sk = [x for x in S if x["branching_kind"] == "score_keyed"]
rho_dist = collections.Counter(x["rho_planted"] for x in sk)
need = {0.30: 2, 0.50: 5, 0.70: 4, 0.90: 2, 1.00: 2}
for r, n in need.items():
    if r == 0.50:
        chk(rho_dist[r] == n, f"rho=0.50 count {rho_dist[r]}, spec says exactly {n}")
    else:
        chk(rho_dist[r] >= n, f"rho={r} count {rho_dist[r]} < {n}")

kinds = collections.Counter(x["branching_kind"] for x in S)
sk_share = 100.0*kinds["score_keyed"]/len(S)
chk(sk_share >= 40, f"score_keyed share {sk_share:.0f}% < 40% spec floor")
margin = kinds["score_keyed"] - int(0.40*len(S))
chk(margin >= 5, f"only {margin} score_keyed instances of margin above the floor (need >=5 buffer)")
hops = collections.Counter(len(x["decision_tree"]["hops"]) for x in S)

print("instances:", len(S))
print("kinds:", dict(kinds))
print("score_keyed rho:", dict(sorted(rho_dist.items())))
print("hop-count dist:", dict(sorted(hops.items())))
print(f"budget<sum: {budget_lt_sum}/{len(S)} ({pct:.0f}%)")
print("actions:", dict(collections.Counter(x['decision_tree']['ground_truth_action'] for x in S)))
print()
if fails:
    print("FAILURES:")
    for f in fails: print("  -", f)
    sys.exit(1)
print("ALL 10 QUALITY CHECKS + SPEC CONSTRAINTS PASSED")
