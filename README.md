# Fractiva Claim Verification

A small Python pipeline that checks pharmaceutical claims against the supplied research papers and promotional material. It extracts source text, asks Gemini to assess claims when enabled, and checks whether returned quotations match the cited pages.

## Run locally

Requires Python 3.10 or newer. In PowerShell, from this folder:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Run without using the Gemini API:

```powershell
python verify.py data/claims.json
python check_evidence.py
python eval.py
```

By default, verification is offline. To enable Gemini, copy `.env.example` to `.env`, add your API key, and set `GEMINI_ENABLED=true`. Live calls may use API quota. Never commit or share `.env`.

## Current status and limitations

- The pipeline runs locally; the offline run and 11 unit tests passed in this environment.
- C1 needs the SOLIQUA prescribing information, which was not included in the supplied files. The promotional PDF does not replace that label, so C1 remains `cannot_verify`.
- The latest Gemini run exhausted API quota. C2–C7 do not have current live verdicts; the offline evaluation score is not a model-performance result.
- In an earlier live run, C4 and C5 passed quotation checks. C5's `supported` result conflicts with the provided gold label and needs review. Quote matching alone does not prove a scientific claim.

## Project files

- `data/claims.json`: claims to assess.
- `data/gold.json`: supplied reference labels for evaluation.
- `papers/`: supplied studies, abstract image, and Sanofi promotional PDF.
- `outputs/predictions.json`: most recent run output; it may be overwritten by the next run.
- `WRITEUP.md`: approach, findings, and limitations.

The API log is written to `outputs/llm_log.jsonl`. It can contain prompts and source text, so it is excluded from Git.
