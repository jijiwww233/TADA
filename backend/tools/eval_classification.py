"""Read-only export, optional real TEXT inference, manual labels and JSON/MD reports.

python tools/eval_classification.py --db ... --env-file ... --limit 20
    --output-dir classification_live_reports --run
Without --run this exports sanitized inputs and a label template, with NO model call.
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--platform", default="xhs")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--post-ids", default="")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ground-truth", type=Path)
    parser.add_argument("--resume-report", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--max-requests", type=int, default=8)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=False)
    from app.classification_backend import ClassificationSettings, OpenAITextClassificationBackend
    from app.classification_live_evaluation import export_posts, label_template, evaluate_predictions, render_report
    records = export_posts(args.db, platform=args.platform, limit=args.limit,
                           post_ids=[int(i) for i in args.post_ids.split(",") if i] or None,
                           secrets=(os.environ.get("AI_API_KEY", ""),))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    def write(name, value):
        (args.output_dir / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write("inputs.json", records)
    # Never overwrite a user's labels when re-running the evaluator.
    if not (args.output_dir / "ground_truth.json").exists():
        write("ground_truth.json", label_template(records))
    if not args.run:
        print(json.dumps({"exported": len(records), "inference": "not_requested"}))
        return 0
    settings = ClassificationSettings.from_env()
    from dataclasses import replace
    settings = replace(settings, max_requests=args.max_requests, **({"model": args.model} if args.model else {}))
    ground_truth = json.loads(args.ground_truth.read_text(encoding="utf-8")) if args.ground_truth else None
    previous = {}
    if args.resume_report:
        previous = {r["work_key"]: r["prediction"] for r in json.loads(args.resume_report.read_text(encoding="utf-8"))["cases"]}
    with OpenAITextClassificationBackend(settings) as backend:
        report = evaluate_predictions(records, backend, ground_truth=ground_truth, previous=previous)
    write("report.json", report)
    (args.output_dir / "report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "usage": report["usage"]}, ensure_ascii=False))
    return int(report["summary"]["backend_errors"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
