"""Offline policy evaluation using recorded scoped backend claims, NOT model accuracy.

python -m app.classification_evaluation tests/fixtures/collection_categories_v1.json
    --output-dir classification_reports
"""
from copy import deepcopy
import json

from .collection_classification import (
    Assessment, Claim, Evidence, FixedBackend, CATEGORIES, classify_collection,
    input_hash, normalize_input, resolve_evidence,
)


def recorded_assessment(case):
    return Assessment(tuple(Claim(c["category"], c["confidence"], tuple(
        Evidence(e["field"], deepcopy(e["value"]), e["kind"]) for e in c["evidence"]))
        for c in case["backend_claims"]))


def audit_evidence(payload, result):
    document = normalize_input(payload)
    errors = []
    for entry in result["evidence"] + result["ignored_evidence"]:
        try:
            actual = resolve_evidence(document, entry["field"])
            source = "post" if entry["field"].startswith("post.") else entry["field"].split(".", 1)[0]
            if actual != entry["value"] or entry["source"] != source:
                raise ValueError("field/source mismatch")
        except (KeyError, IndexError, TypeError, ValueError):
            errors.append("evidence_not_traceable")
    if result["category"] != "uncertain" and not result["evidence"]:
        errors.append("supported_category_has_no_evidence")
    return errors


def evaluate_cases(fixture, *, backend_factory=None):
    if fixture.get("version") != "collection-fixtures/1":
        raise ValueError("unknown fixture version")
    ids = [case["id"] for case in fixture["cases"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case IDs")
    confusion = {truth: {predicted: 0 for predicted in CATEGORIES} for truth in CATEGORIES}
    rows = []
    for case in fixture["cases"]:
        truth = case["expected"]
        if truth not in CATEGORIES:
            raise ValueError("unknown expected label")
        backend = (backend_factory(case) if backend_factory else
                   FixedBackend({input_hash(case["input"]): recorded_assessment(case)}))
        before = deepcopy(case["input"])
        result = classify_collection(case["input"], backend)
        repeated = classify_collection(case["input"], backend)
        errors = audit_evidence(case["input"], result)
        if result != repeated:
            errors.append("nondeterministic_result")
        if case["input"] != before:
            errors.append("input_mutated")
        if result["status"] != "ok":
            errors.append("backend_error")
        predicted = result["category"]
        confusion[truth][predicted] += 1
        rows.append({"id": case["id"], "scenario": case["scenario"], "expected": truth,
                     "predicted": predicted, "label_match": predicted == truth,
                     "passed": predicted == truth and not errors, "errors": errors, "result": result})
    per_category = {}
    for label in CATEGORIES:
        support = sum(confusion[label].values())
        predicted_count = sum(confusion[truth][label] for truth in CATEGORIES)
        correct = confusion[label][label]
        per_category[label] = {"fixtures": support, "predicted": predicted_count, "correct": correct,
                               "precision": correct / predicted_count if predicted_count else None,
                               "recall": correct / support if support else None}
    return {"evaluation_kind": "fixed_backend_policy_regression",
            "warning": "Recorded claims test policy and evidence handling; not live model classification accuracy.",
            "summary": {"cases": len(rows), "passed": sum(r["passed"] for r in rows),
                        "failed": sum(not r["passed"] for r in rows)},
            "confusion": confusion, "per_category": per_category, "cases": rows}


def render_markdown(report):
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    lines = ["# 收藏分类离线策略评测", "", report["warning"], "",
             "固定后端提供人工编写的语义判断；以下只验证聚合、保守归类、证据和确定性，不证明真实模型质量。", "",
             f"Cases: {report['summary']['cases']}; passed: {report['summary']['passed']}; failed: {report['summary']['failed']}.", "",
             "## Confusion（行=预期，列=输出）", "",
             "| Expected | " + " | ".join(CATEGORIES) + " |",
             "|---|" + "---|" * len(CATEGORIES)]
    for label, values in report["confusion"].items():
        lines.append("| " + label + " | " + " | ".join(str(values[c]) for c in CATEGORIES) + " |")
    lines += ["", "## Per-category", "", "| Category | Fixtures | Predicted | Correct | Precision | Recall |", "|---|---|---|---|---|---|"]
    for label, m in report["per_category"].items():
        lines.append(f"| {label} | {m['fixtures']} | {m['predicted']} | {m['correct']} | {m['precision']} | {m['recall']} |")
    lines += ["", "## Per-case", "", "| ID | Scenario | Expected | Predicted | Result |", "|---|---|---|---|---|"]
    for row in report["cases"]:
        lines.append(f"| {row['id']} | {cell(row['scenario'])} | {row['expected']} | {row['predicted']} | {'PASS' if row['passed'] else 'FAIL'} |")
    lines += ["", "## Evidence and policy decisions", ""]
    for row in report["cases"]:
        result = row["result"]
        lines += [f"### {row['id']}: {cell(row['scenario'])}", "",
                  f"- {result['category']} / support={result['confidence']}; reasons={', '.join(result['reasons'])}"]
        for evidence in result["evidence"]:
            lines.append(f"- `{evidence['field']}` ({evidence['kind']}): {cell(json.dumps(evidence['value'], ensure_ascii=False))}")
        for evidence in result["ignored_evidence"]:
            lines.append(f"- Ignored `{evidence['field']}`: {evidence['reason']}")
        for limitation in result.get("limitations", []):
            lines.append("- " + limitation)
        if row["errors"]:
            lines.append("- Errors: " + ", ".join(row["errors"]))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _cli():
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate_cases(json.loads(args.fixture.read_text(encoding="utf-8")))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"]))
    return int(report["summary"]["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(_cli())
