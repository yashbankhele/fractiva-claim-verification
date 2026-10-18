import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import verify


class FakeResponse:
    def __init__(self, text): self.text = text


class FakeModels:
    def __init__(self, text=None, error=None): self.text, self.error = text, error
    def generate_content(self, **kwargs):
        if self.error: raise self.error
        return FakeResponse(self.text)


class FakeClient:
    def __init__(self, text=None, error=None): self.models = FakeModels(text, error)


def model_result(quote="HbA1c decreased by 1.3%."):
    return json.dumps({"verdict": "supported", "checks": {k: "match" for k in ("figure", "population", "drug_indication", "actually_stated")}, "source_id": 0, "evidence_span": quote, "numeric_values": [], "reason": "quoted"})


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.claim = {"claim_id": "C2", "text": "HbA1c result", "cited_paper": "SoliMix_Rosenstock2021.pdf"}
        self.sources = [{"filename": "C2C4C6.pdf", "page": 5, "text": "HbA1c decreased by 1.3%.", "method": "PDF text"}]

    def test_valid_model_json_and_exact_page_evidence(self):
        with patch.object(verify, "_safe_log"):
            out = verify.verify_claim(FakeClient(model_result()), self.claim, self.sources)
        self.assertEqual(out["verdict"], "supported")
        self.assertEqual((out["evidence_file"], out["evidence_page"]), ("C2C4C6.pdf", 5))

    def test_invalid_json_is_api_failure(self):
        with patch.object(verify, "_safe_log"):
            out = verify.verify_claim(FakeClient("not json"), self.claim, self.sources)
        self.assertEqual(out["verification_status"], "model_output_invalid")
        self.assertEqual(out["verdict"], "cannot_verify")

    def test_unsupported_verdict_and_fabricated_or_wrong_page_quote_fail_closed(self):
        bad = json.loads(model_result()); bad["verdict"] = "true"
        with patch.object(verify, "_safe_log"):
            out = verify.verify_claim(FakeClient(json.dumps(bad)), self.claim, self.sources)
        self.assertEqual(out["verification_status"], "model_output_invalid")
        with patch.object(verify, "_safe_log"):
            out = verify.verify_claim(FakeClient(model_result("quote from elsewhere")), self.claim, self.sources)
        self.assertEqual(out["verdict"], "cannot_verify")
        self.assertEqual(out["verification_status"], "evidence_invalid")

    def test_quota_error_is_not_retried_or_a_verdict(self):
        client = FakeClient(error=RuntimeError("429 RESOURCE_EXHAUSTED"))
        with patch.object(verify, "_safe_log"):
            out = verify.verify_claim(client, self.claim, self.sources)
        self.assertEqual(out["verification_status"], "api_failure")
        self.assertEqual(out["verdict"], "cannot_verify")
        self.assertEqual(verify.classify_api_error(ValueError("Evidence is not an exact quotation from the source page")), "evidence_validation")
        self.assertEqual(verify.classify_api_error(RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")), "quota_exhausted")

    def test_missing_c1_source_and_c7_panels(self):
        c1 = {"claim_id": "C1", "text": "indication", "cited_paper": "SOLIQUA_LBL.pdf"}
        out = verify.verify_claim(None, c1, self.sources)
        self.assertEqual(out["verification_status"], "local_validation")
        self.assertIn("missing", out["reason"])
        c7 = {"claim_id": "C7", "text": "weight benefit", "cited_paper": "Tabak.pdf plus LixiLan.pdf and SoliMix.pdf"}
        out = verify.verify_claim(None, c7, self.sources)
        self.assertEqual(out["verification_status"], "evidence_incomplete")

    def test_c7_validates_each_panel_against_its_own_source(self):
        claim = {"claim_id": "C7", "text": "weight benefit across panels", "cited_paper": "Tabak2020_PSM.pdf plus LixiLan-L and SoliMix"}
        sources = [
            {"filename": "C5C7_page2.png", "page": 1, "text": "LixiLan-L weight decreased by 0.7 kg", "method": "OCR"},
            {"filename": "C2C4C6.pdf", "page": 1, "text": "SoliMix weight benefit with iGlarLixi", "method": "PDF text"},
            {"filename": "C7.pdf", "page": 5, "text": "Matched analysis weight benefit with iGlarLixi", "method": "PDF text"},
        ]
        panels = []
        for index, (panel, quote) in enumerate((("LixiLan-L", sources[0]["text"]), ("SoliMix", sources[1]["text"]), ("propensity_score_matched", sources[2]["text"]))):
            panels.append({"panel": panel, "verdict": "supported", "checks": {k: "match" for k in ("figure", "population", "drug_indication", "actually_stated")}, "source_id": index, "evidence_span": quote, "reason": "panel checked"})
        with patch.object(verify, "_safe_log"):
            out = verify.verify_claim(FakeClient(json.dumps({"panel_results": panels})), claim, sources)
        self.assertEqual(out["verdict"], "supported")
        self.assertEqual([i["evidence_file"] for i in out["evidence_items"]], [s["filename"] for s in sources])
        panels[0]["source_id"] = 1
        with patch.object(verify, "_safe_log"):
            out = verify.verify_claim(FakeClient(json.dumps({"panel_results": panels})), claim, sources)
        self.assertEqual(out["verification_status"], "api_failure")

    def test_ratio_arithmetic(self):
        claim = {"text": "approximately 2x"}
        self.assertEqual(verify.compute_ratio_if_applicable(claim, [-1.6, -1.3])["result"], 1.2308)
        self.assertFalse(verify.compute_ratio_if_applicable(claim, [-1.6, -1.3])["matches_claim"])
        self.assertIsNone(verify.compute_ratio_if_applicable(claim, [-1, 0]))

    def test_log_redacts_key_and_logging_failure_does_not_raise(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(verify, "OUTPUT_DIR", Path(temp)), patch.object(verify, "LOG_FILE", Path(temp) / "log.jsonl"), patch.dict(os.environ, {"GEMINI_API_KEY": "secret-value"}):
                verify._safe_log({"claim_id": "C2", "prompt": "secret-value", "raw_response": "ok"})
                contents = (Path(temp) / "log.jsonl").read_text()
                self.assertNotIn("secret-value", contents)
            with patch.object(verify, "OUTPUT_DIR", Path(temp) / "missing"), patch.object(verify, "LOG_FILE", Path(temp) / "missing" / "no"), patch("builtins.open", side_effect=OSError("disk")):
                verify._safe_log({"claim_id": "C"})


if __name__ == "__main__": unittest.main()
