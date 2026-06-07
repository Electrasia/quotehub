"""
test_extractor.py — Quick test for the rules-based extractor.

Usage:
    python3 test_extractor.py [path-to-pdf] [--model-source=auto|model|part_no]
    python3 test_extractor.py --model-source=part_no  (tests all PDFs with part_no)

If no path is given, tests all PDFs in ~/Downloads/QuoDB_Test_Docs/.
"""
import sys
import os
import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.parser import parse_pdf
from backend.extract import extract_items


def test_one(pdf_path, model_source="auto"):
    print("=" * 80)
    print(f"FILE: {os.path.basename(pdf_path)}    [model_source={model_source}]")
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
        ext = extract_items(result, text, result["filename"], model_source=model_source)
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


def parse_args():
    """Parse args. Supports --model-source=VALUE flag and optional path."""
    model_source = "auto"
    pdf_path = None
    for arg in sys.argv[1:]:
        if arg.startswith("--model-source="):
            model_source = arg.split("=", 1)[1]
        elif not arg.startswith("-"):
            pdf_path = arg
    return pdf_path, model_source


if __name__ == "__main__":
    pdf_path, model_source = parse_args()
    if pdf_path:
        if not os.path.exists(pdf_path):
            print(f"File not found: {pdf_path}")
            sys.exit(1)
        test_one(pdf_path, model_source)
    else:
        test_dir = "/home/carlos/Downloads/QuoDB_Test_Docs"
        pdfs = sorted(
            f for f in glob.glob(f"{test_dir}/**/*.pdf", recursive=True)
            if "logs" not in f and "teste" not in f
        )
        if not pdfs:
            print(f"No PDFs found in {test_dir}")
            sys.exit(1)
        print(f"Testing {len(pdfs)} PDFs from {test_dir}    [model_source={model_source}]\n")
        for pdf in pdfs:
            test_one(pdf, model_source)
