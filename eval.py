"""Compare predictions with assignment labels and report completeness explicitly."""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
GOLD_FILE, PREDICTIONS_FILE = BASE / "data" / "gold.json", BASE / "outputs" / "predictions.json"
LABELS = ["supported", "overstated", "cannot_verify"]


def records(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("claims"), list):
        data = data["claims"]
    elif isinstance(data, dict):
        data = [dict(v, claim_id=v.get("claim_id", k)) for k, v in data.items() if isinstance(v, dict)]
    if not isinstance(data, list):
        raise ValueError(f"{path.name} must contain a list")
    found, malformed, duplicates = {}, [], []
    for i, obj in enumerate(data):
        if not isinstance(obj, dict) or not obj.get("claim_id"):
            malformed.append(f"row {i}: object/claim_id missing")
            continue
        cid = str(obj["claim_id"])
        if cid in found:
            duplicates.append(cid)
        found[cid] = obj
    return found, malformed, duplicates


def main():
    try:
        gold, _, _ = records(GOLD_FILE)
        preds, malformed, duplicates = records(PREDICTIONS_FILE)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2
    common = sorted(set(gold) & set(preds))
    if not common:
        print("ERROR: no matching claim IDs")
        return 2
    actual = {c: str(gold[c].get("verdict", "")).strip().lower() for c in common}
    predicted = {c: str(preds[c].get("verdict", "")).strip().lower() for c in common}
    matrix = {a: {p: 0 for p in LABELS} for a in LABELS}
    invalid, false_supported = [], []
    for cid in common:
        a, p = actual[cid], predicted[cid]
        if p == "supported" and a != "supported": false_supported.append(cid)
        if a in LABELS and p in LABELS: matrix[a][p] += 1
        else: invalid.append((cid, a, p))
    correct = sum(actual[c] == predicted[c] and actual[c] in LABELS for c in common)
    print("=== Claim Verification Evaluation ===")
    print(f"Comparable predictions: {len(common)}; correct: {correct}; accuracy: {correct / len(common):.2%}")
    print("Confusion matrix (rows=gold, columns=prediction)")
    print("gold/prediction\t" + "\t".join(LABELS))
    for label in LABELS: print(label + "\t" + "\t".join(str(matrix[label][p]) for p in LABELS))
    print("Per-class precision / recall / F1")
    for label in LABELS:
        tp = matrix[label][label]
        fp = sum(matrix[a][label] for a in LABELS if a != label)
        fn = sum(matrix[label][p] for p in LABELS if p != label)
        precision = tp / (tp + fp) if tp + fp else 0
        recall = tp / (tp + fn) if tp + fn else 0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
        print(f"{label}: precision={precision:.2f} recall={recall:.2f} F1={f1:.2f}")
    missing, extra = sorted(set(gold) - set(preds)), sorted(set(preds) - set(gold))
    if false_supported: print("False-supported claims:", ", ".join(false_supported))
    if missing: print("Missing predictions:", ", ".join(missing))
    if extra: print("Unexpected prediction IDs:", ", ".join(extra))
    if invalid: print("Malformed/invalid verdicts:", invalid)
    if malformed: print("Malformed rows:", malformed)
    if duplicates: print("Duplicate IDs:", duplicates)
    api_failures = [c for c in preds if preds[c].get("verification_status") == "api_failure"]
    incomplete = [c for c in preds if preds[c].get("verification_status") in {"api_not_requested", "api_failure", "source_unavailable", "evidence_incomplete", "evidence_invalid", "model_output_invalid", "model_cannot_verify"}]
    if api_failures or incomplete or missing or invalid:
        print("WARNING: results are invalid or incomplete for model evaluation; API failures, offline placeholders, missing predictions, or malformed outputs are present.")
        if api_failures: print("API-failed claims:", ", ".join(api_failures))
        if incomplete: print("Incomplete claims:", ", ".join(incomplete))
    return 0 if not missing and not invalid and not malformed and not duplicates else 1


if __name__ == "__main__":
    raise SystemExit(main())
