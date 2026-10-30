"""Phase 2 cross-reference validation script."""
import json, sys

mappings = json.load(open("data/api_mappings.json"))
specs    = json.load(open("data/target_api_specs.json"))
cases    = json.load(open("data/benchmark_cases.json"))

errors = []

# 1) Every mapping new_api has a target spec
mapping_new_apis = {m["new_api"] for m in mappings["mappings"]}
spec_apis        = {a["api_name"] for a in specs["apis"]}
missing_specs = mapping_new_apis - spec_apis
if missing_specs:
    errors.append(f"Missing specs for: {missing_specs}")
else:
    print("CHECK 1 PASS: Every new_api has a target spec")

# 2) Every migration_id in benchmark cases exists in mappings
mapping_ids = {m["migration_id"] for m in mappings["mappings"]}
for c in cases["cases"]:
    for mid in c["migration_ids"]:
        if mid not in mapping_ids:
            errors.append(f"Case {c['case_id']} references unknown {mid}")
if not any("references unknown" in e for e in errors):
    print("CHECK 2 PASS: All case migration_ids exist in mappings")

# 3) Every mapping has required fields
required = [
    "migration_id", "library", "old_version", "new_version",
    "old_api", "new_api", "old_signature", "new_signature",
    "argument_transformations", "description",
]
for m in mappings["mappings"]:
    for r in required:
        if r not in m:
            errors.append(f"{m['migration_id']} missing field {r}")
if not any("missing field" in e for e in errors):
    print("CHECK 3 PASS: All mappings have required fields")

# 4) Unique IDs
if len(mapping_ids) != len(mappings["mappings"]):
    errors.append("Duplicate mapping IDs")
case_ids = [c["case_id"] for c in cases["cases"]]
if len(set(case_ids)) != len(case_ids):
    errors.append("Duplicate case IDs")
if not any("Duplicate" in e for e in errors):
    print("CHECK 4 PASS: All IDs unique")

if errors:
    print("\nFAILURES:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("\nALL PHASE 2 CHECKS PASSED")
