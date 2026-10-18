import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_evidence
import eval as evaluation


class ToolTests(unittest.TestCase):
    def test_evidence_source_page_mapping(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "papers").mkdir()
            import pymupdf
            pdf = pymupdf.open()
            page = pdf.new_page()
            page.insert_text((72, 72), "Exact evidence on this page.")
            pdf.save(root / "papers" / "paper.pdf")
            pdf.close()
            with patch.object(check_evidence, "BASE", root):
                self.assertEqual(check_evidence.check_evidence("paper.pdf", 1, "Exact evidence on this page.")[0], "PASS")
                self.assertEqual(check_evidence.check_evidence("paper.pdf", 2, "Exact evidence on this page.")[0], "CHECK")
                self.assertEqual(check_evidence.check_evidence("other.pdf", 1, "Exact evidence")[0], "CHECK")

    def test_c7_panels_are_checked_against_independent_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "papers").mkdir()
            import pymupdf
            for filename, quote in (("lixilan.pdf", "Weight decreased in LixiLan-L."), ("solimix.pdf", "Weight favored iGlarLixi in SoliMix."), ("psm.pdf", "Matched analysis favored iGlarLixi.")):
                pdf = pymupdf.open(); pdf.new_page().insert_text((72, 72), quote); pdf.save(root / "papers" / filename); pdf.close()
            prediction = {"claim_id": "C7", "verdict": "supported", "evidence_items": [
                {"evidence_file": "lixilan.pdf", "evidence_page": 1, "evidence_span": "Weight decreased in LixiLan-L."},
                {"evidence_file": "solimix.pdf", "evidence_page": 1, "evidence_span": "Weight favored iGlarLixi in SoliMix."},
                {"evidence_file": "psm.pdf", "evidence_page": 1, "evidence_span": "Matched analysis favored iGlarLixi."},
            ]}
            with patch.object(check_evidence, "BASE", root):
                overall, details = check_evidence.check_prediction(prediction)
            self.assertEqual(overall, "PASS")
            self.assertEqual([d[0] for d in details], ["PASS", "PASS", "PASS"])

    def test_evaluation_records_detect_missing_prediction_and_malformed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "predictions.json"
            path.write_text(json.dumps([{"claim_id": "C1", "verdict": "supported"}, "bad row"]), encoding="utf-8")
            found, malformed, duplicates = evaluation.records(path)
            self.assertIn("C1", found)
            self.assertEqual(len(malformed), 1)
            self.assertEqual(duplicates, [])
            self.assertEqual({"C1", "C2"} - set(found), {"C2"})


if __name__ == "__main__": unittest.main()
