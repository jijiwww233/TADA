"""Read-only export and report metrics for REAL text classification; no DB writes."""
from dataclasses import asdict
import json
from pathlib import Path
import re
import sqlite3

from .classification_backend import sanitized_document, classify_many_cached
from .collection_classification import CATEGORIES


def export_posts(database, *, platform="xhs", limit=24, post_ids=None, secrets=()):
    path = Path(database).resolve()
    if not path.is_file():
        raise ValueError("source database does not exist")
    if not 1 <= limit <= 200:
        raise ValueError("export limit must be 1..200")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        where, args = "platform=?", [platform]
        if post_ids:
            if any(isinstance(i, bool) or not isinstance(i, int) or i < 1 for i in post_ids):
                raise ValueError("post IDs must be positive integers")
            where += " AND id IN (" + ",".join("?" for _ in post_ids) + ")"
            args.extend(post_ids)
        posts = conn.execute("SELECT id,source_id,title,description,platform,is_video FROM posts WHERE " + where + " ORDER BY id LIMIT ?", [*args, limit]).fetchall()
        exported = []
        for post in posts:
            rows = conn.execute("""SELECT pi.position,a.status,a.subject,a.description,a.layout_json,a.style_json,a.visible_text
                FROM post_images pi LEFT JOIN analyses a ON a.image_id=pi.image_id
                WHERE pi.post_id=? ORDER BY pi.position""", (post["id"],)).fetchall()
            def parsed(text):
                try:
                    return json.loads(text) if text else None
                except (ValueError, TypeError):
                    return None
            images = []
            for row in rows:
                images.append({"subject": parsed(row["subject"]), "description": row["description"],
                               "layout": parsed(row["layout_json"]), "style": parsed(row["style_json"]),
                               "visible_text": row["visible_text"]} if row["status"] == "done" else {})
            document = sanitized_document({"post": {"title": post["title"], "description": post["description"],
                                          "platform": post["platform"], "is_video": bool(post["is_video"])}, "images": images}, secrets=secrets)
            key = post["source_id"] or f"local:{post['id']}"
            if not re.fullmatch(r"[A-Za-z0-9:_=\-]{1,160}", key):
                raise ValueError("work key is not a safe identifier")
            exported.append({"work_key": key, "platform": post["platform"], "input": document,
                             "coverage": {"images": len(rows), "analyzed_images": sum(r["status"] == "done" for r in rows)}})
        return exported
    finally:
        conn.close()


def load_ground_truth(data):
    if not isinstance(data, list):
        raise ValueError("ground truth must be a list")
    labels = {}
    for row in data:
        key, expected = row["work_key"], row.get("expected")
        if not isinstance(key, str) or key in labels or expected is not None and expected not in CATEGORIES:
            raise ValueError("invalid or duplicate ground truth label")
        labels[key] = expected
    return labels


def label_template(records):
    return [{"work_key": r["work_key"], "title": r["input"]["post"]["title"], "expected": None, "note": ""} for r in records]


def evaluate_predictions(records, backend, *, ground_truth=None, previous=None, high_confidence=.8):
    if not 0 <= high_confidence <= 1:
        raise ValueError("invalid high-confidence reporting threshold")
    labels = load_ground_truth(ground_truth or [])
    old = previous or {}
    documents = [r["input"] for r in records]
    results = classify_many_cached(documents, backend, previous=[old.get(r["work_key"]) for r in records])
    confusion = {truth: {p: 0 for p in CATEGORIES} for truth in CATEGORIES}
    cases, wrong = [], []
    for record, result in zip(records, results):
        expected = labels.get(record["work_key"])
        case = {**record, "expected": expected, "label_status": "待人工标注" if expected is None else "已标注",
                "prediction": result, "model_prediction": backend.recorded_prediction(record["input"])}
        if expected is not None:
            confusion[expected][result["category"]] += 1
            if result["category"] != expected and result["confidence"] >= high_confidence:
                wrong.append(record["work_key"])
        cases.append(case)
    labeled = sum(sum(row.values()) for row in confusion.values())
    per_category = {}
    for label in CATEGORIES:
        support = sum(confusion[label].values())
        predicted = sum(confusion[truth][label] for truth in CATEGORIES)
        correct = confusion[label][label]
        per_category[label] = {"support": support, "precision": correct / predicted if predicted else None,
                               "recall": correct / support if support else None}
    usage = asdict(backend.usage)
    tokens = None if usage["prompt_tokens"] is None and usage["completion_tokens"] is None else (
        (usage["prompt_tokens"] or 0) + (usage["completion_tokens"] or 0))
    usage["average_reported_tokens_per_requested_post"] = tokens / usage["posts_requested"] if tokens is not None and usage["posts_requested"] else None
    return {"evaluation_kind": "real_text_backend", "model": backend.settings.model,
            "classifier_version": backend.classifier_version, "usage": usage,
            "summary": {"posts": len(cases), "labeled": labeled, "unlabeled": len(cases) - labeled,
                        "accuracy": sum(confusion[c][c] for c in CATEGORIES) / labeled if labeled else None,
                        "uncertain_rate": sum(c["prediction"]["category"] == "uncertain" for c in cases) / len(cases) if cases else None,
                        "backend_errors": sum(c["prediction"]["status"] != "ok" for c in cases),
                        "valid_uncertain": sum(c["prediction"]["category"] == "uncertain" and c["prediction"]["status"] == "ok" for c in cases),
                        "category_counts": {label: sum(c["prediction"]["category"] == label for c in cases) for label in CATEGORIES}},
            "confusion": confusion, "per_category": per_category, "high_confidence_threshold_for_reporting_only": high_confidence,
            "high_confidence_wrong_work_keys": wrong, "cases": cases,
            "limitations": ["无人工标签时不计算准确率；固定后端成绩不作为真实成绩。",
                            "confidence未校准；未按阈值强制转uncertain。",
                            "token为批次实报usage的摊销，不是每帖独立计量；缺失usage不估算。",
                            "只使用已有文字和vision分析；没有重新看图、OCR或观看视频。"]}


def render_report(report):
    def cell(value): return str(value).replace("|", "\\|").replace("\n", " ")
    summary = report["summary"]
    lines = ["# 真实收藏：文本自动分类预测", "",
             f"模型：{report['model']}；版本：`{report['classifier_version']}`", "",
             f"帖子 {summary['posts']}；人工标签 {summary['labeled']}；待标注 {summary['unlabeled']}。", "",
             f"Accuracy: {summary['accuracy'] if summary['labeled'] else '待人工标注，不报告准确率'}", "",
             f"Uncertain rate: {summary['uncertain_rate']}; backend errors: {summary['backend_errors']}", "",
             f"API usage: {json.dumps(report['usage'], ensure_ascii=False)}", "",
             "错误占位 uncertain 与有效的模型弃权分开查看；uncertain 的策略 confidence 为 0，模型自报值见 JSON model_prediction。", "",
             "| Work key | 标题 | 分类 | confidence | status / error | 既有分析图/总图 | 人工标签 |", "|---|---|---|---|---|---|---|"]
    for row in report["cases"]:
        p, cov = row["prediction"], row["coverage"]
        model = row.get("model_prediction") or {}
        status = model.get("error", p["status"])
        lines.append(f"| {cell(row['work_key'])} | {cell(row['input']['post']['title'])} | {p['category']} | {p['confidence']} | {status} | {cov['analyzed_images']}/{cov['images']} | {row['expected'] or '待人工标注'} |")
    lines += ["", "## Confusion（仅已人工标注部分）", "", "| Expected | " + " | ".join(CATEGORIES) + " |", "|---|" + "---|" * 4]
    for truth in CATEGORIES:
        lines.append("| " + truth + " | " + " | ".join(str(report["confusion"][truth][p]) for p in CATEGORIES) + " |")
    lines += ["", "## Per-category", "", "| Category | Support | Precision | Recall |", "|---|---|---|---|"]
    for label, metrics in report["per_category"].items():
        lines.append(f"| {label} | {metrics['support']} | {metrics['precision']} | {metrics['recall']} |")
    lines += ["", "## 高支持度错分（仅已标注部分）", "", ", ".join(report["high_confidence_wrong_work_keys"]) or "暂无已标注错例；不代表没有错分。", "", "## Evidence", ""]
    for row in report["cases"]:
        lines.append(f"### {cell(row['work_key'])}: {cell(row['input']['post']['title'])}\n")
        for evidence in (row.get("model_prediction") or {}).get("evidence", []):
            lines.append(f"- 模型原文摘录 `{evidence['source']}`: {cell(evidence['text'])}")
        for evidence in row["prediction"]["evidence"]:
            lines.append(f"- `{evidence['field']}`: {cell(json.dumps(evidence['value'], ensure_ascii=False))}")
        lines.append("- " + ", ".join(row["prediction"]["reasons"]))
        lines.append("")
    lines += ["## Limitations", "", *["- " + note for note in report["limitations"]]]
    return "\n".join(lines).rstrip() + "\n"
