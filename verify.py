"""Conservative claim verification pipeline. Live Gemini use is explicitly opt-in."""
import json
import math
import os
import re
import shutil
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
import pymupdf

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

try:
    import pytesseract
    from PIL import Image
except ImportError:  # OCR is optional
    pytesseract = None
    Image = None

if pytesseract is not None:
    _tesseract = shutil.which("tesseract") or r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if Path(_tesseract).is_file():
        pytesseract.pytesseract.tesseract_cmd = _tesseract

DATA_DIR, PAPERS_DIR, OUTPUT_DIR = BASE_DIR / "data", BASE_DIR / "papers", BASE_DIR / "outputs"
PREDICTIONS_FILE, LOG_FILE = OUTPUT_DIR / "predictions.json", OUTPUT_DIR / "llm_log.jsonl"
ALLOWED_VERDICTS = {"supported", "overstated", "cannot_verify"}
ALLOWED_CHECKS = {"match", "mismatch", "unclear"}
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
VERIFICATION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "verdict": {"type": "STRING", "enum": ["supported", "overstated", "cannot_verify"]},
        "checks": {"type": "OBJECT", "properties": {k: {"type": "STRING", "enum": ["match", "mismatch", "unclear"]} for k in ("figure", "population", "drug_indication", "actually_stated")}, "required": ["figure", "population", "drug_indication", "actually_stated"]},
        "source_id": {"type": "INTEGER"},
        "evidence_span": {"type": "STRING"},
        "numeric_values": {"type": "ARRAY", "items": {"type": "NUMBER"}},
        "reason": {"type": "STRING"},
    },
    "required": ["verdict", "checks", "source_id", "evidence_span", "numeric_values", "reason"],
}


def normalize(text):
    text = unicodedata.normalize("NFKC", str(text)).replace("\u00ad", "")
    return re.sub(r"\s+", " ", text).strip()


def evidence_matches(span, source_text):
    if not isinstance(span, str) or not span.strip():
        return False
    return normalize(span).casefold() in normalize(source_text).casefold()


def load_claims(path=None):
    path = Path(path) if path else DATA_DIR / "claims.json"
    if not path.is_absolute():
        path = BASE_DIR / path
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or any(not isinstance(c, dict) or not c.get("claim_id") or not c.get("text") for c in data):
        raise ValueError("Claims must be a list of objects with claim_id and text.")
    if len({str(c['claim_id']) for c in data}) != len(data):
        raise ValueError("Duplicate claim_id values.")
    return data


def cited_source_missing(claim):
    cited = str(claim.get("cited_paper", "")).casefold()
    if "soliqua_lbl" not in cited and "prescribing information" not in cited and "prescribing info" not in cited:
        return False
    return not any((PAPERS_DIR / n).exists() for n in ("SOLIQUA_LBL.pdf", "SOLIQUA_PI.pdf", "SOLIQUA_Prescribing_Information.pdf"))


def extract_page_text(page):
    text = page.get_text().strip()
    if len(text) >= 150 or pytesseract is None or Image is None:
        return text, "PDF text" if text else "PDF text unavailable; OCR unavailable"
    try:
        pix = page.get_pixmap(matrix=pymupdf.Matrix(3, 3), alpha=False)
        image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        ocr = pytesseract.image_to_string(image, lang="eng").strip()
        return "\n".join(x for x in (text, ocr) if x), "PDF text + OCR" if ocr else "PDF text; OCR returned no text"
    except Exception as exc:
        return text, f"PDF text; OCR failed: {exc}"


def get_sources_for_all_claims(claims=None):
    sources = []
    for path in sorted(PAPERS_DIR.glob("*.pdf")):
        try:
            with pymupdf.open(path) as pdf:
                for number, page in enumerate(pdf, 1):
                    text, method = extract_page_text(page)
                    if text.strip():
                        sources.append({"filename": path.name, "page": number, "text": text, "method": method})
        except Exception as exc:
            print(f"Warning: could not extract {path.name}: {exc}")
    # The LixiLan-L abstract is supplied as a raster panel alongside the PDFs.
    for path in sorted(PAPERS_DIR.glob("*.png")):
        text, method = "", "OCR unavailable"
        if pytesseract is not None and Image is not None:
            try:
                text = pytesseract.image_to_string(Image.open(path), lang="eng").strip()
                method = "OCR" if text else "OCR returned no text"
            except Exception as exc:
                method = f"OCR failed: {exc}"
        sources.append({"filename": path.name, "page": 1, "text": text, "method": method})
    return sources


def cannot_verify(claim_id, reason, status="local_validation"):
    return {"claim_id": str(claim_id), "verdict": "cannot_verify", "checks": {k: "unclear" for k in ("figure", "population", "drug_indication", "actually_stated")}, "evidence_file": None, "evidence_page": None, "evidence_span": "", "computed": None, "reason": reason, "verification_status": status}


def compute_ratio_if_applicable(claim, extracted_values):
    txt = str(claim.get("text", "")).casefold()
    if not re.search(r"(?:~|approximately\s+)\s*2\s*(?:x|times|×)", txt):
        return None
    if not isinstance(extracted_values, list) or len(extracted_values) != 2:
        return None
    try:
        a, b = map(float, extracted_values)
    except (TypeError, ValueError):
        return None
    if not all(map(math.isfinite, (a, b))) or b == 0:
        return None
    ratio = abs(a / b)
    return {"claimed": "~2x", "source_values": [a, b], "result": round(ratio, 4), "matches_claim": math.isclose(ratio, 2, rel_tol=.2)}


def _safe_log(record):
    """Best effort logging; redact credential-shaped values, never disrupt predictions."""
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        text = json.dumps(record, ensure_ascii=False)
        key = os.getenv("GEMINI_API_KEY", "")
        if key:
            text = text.replace(key, "[REDACTED]")
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception as exc:
        print(f"Warning: could not write API log: {exc}", file=sys.stderr)


def _sources_for_claim(claim, all_sources):
    if str(claim.get("claim_id")) == "C7":
        expected = {"C5C7_page2.png", "C2C4C6.pdf", "C7.pdf"}
        return [s for s in all_sources if s["filename"] in expected and s["text"].strip()]
    if str(claim.get("claim_id")) == "C5":
        raster = [s for s in all_sources if s["filename"] == "C5C7_page2.png" and s["text"].strip()]
        return raster
    cited = str(claim.get("cited_paper", ""))
    names = re.findall(r"[A-Za-z0-9_-]+\.pdf", cited)
    if not names:
        # Legacy folder uses consolidated PDFs. C7 requires all three sources,
        # so no single-source result can complete it.
        if str(claim.get("claim_id")) == "C7":
            return [s for s in all_sources if s["filename"] in {"C7.pdf", "C5C7.pdf", "C2C4C6.pdf"}]
        names = ["C2C4C6.pdf" if str(claim.get("claim_id")) in {"C2", "C4", "C6"} else "C3.pdf" if str(claim.get("claim_id")) == "C3" else "C5C7.pdf"]
    mapped = [s for s in all_sources if s["filename"] in names]
    # Assignment copies papers under consolidated filenames. Use known local
    # mapping only when no explicitly named paper exists in the supplied set.
    if not mapped and len(names) == 1:
        fallback = {"C2": "C2C4C6.pdf", "C3": "C3.pdf", "C4": "C2C4C6.pdf", "C5": "C5C7.pdf", "C6": "C2C4C6.pdf"}.get(str(claim.get("claim_id")))
        mapped = [s for s in all_sources if s["filename"] == fallback] if fallback else []
    return mapped


def select_sources(claim, sources, max_pages=12):
    stop = {"the", "and", "for", "with", "from", "than", "this", "that", "claim", "versus", "versus", "patients"}
    terms = {w for w in re.findall(r"[a-z0-9]+", " ".join(str(claim.get(k, "")) for k in ("text", "population", "molecule")).casefold()) if len(w) > 2 and w not in stop}
    ranked = sorted(sources, key=lambda s: (len(terms & set(re.findall(r"[a-z0-9]+", s["text"].casefold()))), -s["page"]), reverse=True)
    return ranked[:max_pages]


def classify_api_error(exc):
    msg = str(exc).casefold()
    quota_markers = ("quota exceeded", "quota exhausted", "quota limit", "current quota", "quota has been exceeded")
    if re.search(r"\b429\b", msg) or "resource_exhausted" in msg or any(marker in msg for marker in quota_markers):
        return "quota_exhausted"
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if isinstance(exc, ValueError) and "evidence" in msg:
        return "evidence_validation"
    if isinstance(exc, (json.JSONDecodeError, ValueError)):
        return "malformed_response"
    return "api_error"


def verify_claim(client, claim, all_sources):
    cid = str(claim["claim_id"])
    if cited_source_missing(claim):
        return cannot_verify(cid, "The cited prescribing information is missing; unrelated trial papers cannot verify the label claim.")
    sources = _sources_for_claim(claim, all_sources)
    if cid == "C7":
        required = {"C5C7_page2.png", "C2C4C6.pdf", "C7.pdf"}
        available = {s["filename"] for s in sources}
        if not required.issubset(available):
            missing = sorted(required - available)
            return cannot_verify(cid, "C7 needs independently readable LixiLan-L, SoliMix, and propensity-matched panel sources; unavailable: " + ", ".join(missing), "evidence_incomplete")
        if client is None:
            return cannot_verify(cid, "Live model verification was not run; all three source panels are available for independent review.", "api_not_requested")
        return _verify_c7(client, claim, sources)
    if not sources:
        return cannot_verify(cid, "No readable pages from the cited source are available.", "source_unavailable")
    if client is None:
        return cannot_verify(cid, "Live model verification was not run. Set GEMINI_ENABLED=true to opt in; local source extraction completed without network access.", "api_not_requested")
    # The live prompt limits the model to the cited source; evidence must match
    # the exact returned source page, not an arbitrary page in the bundle.
    selected = select_sources(claim, sources)
    blocks = "\n\n".join(f"[SOURCE_ID: {i}] FILE: {s['filename']} PAGE: {s['page']}\n{s['text']}" for i, s in enumerate(selected))
    prompt = f"Verify the claim using only the supplied sources. Return the required JSON fields. checks must use exactly match, mismatch, or unclear (never booleans). Choose ONE integer source_id and quote ONE short contiguous exact passage from that source page only. Do not add labels or explanations inside evidence_span. If no exact quote is available, use cannot_verify and an empty evidence_span. A supported verdict requires all checks to be match. Claim: {json.dumps(claim, ensure_ascii=False)}\n{blocks}"
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), "claim_id": cid, "model": MODEL_NAME, "parameters": {"temperature": 0, "thinking_budget": 0, "max_output_tokens": 1200, "response_mime_type": "application/json", "response_schema": VERIFICATION_SCHEMA, "timeout_ms": 30000, "retries": 0}, "prompt": prompt, "raw_response": None, "error": None}
    try:
        from google.genai import types
        response = client.models.generate_content(model=MODEL_NAME, contents=prompt, config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=VERIFICATION_SCHEMA, max_output_tokens=1200, thinking_config=types.ThinkingConfig(thinking_budget=0), temperature=0, http_options=types.HttpOptions(timeout=30000, retry_options=types.HttpRetryOptions(attempts=1))))
        raw = response.text or ""
        record["raw_response"] = raw
        obj = json.loads(raw)
        verdict, checks = obj.get("verdict"), obj.get("checks")
        if verdict not in ALLOWED_VERDICTS or not isinstance(checks, dict) or any(checks.get(k) not in ALLOWED_CHECKS for k in ("figure", "population", "drug_indication", "actually_stated")):
            raise ValueError("Malformed model JSON or unsupported verdict/check value")
        if verdict == "supported" and any(checks[k] != "match" for k in checks):
            raise ValueError("Supported verdict has a non-matching check")
        source_id = obj.get("source_id")
        if type(source_id) is not int or not 0 <= source_id < len(selected):
            raise ValueError("Invalid source_id")
        source = selected[source_id]
        quote = obj.get("evidence_span", "")
        if verdict != "cannot_verify" and not evidence_matches(quote, source["text"]):
            raise ValueError("Evidence is not an exact quotation from the selected source and page")
        computed = compute_ratio_if_applicable(claim, obj.get("numeric_values"))
        if computed and not computed["matches_claim"]:
            verdict, checks["figure"] = "overstated", "mismatch"
        if verdict == "cannot_verify":
            result = cannot_verify(cid, str(obj.get("reason", "Evidence is unclear.")), "model_cannot_verify")
        else:
            result = {"claim_id": cid, "verdict": verdict, "checks": checks, "evidence_file": source["filename"], "evidence_page": source["page"], "evidence_span": quote, "computed": computed, "reason": str(obj.get("reason", "")), "verification_status": "completed"}
        return result
    except Exception as exc:
        category = classify_api_error(exc)
        record["error"] = {"type": type(exc).__name__, "category": category, "message": str(exc)}
        status = "evidence_invalid" if category == "evidence_validation" else "model_output_invalid" if category == "malformed_response" else "api_failure"
        return cannot_verify(cid, f"{category.replace('_', ' ')}; no scientific verdict was produced.", status)
    finally:
        _safe_log(record)


def _verify_c7(client, claim, sources):
    """Assess the three C7 panels independently in one bounded API request."""
    cid = str(claim["claim_id"])
    panel_files = {"LixiLan-L": "C5C7_page2.png", "SoliMix": "C2C4C6.pdf", "propensity_score_matched": "C7.pdf"}
    selected = [s for panel, filename in panel_files.items() for s in select_sources(claim, [x for x in sources if x["filename"] == filename], 4)]
    blocks = "\n\n".join(f"[SOURCE_ID: {i}] FILE: {s['filename']} PAGE: {s['page']}\n{s['text']}" for i, s in enumerate(selected))
    prompt = f"Evaluate the aggregate claim: {json.dumps(claim, ensure_ascii=False)}. Independently assess exactly these panels: {json.dumps(panel_files)}. For each panel return panel, verdict, four checks (figure/population/drug_indication/actually_stated), source_id, exact evidence_span, reason. Each panel must use its corresponding FILE only. Do not infer one panel from another. Allowed verdicts supported/overstated/cannot_verify and checks match/mismatch/unclear. Return JSON {{\"panel_results\":[...]}} only.\n{blocks}"
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), "claim_id": cid, "model": MODEL_NAME, "parameters": {"temperature": 0, "response_mime_type": "application/json", "timeout_ms": 30000, "retries": 0}, "prompt": prompt, "raw_response": None, "error": None}
    try:
        from google.genai import types
        response = client.models.generate_content(model=MODEL_NAME, contents=prompt, config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0, http_options=types.HttpOptions(timeout=30000, retry_options=types.HttpRetryOptions(attempts=1))) )
        raw = response.text or ""
        record["raw_response"] = raw
        obj = json.loads(raw)
        panel_results = obj.get("panel_results")
        if not isinstance(panel_results, list) or len(panel_results) != 3:
            raise ValueError("C7 response must contain exactly three panel results")
        items, verdicts = [], []
        for panel, filename in panel_files.items():
            result = next((r for r in panel_results if isinstance(r, dict) and r.get("panel") == panel), None)
            if result is None or result.get("verdict") not in ALLOWED_VERDICTS:
                raise ValueError(f"Missing or invalid C7 panel result: {panel}")
            checks = result.get("checks")
            keys = ("figure", "population", "drug_indication", "actually_stated")
            if not isinstance(checks, dict) or any(checks.get(k) not in ALLOWED_CHECKS for k in keys):
                raise ValueError(f"Invalid checks for C7 panel: {panel}")
            source_id = result.get("source_id")
            if type(source_id) is not int or not 0 <= source_id < len(selected):
                raise ValueError(f"Invalid source id for C7 panel: {panel}")
            source = selected[source_id]
            quote = result.get("evidence_span", "")
            if source["filename"] != filename or not evidence_matches(quote, source["text"]):
                raise ValueError(f"C7 {panel} evidence does not match its own source page")
            items.append({"panel": panel, "evidence_file": filename, "evidence_page": source["page"], "evidence_span": quote})
            verdicts.append((result["verdict"], checks))
        if any(v == "overstated" or any(c[k] == "mismatch" for k in c) for v, c in verdicts):
            verdict = "overstated"
        elif all(v == "supported" and all(c[k] == "match" for k in ("figure", "population", "drug_indication", "actually_stated")) for v, c in verdicts):
            verdict = "supported"
        else:
            verdict = "cannot_verify"
        first = items[0]
        return {"claim_id": cid, "verdict": verdict, "checks": verdicts[0][1], "evidence_file": first["evidence_file"], "evidence_page": first["evidence_page"], "evidence_span": first["evidence_span"], "evidence_items": items, "computed": None, "reason": " | ".join(str(r.get("reason", "")) for r in panel_results), "verification_status": "completed" if verdict != "cannot_verify" else "model_cannot_verify"}
    except Exception as exc:
        record["error"] = {"type": type(exc).__name__, "category": classify_api_error(exc), "message": str(exc)}
        return cannot_verify(cid, "C7 panel assessment failed; no combined scientific verdict was produced.", "api_failure")
    finally:
        _safe_log(record)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        claims = load_claims(sys.argv[1] if len(sys.argv) > 1 else None)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    # Explicit opt-in prevents accidental calls against exhausted/free-tier API.
    client = None
    enabled = os.getenv("GEMINI_ENABLED", "false").strip().casefold() in {"1", "true", "yes"}
    if enabled:
        if not os.getenv("GEMINI_API_KEY", "").strip():
            print("GEMINI_ENABLED=true but GEMINI_API_KEY is missing; requests disabled.", file=sys.stderr)
        else:
            try:
                from google import genai
                from google.genai import types
                client = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=types.HttpOptions(timeout=30000, retry_options=types.HttpRetryOptions(attempts=1)))
            except Exception as exc:
                print(f"Could not initialize Gemini client: {exc}", file=sys.stderr)
    print("Extracting local source pages...")
    sources = get_sources_for_all_claims(claims)
    predictions = []
    for claim in claims:
        prediction = verify_claim(client, claim, sources)
        predictions.append(prediction)
        print(f"{claim['claim_id']}: {prediction['verdict']} ({prediction['verification_status']})")
    PREDICTIONS_FILE.write_text(json.dumps(predictions, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {len(predictions)} predictions to {PREDICTIONS_FILE}")
    if not enabled:
        print("Offline mode: no Gemini requests were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
