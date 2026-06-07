"""
test_extractor.py — Quick test for the rules-based extractor.

Usage:
    python3 test_extractor.py [path-to-pdf]

If no path is given, tests all PDFs in ~/Downloads/QuoDB_Test_Docs/
"""
import sys
import os
import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.parser import parse_pdf
from backend.extract import extract_items


def test_one(pdf_path):
    print("=" * 80)
    print(f"FILE: {os.path.basename(pdf_path)}")
    print("=" * 80)
    try:
        result = parse_pdf(pdf_path)
        if "error" in result:
            print(f"  PARSE ERROR: {result['error']}")
            return
        if "parsers" not in result:
            print(f"  No parsers in result (keys: {list(result.keys())})")
            return
        pp = result["parsers"]["pdfplumber"]
        text = pp["pages"][0]["text"] if pp.get("pages") else ""
        ext = extract_items(result, text, result["filename"])
        print(f"  Supplier:    {ext['supplier'] or '(not detected)'}")
        print(f"  Date:        {ext['date'] or '(not detected)'}")
        print(f"  Currency:    {ext['currency'] or '(not detected)'}")
        print(f"  Doc type:    {ext['document_type']}")
        print(f"  Items:       {len(ext['items'])}")
        print()
        for i, it in enumerate(ext["items"][:10]):
            print(f"  [{i+1}] model='{it.get('model','')}'")
            print(f"      desc='{it.get('description','')}'")
            print(f"      qty={it.get('quantity','')} {it.get('unit','')} "
                  f"x {it.get('unit_price','')} = {it.get('total','')}")
        if len(ext["items"]) > 10:
            print(f"  ... and {len(ext['items']) - 10} more items")
        if ext["extraction_warnings"]:
            print()
            print(f"  WARNINGS:")
            for w in ext["extraction_warnings"]:
                print(f"    - {w}")
    except Exception as e:
        print(f"  ERROR: {e}")
    print()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        pdf = sys.argv[1]
        if not os.path.exists(pdf):
            print(f"File not found: {pdf}")
            sys.exit(1)
        test_one(pdf)
    else:
        test_dir = "/home/carlos/Downloads/QuoDB_Test_Docs"
        pdfs = sorted(
            f for f in glob.glob(f"{test_dir}/**/*.pdf", recursive=True)
            if "logs" not in f and "teste" not in f
        )
        if not pdfs:
            print(f"No PDFs found in {test_dir}")
            sys.exit(1)
        print(f"Testing {len(pdfs)} PDFs from {test_dir}\n")
        for pdf in pdfs:
            test_one(pdf)
