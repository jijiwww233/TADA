"""Train and evaluate the desktop-library concept reranker from reviewed image IDs."""
import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np

from app import config
from app.learned_find import LearnedFind, clean_query
from app.similarity import ImageSimilarity
from app.embedding.search_application import readonly_connection


def members(text):
    for token in text.split(","):
        left, _, right = token.partition("-")
        yield from range(int(left), int(right or left) + 1)


def fit(matrix, labels):
    mean = matrix.mean(axis=0)
    bias = labels.mean(axis=0)
    centered = matrix - mean
    # L2 regularization on all concept heads, with a shared index and independent offsets.
    dual = np.linalg.solve(centered @ centered.T + .10 * np.eye(len(matrix)), labels - bias)
    weights = centered.T @ dual
    predicted = centered @ weights + bias
    floor = []
    for target in range(labels.shape[1]):
        positives = predicted[labels[:, target] == 1, target]
        negatives = predicted[labels[:, target] == 0, target]
        floor.append(float((np.quantile(positives, .10) + np.quantile(negatives, .97)) / 2)
                     if len(positives) and len(negatives) else 2.0)
    return mean, weights, bias, np.asarray(floor)


def post_unique(ranked, posts):
    seen = set()
    selected = []
    for image_id in ranked:
        post = posts[image_id]
        if post not in seen:
            selected.append(image_id)
            seen.add(post)
    return selected


def summarize(results):
    return dict(evaluated=len(results),
                hit_at_1=sum(bool(r and r[0]) for r in results),
                hit_at_3=sum(any(r[:3]) for r in results),
                first_three_correct=sum(sum(r[:3]) for r in results),
                first_three_returned=sum(min(len(r), 3) for r in results))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", type=Path, default=Path("training/find_concepts.json"))
    parser.add_argument("--baseline-url", default="http://127.0.0.1:5199/api/search")
    args = parser.parse_args()
    concepts = json.loads(args.labels.read_text(encoding="utf-8-sig"))["concepts"]
    service = ImageSimilarity()
    ids, matrix = service.index_vectors()
    with readonly_connection(config.db_path()) as conn:
        rows = conn.execute("SELECT id,post_id,sha256 FROM images").fetchall()
        posts = {row["id"]: row["post_id"] for row in rows}
        reviewed = {str(row["id"]): row["sha256"] for row in rows}
    image_ids = set(map(int, ids))
    if image_ids != set(posts):
        raise SystemExit("Build the complete visual index before training")
    positives = [set(members(group["positive"])) for group in concepts]
    if any(not group or group - image_ids for group in positives):
        raise SystemExit("Training labels do not match the indexed images")
    labels = np.asarray([[image_id in group for group in positives] for image_id in ids], dtype=float)
    aliases = [(alias, i) for i, group in enumerate(concepts) for alias in group["train"]]
    train_words = {clean_query(alias) for alias, _ in aliases}
    if any(clean_query(query) in train_words for group in concepts for query in group["test"]):
        raise SystemExit("A held query duplicates a training phrase")
    mean, weights, bias, floor = fit(matrix, labels)
    payload = {
        "version": 1, "encoder": service.encoder.identity,
        "mean": mean.tolist(), "weights": weights.tolist(), "bias": bias.tolist(), "floor": floor.tolist(),
        "aliases": aliases, "reviewed": reviewed, "alias_vectors": service.encoder.encode_texts([alias for alias, _ in aliases]),
    }
    trained = LearnedFind(payload)

    baseline, candidate, evaluations = [], [], []
    for i, group in enumerate(concepts):
        for query in group["test"]:
            request = Request(args.baseline_url, data=json.dumps({"query": query}).encode(),
                              headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=20) as response:
                original = [hit["image_id"] for hit in json.load(response)["items"]]
            target = trained.concept(query, service.encoder)
            updated = post_unique([image_id for image_id, _ in trained.rank(target, ids, matrix)], posts) if target is not None else []
            # Match the server behavior when the newly trained route cannot make a confident choice.
            if not updated:
                updated = original
            baseline.append([image_id in positives[i] for image_id in original])
            candidate.append([image_id in positives[i] for image_id in updated])
            evaluations.append({"query": query, "concept": group["name"], "routed_to": concepts[target]["name"] if target is not None else None,
                                "baseline_top3": original[:3], "trained_top3": updated[:3]})

    # Keep whole posts in a fold. Only grade concepts that still occur on both sides.
    grouped_base, grouped_model = [], []
    unique_posts = sorted(set(posts.values()))
    fold_by_post = {post: order % 4 for order, post in enumerate(unique_posts)}
    label_vectors = np.asarray(service.encoder.encode_texts([g["train"][0] for g in concepts]))
    for fold in range(4):
        mask = np.asarray([fold_by_post[posts[int(image_id)]] == fold for image_id in ids])
        mean_oof, weights_oof, bias_oof, _ = fit(matrix[~mask], labels[~mask])
        estimates = (matrix[mask] - mean_oof) @ weights_oof + bias_oof
        text_estimates = matrix[mask] @ label_vectors.T
        held_ids = ids[mask]
        for i in range(len(concepts)):
            if not (labels[mask, i].any() and labels[~mask, i].any()):
                continue
            base = [int(held_ids[j]) for j in text_estimates[:, i].argsort()[::-1]]
            tuned = [int(held_ids[j]) for j in estimates[:, i].argsort()[::-1]]
            grouped_base.append([image_id in positives[i] for image_id in post_unique(base, posts)])
            grouped_model.append([image_id in positives[i] for image_id in post_unique(tuned, posts)])

    model = config.data_dir() / "models" / "learned-find.json"
    report = model.with_name("learned-find-report.json")
    outcome = {
        "images": len(ids), "concepts": len(concepts), "training_query_aliases": len(aliases),
        "image_concept_pairs": int(labels.size), "positive_pairs": int(labels.sum()),
        "held_query_baseline": summarize(baseline), "held_query_trained": summarize(candidate),
        "held_post_raw_clip": summarize(grouped_base), "held_post_trained": summarize(grouped_model),
        "queries": evaluations,
    }
    model.with_suffix(".pending").write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    report.write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in outcome.items() if k != "queries"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
