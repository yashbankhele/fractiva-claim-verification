"""Check prediction quotations against their declared PDF and page."""
import json
from pathlib import Path

import pymupdf
from verify import extract_page_text, evidence_matches

BASE = Path(__file__).resolve().parent
PREDICTIONS = BASE / "outputs" / "predictions.json"


def check_evidence(filename, page_number, evidence_span):
    path = BASE / "papers" / str(filename)
    if not path.is_file():
        return "CHECK", f"source file missing: {filename}"
    if type(page_number) is not int or page_number < 1:
        return "SKIP", "page number missing or invalid"
    if not isinstance(evidence_span, str) or not evidence_span.strip():
        return "SKIP", "evidence span is empty"
    if path.suffix.casefold() in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
        try:
            import pytesseract
            from PIL import Image
            text = pytesseract.image_to_string(Image.open(path), lang="eng")
            if evidence_matches(evidence_span, text):
                return "PASS", f"exact quote in raster source {filename} ({page_number}) via OCR"
            return "CHECK", f"quote not found by OCR in raster source {filename}"
        except Exception as exc:
            return "CHECK", f"raster source needs OCR/manual review: {exc}"
    try:
        with pymupdf.open(path) as pdf:
            if page_number > len(pdf):
                return "CHECK", f"{filename} has {len(pdf)} pages; requested {page_number}"
            text, method = extract_page_text(pdf[page_number - 1])
        if not text:
            return "CHECK", f"page text unavailable ({method}); absence is inconclusive"
        if evidence_matches(evidence_span, text):
            return "PASS", f"exact quote on {filename}, page {page_number} ({method})"
        if "OCR failed" in method or "OCR unavailable" in method:
            return "CHECK", f"extraction incomplete ({method})"
        return "CHECK", f"quote not found on declared page ({method})"
    except Exception as exc:
        return "CHECK", f"could not inspect {filename}: {exc}"


def check_prediction(item):
    """Return independent panel checks and an aggregate result for a prediction."""
    if not isinstance(item, dict) or item.get("verdict") == "cannot_verify":
        return "SKIP", [("SKIP", "no completed evidence-bearing verdict")]
    evidence_items = item.get("evidence_items")
    if not isinstance(evidence_items, list):
        evidence_items = [{"evidence_file": item.get("evidence_file"), "evidence_page": item.get("evidence_page"), "evidence_span": item.get("evidence_span", "")}]
    if not evidence_items:
        return "CHECK", [("CHECK", "no evidence items")]
    details = [check_evidence(panel.get("evidence_file"), panel.get("evidence_page"), panel.get("evidence_span")) for panel in evidence_items]
    statuses = [status for status, _ in details]
    overall = "PASS" if all(s == "PASS" for s in statuses) else "CHECK" if "CHECK" in statuses else "SKIP"
    return overall, details


def main():
    if not PREDICTIONS.is_file():
        print(f"ERROR: predictions file missing: {PREDICTIONS}")
        return 2
    try:
        predictions = json.loads(PREDICTIONS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: invalid predictions JSON: {exc}")
        return 2
    if not isinstance(predictions, list):
        print("ERROR: predictions must be a JSON list")
        return 2
    counts = {"PASS": 0, "CHECK": 0, "SKIP": 0}
    for item in predictions:
        cid = item.get("claim_id", "UNKNOWN") if isinstance(item, dict) else "UNKNOWN"
        overall, details = check_prediction(item)
        for status, reason in details:
            print(f"{cid}: {status} — {reason}")
        counts[overall] += 1
    print("\nEvidence check summary")
    print(" ".join(f"{k}: {v}" for k, v in counts.items()))
    print("A quotation match checks source/page fidelity only; it does not establish that the claim is scientifically supported.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
