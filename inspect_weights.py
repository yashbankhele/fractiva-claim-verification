import pymupdf
from pathlib import Path

files = ["C7.pdf", "C5C7.pdf", "C2C4C6.pdf"]
keywords = ["weight", "body weight", "-1.32", "0.7 kg", "1.9"]

papers_dir = Path(__file__).resolve().parent / "papers"

for filename in files:
    pdf_path = papers_dir / filename
    print(f"\n{'=' * 15} {filename} {'=' * 15}")

    if not pdf_path.exists():
        print("PDF missing")
        continue

    with pymupdf.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf, start=1):
            text = page.get_text()
            if any(word in text.casefold() for word in keywords):
                print(f"\n--- PDF page {page_number} ---")
                print(text[:1800])