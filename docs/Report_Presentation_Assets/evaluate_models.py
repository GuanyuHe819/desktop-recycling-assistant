"""Replay the same local images through both exported Keras classifiers."""
import csv
import hashlib
import json
import sys
import time
from collections import Counter
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import tensorflow as tf
from PIL import Image, ImageOps

OUTPUT = Path(__file__).resolve().parent
ROOT = OUTPUT.parents[1]
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "computer"))
from app import load_classifier

MATERIALS = ["paper", "plastic", "metal", "cardboard", "textile"]
LABELS = MATERIALS + ["empty"]
EXPORTS = {"baseline": "2026-11-18-dataset-baseline", "augmented": "2026-11-22-my-samples"}
COHORTS = ["public_candidate", "supplement_replay", "empty_replay"]
THRESHOLDS = [.70, .80, .85, .90, .95]
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def images(folder):
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)


@lru_cache(maxsize=None)
def sha(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def decode(path):
    # imdecode handles non-ASCII Windows paths and follows OpenCV's camera BGR convention.
    frame = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError(f"Could not decode {path.relative_to(ROOT)}")
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def dhash(path):
    thumb = np.asarray(decode(path).convert("L").resize((9, 8), Image.Resampling.LANCZOS))
    return int.from_bytes(np.packbits(thumb[:, 1:] > thumb[:, :-1]).tobytes(), "big")


def prepare(path):
    crop = ImageOps.fit(decode(path), (224, 224), method=Image.Resampling.LANCZOS)
    return np.asarray(crop).astype(np.float32) / 127.5 - 1.0


def make_cohorts():
    selected = DATA / "TeachableMachine_五类_初筛"
    public = DATA / "RealWaste_五类候选_未筛选"
    supplemental = DATA / "补充"
    empty = images(DATA / "empty_需要自行拍摄")
    training_folders = [p for label in MATERIALS for folder in (selected, supplemental) for p in images(folder / label)]
    with (selected / "来源清单.csv").open(encoding="utf-8-sig", newline="") as source:
        manifest = list(csv.DictReader(source))
    for row in manifest:
        path = selected / row["class"] / row["original_filename"]
        if sha(path) != row["sha256"].lower():
            raise ValueError(f"Selected RealWaste file differs from ID manifest: {path.name}")
    known_hashes = {sha(p) for p in training_folders + empty}
    selected_keys = {(r["class"], r["original_filename"]) for r in manifest}
    # 64-bit difference hash, across labels, screened against the known exposed-image folders.
    exposure_dhashes = {dhash(p) for p in training_folders + empty}
    inspected_public = set()
    items, excluded = [], []
    for label in MATERIALS:
        for path in images(public / label):
            digest = sha(path)
            if (label, path.name) in selected_keys or digest in known_hashes:
                reason = "documented_selection_or_exact_exposure"
            elif digest in inspected_public:
                reason = "exact_duplicate_within_public_pool"
            else:
                code = dhash(path)
                reason = "near_duplicate_to_exposure_dhash_le4" if any(
                    (h ^ code).bit_count() <= 4 for h in exposure_dhashes
                ) else ""
                inspected_public.add(digest)
            if reason:
                excluded.append({"label": label, "file": path.relative_to(ROOT).as_posix(), "reason": reason})
            else:
                items.append(("public_candidate", label, path))
        items.extend(("supplement_replay", label, p) for p in images(supplemental / label))
    items.extend(("empty_replay", "empty", p) for p in empty)
    return items, excluded


def write_csv(filename, rows):
    with (OUTPUT / filename).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def ratio(n, d):
    return float(n / d) if d else None


def metrics(rows, labels):
    truth = np.array([LABELS.index(r["label"]) for r in rows])
    predicted = np.array([LABELS.index(r["prediction"]) for r in rows])
    scores = np.array([r["score"] for r in rows])
    matrix = np.zeros((6, 6), dtype=int)
    np.add.at(matrix, (truth, predicted), 1)
    class_results = {}
    for label in labels:
        i = LABELS.index(label)
        support = int(matrix[i, :].sum())
        tp = int(matrix[i, i])
        class_results[label] = {"n": support, "correct": tp, "recall": ratio(tp, support),
                                "precision": ratio(tp, matrix[:, i].sum()),
                                "f1": ratio(2 * tp, support + matrix[:, i].sum())}
    sweeps = []
    correct = predicted == truth
    for threshold in THRESHOLDS:
        accepted = (scores >= threshold) & (predicted != LABELS.index("empty"))
        n_accepted = int(accepted.sum())
        n_correct = int((accepted & correct).sum())
        n_empty = int(((scores >= threshold) & (predicted == LABELS.index("empty"))).sum())
        sweeps.append({"threshold": threshold, "accepted_material": n_accepted,
                       "accepted_correct": n_correct, "accepted_wrong": n_accepted - n_correct,
                       "accepted_empty": n_empty, "coverage": ratio(n_accepted, len(rows)),
                       "accuracy_on_accepted": ratio(n_correct, n_accepted),
                       "correct_material_yield": ratio(n_correct, len(rows))})
    return {"n": len(rows), "correct": int(correct.sum()), "accuracy": float(correct.mean()),
            "macro_f1": float(np.mean([r["f1"] for r in class_results.values()])),
            "classes": class_results, "thresholds": sweeps, "confusion": matrix.tolist()}


def main():
    start = time.perf_counter()
    items, excluded = make_cohorts()
    print("Cohorts:", Counter(cohort for cohort, _, _ in items), flush=True)
    print("Public exclusions:", Counter(row["reason"] for row in excluded), flush=True)
    write_csv("public_exclusions.csv", excluded)
    predictors, hashes = {}, {}
    for tag, directory in EXPORTS.items():
        model_path = ROOT / "models" / directory / "keras_model.h5"
        labels_path = model_path.with_name("labels.txt")
        model, order = load_classifier(model_path, labels_path, MATERIALS)
        predictors[tag] = (tf.function(model, reduce_retracing=True), order)
        hashes[tag] = {"folder": directory, "weights_sha256": sha(model_path), "labels_sha256": sha(labels_path)}
    rows = []
    batch_size = 24
    for first in range(0, len(items), batch_size):
        batch = items[first:first + batch_size]
        inputs = np.stack([prepare(path) for _, _, path in batch])
        for tag, (predict, order) in predictors.items():
            probabilities = predict(inputs, training=False).numpy()
            if probabilities.shape != (len(batch), len(LABELS)) or not np.isfinite(probabilities).all():
                raise ValueError(f"Invalid class scores returned by {tag}")
            if probabilities.min() < -1e-6 or not np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-4):
                raise ValueError(f"{tag} does not return class probabilities")
            for (cohort, label, path), scores in zip(batch, probabilities):
                winner = int(np.argmax(scores))
                rows.append({"cohort": cohort, "label": label, "file": path.relative_to(ROOT).as_posix(),
                             "model": tag, "prediction": order[winner], "score": float(scores[winner]),
                             **{f"p_{name}": float(scores[order.index(name)]) for name in LABELS}})
        if first % (batch_size * 20) == 0:
            print(f"{min(first + len(batch), len(items))}/{len(items)} images", flush=True)
    write_csv("image_predictions.csv", rows)
    result = {"models": hashes, "label_order": LABELS,
              "exclusions": dict(Counter(row["reason"] for row in excluded)), "cohorts": {}}
    compact = []
    threshold_table = []
    for cohort in COHORTS:
        result["cohorts"][cohort] = {}
        for tag in EXPORTS:
            subset = [r for r in rows if r["cohort"] == cohort and r["model"] == tag]
            report = metrics(subset, ["empty"] if cohort == "empty_replay" else MATERIALS)
            result["cohorts"][cohort][tag] = report
            compact.append({"cohort": cohort, "model": tag, "n": report["n"], "correct": report["correct"],
                            "top1_accuracy": report["accuracy"], "macro_f1": report["macro_f1"]})
            threshold_table.extend({"cohort": cohort, "model": tag, **line} for line in report["thresholds"])
    write_csv("model_summary.csv", compact)
    write_csv("threshold_sweep.csv", threshold_table)
    (OUTPUT / "metrics.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    for line in compact:
        print(line, flush=True)
    print(f"Finished in {time.perf_counter() - start:.1f}s. No project settings changed.", flush=True)


if __name__ == "__main__":
    main()
