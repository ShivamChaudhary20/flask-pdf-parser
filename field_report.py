"""Quick field extraction report across all test PDFs."""
import os, signal, sys

# Reduce OCR DPI for speed in this report
import pdf_parser
_orig_ocr = pdf_parser._ocr_pdf
def _fast_ocr(path):
    from pdf2image import convert_from_path
    import pytesseract
    images = convert_from_path(path, dpi=150, first_page=1, last_page=5)
    texts = [pytesseract.image_to_string(img) for img in images]
    return "\n".join(texts)
pdf_parser._ocr_pdf = _fast_ocr

from pdf_parser import extract_eob_data

PDF_DIR = "/home/user1/Downloads/Payment Posting OCR Bots/"

HEADER_FIELDS = [
    "patient_name", "claim_number", "member_id", "group_number",
    "date_of_service", "payer_name", "subscriber_name", "provider_name",
    "provider_npi", "rendering_provider", "billing_provider",
    "total_charge", "total_allowed", "total_paid", "check_number", "check_date"
]

LINE_FIELDS = [
    "cpt_code", "date_of_service", "billed", "allowed", "deductible",
    "copay", "coinsurance", "paid", "disallowed", "patient_resp",
    "paid_to_provider", "remark_codes"
]

pdfs = sorted([f for f in os.listdir(PDF_DIR) if f.lower().endswith('.pdf')])

print("=" * 100)
print("COMPREHENSIVE FIELD EXTRACTION REPORT")
print("=" * 100)

all_missing_header = {}
all_missing_line = {}
total_lines = 0
processed = 0

class Timeout(Exception): pass
def _handler(s, f): raise Timeout()

for pdf in pdfs:
    path = os.path.join(PDF_DIR, pdf)
    signal.signal(signal.SIGALRM, _handler)
    signal.alarm(45)
    try:
        data = extract_eob_data(path)
        signal.alarm(0)
    except Timeout:
        signal.alarm(0)
        print(f"\n{'─' * 100}")
        print(f"PDF: {pdf}")
        print(f"  ⏳ SKIPPED — timed out (>45s)")
        print(f"{'─' * 100}")
        continue
    except Exception as e:
        signal.alarm(0)
        print(f"\n{'─' * 100}")
        print(f"PDF: {pdf}")
        print(f"  [ERROR]: {e}")
        print(f"{'─' * 100}")
        continue

    processed += 1
    print(f"\n{'─' * 100}")
    print(f"PDF: {pdf}")
    used_ocr = data.get("_notice", "")
    if used_ocr:
        print(f"  [OCR USED]")
    print(f"{'─' * 100}")

    filled = []
    missing = []
    for f in HEADER_FIELDS:
        val = data.get(f, "")
        if val and str(val).strip():
            filled.append((f, str(val).strip()[:60]))
        else:
            missing.append(f)

    print(f"\n  HEADER FIELDS ({len(filled)}/{len(HEADER_FIELDS)} filled):")
    for name, val in filled:
        print(f"    [OK] {name:25s} = {val}")
    if missing:
        print(f"\n  MISSING HEADER FIELDS ({len(missing)}):")
        for f in missing:
            print(f"    [--] {f}")
            all_missing_header[f] = all_missing_header.get(f, 0) + 1

    lines = data.get("service_lines", [])
    total_lines += len(lines)
    print(f"\n  SERVICE LINES: {len(lines)} line(s)")
    for i, line in enumerate(lines):
        print(f"\n    Line {i+1}:")
        line_filled = []
        line_missing = []
        for f in LINE_FIELDS:
            val = line.get(f, "")
            if val and str(val).strip() and str(val).strip() != "0.00":
                line_filled.append((f, str(val).strip()[:50]))
            else:
                line_missing.append(f)
        for name, val in line_filled:
            print(f"      [OK] {name:20s} = {val}")
        if line_missing:
            for f in line_missing:
                print(f"      [--] {f}")
                all_missing_line[f] = all_missing_line.get(f, 0) + 1

print(f"\n\n{'=' * 100}")
print(f"SUMMARY: MOST COMMONLY MISSING FIELDS ACROSS {processed} PDFs")
print("=" * 100)

if all_missing_header:
    print(f"\n  HEADER FIELDS (missing count out of {processed} PDFs):")
    for f, count in sorted(all_missing_header.items(), key=lambda x: -x[1]):
        bar = "█" * count + "░" * (processed - count)
        print(f"    {f:25s}  {bar}  missing in {count}/{processed} PDFs")
else:
    print("\n  HEADER FIELDS: ALL filled in every PDF!")

if all_missing_line:
    print(f"\n  SERVICE LINE FIELDS (missing count out of {total_lines} total lines):")
    for f, count in sorted(all_missing_line.items(), key=lambda x: -x[1]):
        print(f"    {f:20s}  missing in {count}/{total_lines} lines")
else:
    print("\n  SERVICE LINE FIELDS: ALL filled in every line!")

print(f"\n{'=' * 100}")
print("DONE")
