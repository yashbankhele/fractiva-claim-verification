# Claim verification take-home report

## Approach

The project extracts text from supplied PDFs and asks a configured model to assess a claim. I tightened the evidence path so a returned quotation must match the selected source page, added structured failure states, and made network access opt-in. Numerical ratios are computed in Python. The evidence checker verifies the prediction's declared page separately from label evaluation.

## Findings and limitations

An earlier live run reached Gemini: C4 and C5 returned completed verdicts and their quotations passed local exact-source checks. C2 and C6 failed evidence validation, C3 returned an invalid model response, and C7 failed its combined panel assessment. C5 was labeled `supported` by the model but is `overstated` in the supplied gold data, so that result needs review. A later run exhausted Gemini quota, leaving C2-C7 without a current scientific verdict. Evaluation remains incomplete. The default run is offline and makes no request; resulting `cannot_verify` records are tagged `api_not_requested`.

The provided corpus does not include the cited `SOLIQUA_LBL.pdf`, so C1 cannot be verified against its required prescribing-information source. The four-page Sanofi promotional PDF is included in `papers/`, but it is not a substitute for the prescribing information. Tesseract OCR is installed and configured for image-based pages such as the C5 abstract. The available materials report approximately 1.6% versus 1.3% HbA1c reduction for C3, about 1.23-fold by arithmetic, which does not establish ~2-fold. Source text extraction and exact quotation checks cannot alone establish population, formulation, endpoint, or time-point correspondence.

## Reproduction

Run `python -m unittest discover -s tests -v`, then `python verify.py data/claims.json`, `python check_evidence.py`, and `python eval.py`. No live evaluation is claimed in this report. Gemini remains disabled unless explicitly enabled by the operator.
