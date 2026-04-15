import re
import pdfplumber

try:
    from pdf2image import convert_from_path
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False


# ---------------------------------------------------------------------------
# Smart field-boundary terminator.
# Stops matching when it hits:  2+ spaces, a KNOWN LABEL pattern, or EOL.
# This prevents values from bleeding into adjacent fields on the same line.
# ---------------------------------------------------------------------------

_LABEL_LOOKAHEAD = (
    r"(?="
    r"\s{2,}"                       # two-or-more whitespace gap
    r"|\s+(?:"                      # OR a space followed by a known label keyword
    r"SUBSCRIBER|MEMBER|SERVICING|INTEREST|DATE|PCP|CLAIM|REMIT|PATIENT|"
    r"PRODUCT|BILLING|CARRIER|PROMPT|TOTAL|GRP|RA\s|PAYEE|PAYMENT|"
    r"PROVIDER|VENDOR|PLAN|DISPOSITION|STATUS|INSURED|POLICY|CONTRACT|"
    r"ENROLLEE|RENDERING|REFERRING|BENEFIT|COVERAGE|PAYER|INSURANCE|"
    r"LINE\sOF|LOB|CHECK|EFT|NET|ACCOUNT|TAX|DCN|TCN|TRACE|"
    r"REMARK|ADJUDICATION|PROCESSED|RECEIVED|GROUP|CLAIMANT|BENEFICIARY"
    r")(?:\s+(?:ID|NAME|NPI|NM|NUMBER|DETAIL|ACCOUNT|DESC|AMOUNT|AMT|"
    r"DATE|DISC|TAX|RECEIVED|REFERENCE|PAY|TYPE|CODE|HOLDER|STATUS|"
    r"PLAN|NO))*\.?\s*:"
    r"|$)"
)

# ---------------------------------------------------------------------------
# Alternate field patterns – each key maps to a list of regex alternatives.
# The first match wins, so put the most specific patterns first.
# ---------------------------------------------------------------------------

FIELD_PATTERNS = {
    "patient_name": [
        r"PATIENT:\s*(.+?)" + _LABEL_LOOKAHEAD,
        r"MEMBER\s*NAME:\s*(.+?)" + _LABEL_LOOKAHEAD,
        r"PATIENT\s*NAME:\s*(.+?)" + _LABEL_LOOKAHEAD,
        r"INSURED\s*(?:NAME)?:\s*(.+?)" + _LABEL_LOOKAHEAD,
        r"CLAIMANT\s*(?:NAME)?:\s*(.+?)" + _LABEL_LOOKAHEAD,
        r"BENEFICIARY\s*(?:NAME)?:\s*(.+?)" + _LABEL_LOOKAHEAD,
        r"\bNAME\s+([A-Z][A-Za-z,. \-]+?)\s+MID\b",  # Medicare
    ],
    "subscriber_id": [
        r"SUBSCRIBER\s*ID:\s*([\w-]+)",
        r"INSURED\s*ID:\s*([\w-]+)",
        r"POLICY\s*(?:NUMBER|#|NO)\.?:\s*([\w-]+)",
        r"GROUP\s*(?:NUMBER|#|NO)\.?:\s*([\w-]+)",
        r"CONTRACT\s*(?:NUMBER|#|NO)\.?:\s*([\w-]+)",
    ],
    "member_id": [
        r"MEMBER\s*ID:\s*([\w-]+)",
        r"ENROLLEE\s*ID:\s*([\w-]+)",
        r"ID\s*(?:NUMBER|#|NO)\.?:\s*([\w-]+)",
        r"\bMID\s+([\w]+)",  # Medicare
    ],
    "servicing_prov_npi": [
        r"SERVICING\s*PROV(?:IDER)?\s*NPI:\s*([\d]+)",
        r"RENDERING\s*(?:PROV(?:IDER)?)?\s*NPI:\s*([\d]+)",
        r"PERF(?:ORMING)?\s*PROV(?:IDER)?\s*(?:NPI)?:\s*([\d]+)",
    ],
    "subscriber_name": [
        r"SUBSCRIBER\s*NAME:\s*(.+?)" + _LABEL_LOOKAHEAD,
        r"INSURED\s*NAME:\s*(.+?)" + _LABEL_LOOKAHEAD,
        r"POLICY\s*HOLDER:\s*(.+?)" + _LABEL_LOOKAHEAD,
    ],
    "interest_amount": [
        r"INTEREST\s*(?:AMOUNT|AMT):\s*\$?([\d,.]+)",
    ],
    "servicing_prov_nm": [
        r"SERVICING\s*PROV(?:IDER)?\s*(?:NM|NAME):\s*(.+?)" + _LABEL_LOOKAHEAD,
        r"RENDERING\s*(?:PROV(?:IDER)?)?\s*(?:NM|NAME):\s*(.+?)" + _LABEL_LOOKAHEAD,
        r"PAYEE\s*NAME:\s*(.+?)" + _LABEL_LOOKAHEAD,
    ],
    "date_received": [
        r"DATE\s*RECEIVED:\s*([\d/]+)",
        r"DISPOSITION\s*DATE:\s*([\d/]+)",
        r"RECEIVED\s*DATE:\s*([\d/]+)",
        r"PROCESSED?\s*DATE:\s*([\d/]+)",
        r"ADJUDICATION\s*DATE:\s*([\d/]+)",
        r"PAID\s*DATE:\s*([\d/]+)",
        r"CHECK\s*DATE:\s*([\d/]+)",
        r"PAYMENT\s*DATE:\s*([\d/]+)",
        r"REMIT(?:TANCE)?\s*DATE:\s*([\d/]+)",
        r"\bDATE:\s*([\d/]+)",  # Medicare
    ],
    "pcp_number": [
        r"PCP\s*NUMBER:\s*([\d]+)",
        r"PROVIDER\s*ID:\s*([\w]+)",
        r"PROV(?:IDER)?\s*(?:NUMBER|#|NO)\.?:\s*([\w]+)",
        r"TAX\s*ID:\s*([\d-]+)",
    ],
    "claim_number": [
        r"CLAIM\s*(?:NUMBER|#|NO)\.?:\s*([\w-]+)",
        r"REFERENCE\s*(?:NUMBER|#|NO)\.?:\s*([\w-]+)",
        r"DCN:\s*([\w-]+)",
        r"TCN:\s*([\w-]+)",
        r"TRACE\s*(?:NUMBER|#):\s*([\w-]+)",
        r"\bICN\s+(\d{10,})",  # Medicare
    ],
    "remit_detail": [
        r"REMIT\s*DETAIL:\s*(.+?)" + _LABEL_LOOKAHEAD,
        r"STATUS:\s*(.+?)" + _LABEL_LOOKAHEAD,
        r"CLAIM\s*STATUS:\s*(.+?)" + _LABEL_LOOKAHEAD,
        r"REMARK\s*CODE:\s*(.+?)" + _LABEL_LOOKAHEAD,
    ],
    "pcp_name": [
        r"PCP\s*NAME:\s*(.+?)" + _LABEL_LOOKAHEAD,
        r"REFERRING\s*(?:PROV(?:IDER)?)?(?:\s*NAME)?:\s*(.+?)" + _LABEL_LOOKAHEAD,
    ],
    "patient_account": [
        r"PATIENT\s*ACCOUNT\s*(?:NUMBER|#|NO)?\.?:\s*([\w]+)",
        r"VENDOR\s*ID:\s*([\w]+)",
        r"ACCOUNT\s*(?:NUMBER|#|NO)\.?:\s*([\w]+)",
        r"\bACNT\s+([\w]+)",  # Medicare
    ],
    "product_desc": [
        r"PRODUCT\s*DESC\.?:?\s*(.+?)" + _LABEL_LOOKAHEAD,
        r"PLAN\s*NAME:\s*(.+?)" + _LABEL_LOOKAHEAD,
        r"PLAN\s*(?:DESC(?:RIPTION)?|TYPE):\s*(.+?)" + _LABEL_LOOKAHEAD,
        r"BENEFIT\s*PLAN:\s*(.+?)" + _LABEL_LOOKAHEAD,
        r"COVERAGE\s*(?:TYPE|DESC):\s*(.+?)" + _LABEL_LOOKAHEAD,
        r"LOB:\s*(.+?)" + _LABEL_LOOKAHEAD,
        r"LINE\s*OF\s*BUSINESS:\s*(.+?)" + _LABEL_LOOKAHEAD,
    ],
    "billing_npi": [
        r"BILLING\s*(?:PROV(?:IDER)?)?\s*NPI:\s*([\d]+)",
        r"\bNPI:\s*([\d]+)",  # Medicare
    ],
    "carrier_id": [
        r"CARRIER\s*ID:\s*([\w]+)",
        r"PAYER\s*ID:\s*([\w]+)",
        r"INSURANCE\s*ID:\s*([\w]+)",
    ],
    "carrier_name": [
        r"PAYER\s*(?:NAME)?:\s*(.+?)" + _LABEL_LOOKAHEAD,
        r"INSURANCE\s*CO(?:MPANY)?:\s*(.+?)" + _LABEL_LOOKAHEAD,
        r"CARRIER:\s*(.+?)" + _LABEL_LOOKAHEAD,
    ],
    "payment_number": [
        r"PAYMENT\s*(?:NUMBER|#|NO)\.?:\s*([\w-]+)",
        r"CHECK\s*(?:NUMBER|#|NO)\.?:\s*([\w-]+)",
        r"EFT\s*(?:NUMBER|#|NO|TRACE)\.?:\s*([\w-]+)",
        r"TRACE\s*(?:NUMBER|#):\s*([\w-]+)",
    ],
    "payment_date": [
        r"PAYMENT\s*DATE:\s*([\d/]+)",
        r"CHECK\s*DATE:\s*([\d/]+)",
        r"REMIT(?:TANCE)?\s*DATE:\s*([\d/]+)",
        r"DEPOSIT\s*DATE:\s*([\d/]+)",
    ],
    "total_payable_to_provider": [
        r"TOTAL\s*PAYABLE\s*TO\s*PROVIDER:?\s*\$?([\d,.]+)",
        r"PAYMENT\s*AMOUNT:\s*\$?([\d,.]+)",
        r"TOTAL\s*(?:NET\s*)?PAYMENT:?\s*\$?([\d,.]+)",
        r"CHECK\s*(?:AMOUNT|AMT):?\s*\$?([\d,.]+)",
        r"EFT\s*(?:AMOUNT|AMT):?\s*\$?([\d,.]+)",
        r"TOTAL\s*PAID:?\s*\$?([\d,.]+)",
        r"NET\s*(?:AMOUNT|AMT):?\s*\$?([\d,.]+)",
    ],
}

# ---------------------------------------------------------------------------
# Column-name aliases → our standard service-line key.
# Normalised to lowercase for matching.
# ---------------------------------------------------------------------------

COLUMN_ALIASES = {
    # date
    "date_of_service": [
        "date(s) of service", "date of service", "dates of service",
        "service dates", "service date", "dos", "serv date",
        "from date", "thru date", "date from", "date to",
        "begin date", "end date", "svc date", "service from",
        "line date", "stmt date",
    ],
    # description / procedure
    "description": [
        "description of service submitted/adjudicated", "description of service",
        "description", "service code/units", "service code", "procedure",
        "proc code", "cpt", "cpt code", "proc", "hcpcs", "hcpc",
        "revenue code", "rev code", "type of service", "tos",
        "procedure code", "service description", "service desc",
        "modifier", "mods",
    ],
    # units
    "units": ["units", "unit", "qty", "quantity", "nos", "days or units",
              "days/units", "days", "count", "svc units"],
    # billed
    "billed_amt": [
        "billed amt", "billed amount", "billed", "charge amt",
        "charge amt ($)", "charge amount", "charges", "total charge",
        "total charges", "submitted amt", "submitted amount", "submitted",
        "gross amt", "gross amount", "gross charge", "original amt",
        "amount billed", "bill amt", "chg amt", "chg",
    ],
    # disallow
    "disallow_amt": [
        "disallow amt", "disallow amount", "disallow", "disallowed",
        "discount amt", "discount", "non-covered", "noncovered",
        "not covered", "ineligible", "reduction", "adjustment",
        "adj amt", "contractual", "contractual adj", "write off",
        "writeoff", "write-off",
    ],
    # allowed
    "allowed_amt": [
        "allowed amt", "allowed amount", "allowed", "considered amt",
        "eligible amt", "eligible amount", "eligible", "approved amt",
        "approved amount", "approved", "covered amt", "covered amount",
        "recognized", "recognized amt", "plan allowed",
        "max allowable", "fee schedule",
    ],
    # deduct
    "deduct_amt": [
        "deduct amt", "deduct amount", "deduct", "deductible",
        "ded amt", "ded", "annual deductible", "applied to deductible",
        "deduct applied",
    ],
    # copay / coins
    "copay_coins_amt": [
        "copay/coins amt", "copay/coins", "copay coins amt",
        "copay", "coin", "coins", "coinsurance", "copay/coinsurance",
        "co-pay", "co-insurance", "coinsurance amt", "copay amt",
        "member coins", "member copay", "patient copay",
        "copay/coins/ded", "cost share",
    ],
    # cob
    "cob_pmt_amt": [
        "cob pmt amt", "cob pmt amount", "cob payment", "cob",
        "other insurance", "other ins", "other payer",
        "other carrier", "oop amt", "coordination of benefits",
        "secondary payment", "primary paid",
    ],
    # withhold
    "withhold_amt": [
        "withhold amt", "withhold amount", "withhold",
        "withholding", "w/h", "w/h amt", "capitation",
    ],
    # paid to provider
    "paid_to_provider_amt": [
        "paid to provider amt", "paid to provider amount", "paid to provider",
        "amt paid", "amt paid ($)", "amount paid", "paid amt", "paid amount",
        "paid", "net paid", "prov pd", "net payment", "net pay",
        "payment amt", "payment amount", "payment", "pay amount",
        "benefit paid", "benefit amount", "benefit amt",
        "plan paid", "plan payment", "provider payment",
        "check amount", "check amt", "reimbursement",
        "reimburse", "reimb", "total paid",
    ],
    # patient resp
    "patient_resp_amt": [
        "patient resp amt", "patient resp amount", "patient resp",
        "patient responsibility", "member resp", "member responsibility",
        "patient liability", "member liability", "pt resp",
        "patient balance", "member balance", "patient owe",
        "patient portion", "member portion", "amount due",
        "balance due", "you owe", "your responsibility",
    ],
}


def _normalise(text):
    """Lower-case, collapse whitespace, strip $ and special chars for matching."""
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text).lower().strip())


# Keyword sets for fuzzy fallback column detection.
# If no exact alias matches, check if the header contains these keywords.
_KEYWORD_MAP = {
    "date_of_service": {"date", "dos", "from", "serv"},
    "description":     {"desc", "proc", "cpt", "hcpc", "code"},
    "units":           {"unit", "qty", "day", "nos", "count"},
    "billed_amt":      {"bill", "charge", "chg", "submit", "gross"},
    "disallow_amt":    {"disallow", "discount", "noncov", "non-cov", "inelig", "writeoff", "write-off", "adj"},
    "allowed_amt":     {"allow", "elig", "approv", "cover", "recogn", "fee sched"},
    "deduct_amt":      {"deduct", "ded"},
    "copay_coins_amt": {"copay", "co-pay", "coin", "coinsur", "cost share"},
    "cob_pmt_amt":     {"cob", "coord", "other ins", "other pay", "secondary"},
    "withhold_amt":    {"withhold", "w/h", "capitat"},
    "paid_to_provider_amt": {"paid", "pay", "prov pd", "net", "reimb", "benefit", "check"},
    "patient_resp_amt":     {"patient resp", "member resp", "liabil", "pt resp", "balance due", "you owe", "portion"},
}


def _match_column(header_text):
    """Return our standard key for a given column header, or None.

    Strategy:
    1. Exact alias match (fast, precise)
    2. Keyword-based fuzzy match (catches unknown variations)
    """
    norm = _normalise(header_text)
    if not norm:
        return None

    # --- Pass 1: exact alias match ---
    for key, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias == norm or alias in norm:
                return key

    # --- Pass 2: keyword-based fuzzy match ---
    # Score each candidate by how many keywords appear in the header.
    best_key = None
    best_score = 0
    for key, keywords in _KEYWORD_MAP.items():
        score = sum(1 for kw in keywords if kw in norm)
        if score > best_score:
            best_score = score
            best_key = key
    # Require at least 1 keyword match
    if best_score >= 1:
        return best_key

    return None


def _clean_amount(val):
    """Strip $, commas, whitespace from an amount string."""
    if not val:
        return "0.00"
    cleaned = re.sub(r"[$ ,]", "", str(val).strip())
    if cleaned == "" or cleaned == "-":
        return "0.00"
    return cleaned


def _new_data_record():
    """Create a fresh parsed-record payload with default values."""
    return {
        "patient_name": "",
        "subscriber_id": "",
        "member_id": "",
        "servicing_prov_npi": "",
        "subscriber_name": "",                                                                  
        "interest_amount": "",
        "servicing_prov_nm": "",
        "date_received": "",
        "pcp_number": "",
        "claim_number": "",
        "remit_detail": "",
        "pcp_name": "",
        "patient_account": "",
        "product_desc": "",
        "billing_npi": "",
        "carrier_id": "",
        "carrier_name": "",
        "payment_number": "",
        "payment_date": "",
        "total_payable_to_provider": "",
        "service_lines": [],
    }


# ---------------------------------------------------------------------------
# OCR fallback for scanned / image-based PDFs
# ---------------------------------------------------------------------------

def _ocr_pdf(pdf_path, max_pages=5, timeout_per_page=15):
    """Convert PDF to images and run OCR on each page.

    Uses a per-page timeout and page limit to avoid hanging on large
    scanned documents.  DPI kept at 150 for speed; sufficient for
    typical EOB/remittance text.

    Automatically detects and corrects page rotation via Tesseract OSD
    before running OCR, which fixes sideways/rotated scanned pages.
    """
    try:
        images = convert_from_path(pdf_path, dpi=150, first_page=1,
                                   last_page=max_pages)
        pages_text = []
        for img in images:
            try:
                # Detect rotation via OSD and correct before OCR
                try:
                    osd = pytesseract.image_to_osd(img, timeout=timeout_per_page)
                    rot_line = [l for l in osd.split('\n') if l.startswith('Rotate:')]
                    rot = int(rot_line[0].split(':')[1].strip()) if rot_line else 0
                    if rot != 0:
                        img = img.rotate(-rot, expand=True)
                except Exception:
                    pass  # OSD failed — proceed with original orientation

                text = pytesseract.image_to_string(img, timeout=timeout_per_page)
                pages_text.append(text)
            except RuntimeError:
                # Timeout on this page — skip it
                pages_text.append("")
        return "\n".join(pages_text)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------

def extract_eob_data(pdf_path):
    """Extract medical billing EOB data from a PDF file."""
    full_text = ""
    tables = []
    pdfplumber_failed = False

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                full_text += page_text + "\n"
                page_tables = page.extract_tables()
                if page_tables:
                    tables.extend(page_tables)
    except Exception:
        # Handles circular reference, corrupt PDF structure, etc.
        # Try pypdf as fallback (different parser, more lenient)
        try:
            from pypdf import PdfReader
            reader = PdfReader(pdf_path)
            for page in reader.pages:
                page_text = page.extract_text() or ""
                full_text += page_text + "\n"
        except Exception:
            pdfplumber_failed = True

    # --- OCR fallback for image-based / scanned PDFs ---
    text_stripped = full_text.strip()
    used_ocr = False
    if pdfplumber_failed or len(text_stripped) < 50:
        if OCR_AVAILABLE:
            ocr_text = _ocr_pdf(pdf_path)
            if ocr_text.strip():
                full_text = ocr_text
                used_ocr = True
            else:
                # OCR ran but extracted nothing useful
                data = _new_data_record()
                data["_notice"] = (
                    "This PDF is an image/scanned document. "
                    "OCR could not extract readable text. "
                    "Please try a higher-quality scan."
                )
                return data
        else:
            data = _new_data_record()
            data["_notice"] = (
                "This PDF is an image/scanned document. "
                "Install tesseract to enable OCR extraction."
            )
            return data

    # ---- Aetna "Summary of Claim Payment" (reversed text) early detection ----
    if "tneitaP :emaN" in full_text or "noitanalpxE fO stifeneB" in full_text:
        return _extract_aetna_claims(full_text, tables)

    # ---- Aetna "Explanation of Benefits" (normal text, multi-patient) ----
    if _is_aetna_eob(full_text):
        return _extract_aetna_eob_claims(full_text, tables)

    # ---- UHC "Provider Remittance Advice" (OCR, multi-patient) ----
    if _is_uhc_remittance(full_text):
        result = _extract_uhc_remittance_claims(full_text)
        if used_ocr:
            result["_notice"] = ("This PDF is an image/scanned document. "
                                 "Data was extracted using OCR — please verify accuracy.")
        return result

    data = _new_data_record()

    # ---- 1. Extract header fields using multi-pattern matching ----
    lines = full_text.split("\n")
    remaining_fields = set(FIELD_PATTERNS.keys())
    for line in lines:
        if not remaining_fields:
            break  # all fields found — stop scanning
        matched_in_line = []
        for field in remaining_fields:
            for pattern in FIELD_PATTERNS[field]:
                m = re.search(pattern, line, re.IGNORECASE)
                if m:
                    data[field] = m.group(1).strip()
                    matched_in_line.append(field)
                    break
        for f in matched_in_line:
            remaining_fields.discard(f)

    # ---- 1b. Header-row / value-row extraction (portal-style PDFs) ----
    # Priority Health and similar portals use "header row then value row" format.
    # We match specific known header patterns and parse the corresponding value line.
    _hv_patterns = [
        # (header regex, value regex with named groups)
        (
            r"Status\s+Member\s*Name\s+Contract\s*Number\s+Service\s*Date",
            r"\S+\s+(?P<patient_name>.+?)\s+(?P<member_id>\d[\d-]+)\s+(?P<date_received>\d{1,2}/\d{1,2}/\d{2,4})"
        ),
        (
            r"Provider\s+Claim\s*Number\s+Paid\s*Date\s+Total\s+(?:Priority|Plan)\s*Paid",
            r"(?P<servicing_prov_nm>.+?)\s+(?P<claim_number>\d{6,})\s+(?P<payment_date>\d{1,2}/\d{1,2}/\d{2,4})\s+\$?(?P<total_payable_to_provider>[\d,.]+)"
        ),
        (
            r"Billed\s*Amount\s+Account\s*Number\s+Medical\s*Plan\s+Provider",
            r"\$?[\d,.]+\s+(?P<member_id_alt>\w+)\s+(?P<product_desc>\S+)\s+(?P<servicing_prov_nm_alt>.+)"
        ),
        (
            r"Provider\s*Id\s+Tax\s*Id\s+Service\s*Date\s+Claim\s*Received",
            r"(?P<pcp_number>\d+)\s+\S+\s+(?P<svc_date>[\d-]+)\s+(?P<claim_received>[\d-]+)"
        ),
        (
            r"Paid\s*Date\s+Submitted\s*DRG\s+Voucher\s*#\s+Check\s*#",
            r"[\d-]+\s+\S+\s+(?P<payment_number_alt>\w+)\s+(?P<payment_number>\d+)"
        ),
    ]
    for i, line in enumerate(lines[:-1]):
        stripped = line.strip()
        for hdr_re, val_re in _hv_patterns:
            if not re.search(hdr_re, stripped, re.IGNORECASE):
                continue
            # Get next non-empty, non-nav line
            for j in range(i + 1, min(i + 3, len(lines))):
                candidate = lines[j].strip()
                if candidate and not candidate.startswith("http") and not candidate.startswith("(/"):
                    m = re.match(val_re, candidate, re.IGNORECASE)
                    if m:
                        for key, val in m.groupdict().items():
                            val = (val or "").strip()
                            if not val or val == "--":
                                continue
                            # Handle _alt fields (only fill if main is empty)
                            real_key = key.replace("_alt", "")
                            if real_key == "servicing_prov_nm":
                                val = val.replace(" null ", " ").strip()
                            if real_key == "svc_date":
                                if not data.get("date_received"):
                                    data["date_received"] = val
                                continue
                            if real_key == "claim_received":
                                if not data.get("date_received"):
                                    data["date_received"] = val
                                continue
                            if not data.get(real_key):
                                data[real_key] = val
                    break

    # ---- 1c. Priority Health service line extraction from text ----
    if not data.get("_ph_svc_extracted"):
        for i, line in enumerate(lines):
            # Look for "Code Description Units Billed Amount"
            if re.search(r"Code\s+Description\s+Units\s+Billed", line, re.IGNORECASE):
                # Next non-empty line(s) contain the service line data
                for j in range(i + 1, min(i + 5, len(lines))):
                    svc_line = lines[j].strip()
                    if not svc_line or svc_line.lower().startswith("deductible"):
                        break
                    # Pattern: CODE DESCRIPTION UNITS $AMOUNT
                    m = re.match(r"(\w+)\s+(.+?)\s+(\d+)\s+\$?([\d,.]+)", svc_line)
                    if m:
                        code, desc, units, billed = m.group(1), m.group(2).strip(), m.group(3), m.group(4)
                        sl = {
                            "date_of_service": data.get("date_received", ""),
                            "description": f"{code} {desc}",
                            "units": units,
                            "billed_amt": billed,
                            "disallow_amt": "0.00",
                            "allowed_amt": "0.00",
                            "deduct_amt": "0.00",
                            "copay_coins_amt": "0.00",
                            "cob_pmt_amt": "0.00",
                            "withhold_amt": "0.00",
                            "paid_to_provider_amt": "0.00",
                            "patient_resp_amt": "0.00",                                                                                                                                                                     
                        }
                        data["service_lines"].append(sl)
                break
        # Fill amounts from "Line Paid Detail" section
        for i, line in enumerate(lines):
            if re.search(r"Line\s+Paid\s+Detail", line, re.IGNORECASE):
                # Next lines: header then values
                for j in range(i + 1, min(i + 6, len(lines))):
                    hdr = lines[j].strip()
                    if re.search(r"Allowed\s+.*(?:Other|Insurance|Capitation)", hdr, re.IGNORECASE):
                        if j + 1 < len(lines):
                            vals = re.findall(r"\$?([\d,.]+)", lines[j + 1])
                            if vals and data["service_lines"]:
                                sl = data["service_lines"][-1]
                                sl["allowed_amt"] = vals[0]
                    if re.search(r"Total\s+Patient\s+Liability", hdr, re.IGNORECASE):
                        if j + 1 < len(lines):
                            vals = re.findall(r"\$?([\d,.]+)", lines[j + 1])
                            if vals and data["service_lines"]:
                                sl = data["service_lines"][-1]
                                sl["patient_resp_amt"] = vals[0]
                                if len(vals) >= 2:
                                    sl["copay_coins_amt"] = vals[1]
                                if len(vals) >= 3:
                                    sl["deduct_amt"] = vals[2]
                    if re.search(r"Priority\s+Health\s+Paid", hdr, re.IGNORECASE):
                        vals = re.findall(r"\$?([\d,.]+)", hdr)
                        if not vals and j + 1 < len(lines):
                            vals = re.findall(r"\$?([\d,.]+)", lines[j + 1])
                        if vals and data["service_lines"]:
                            sl = data["service_lines"][-1]
                            sl["paid_to_provider_amt"] = vals[-1]
                break
        # Compute disallow from billed - allowed
        for sl in data["service_lines"]:
            try:
                b = float(sl.get("billed_amt", 0))
                a = float(sl.get("allowed_amt", 0))
                if b > a > 0:
                    sl["disallow_amt"] = "%.2f" % (b - a)
            except (ValueError, TypeError):
                pass

    # ---- 1e. Priority Health carrier detection ----
    if not data.get("carrier_name"):
        for line in lines:
            if re.search(r"priority\s*health", line, re.IGNORECASE):
                data["carrier_name"] = "Priority Health"
                break
            if "priorityhealth.com" in line.lower():
                data["carrier_name"] = "Priority Health"
                break

    # ---- 2. Extract service lines from tables (smart column mapping) ----
    _extract_service_lines_from_tables(tables, data)

    # ---- 3. Medicare Remittance Advice (fixed-width text) ----
    if not data["service_lines"]:
        _extract_medicare_claims(full_text, lines, data)

    # ---- 4. Fallback: regex-based service line extraction from text ----
    if not data["service_lines"]:
        _extract_service_lines_from_text(lines, data)

    # ---- 5. Generic OCR line extractor (date + amounts on same line) ----
    if not data["service_lines"]:
        _extract_ocr_service_lines(lines, data)

    # ---- 6. Last resort: grab individual amounts from raw text ----
    if not data["service_lines"]:
        _extract_amounts_from_text(full_text, data)

    # ---- 7. Extract carrier name from text if still empty ----
    if not data["carrier_name"]:
        carrier_re = re.compile(
            r"(?:insurance|healthcare|health\s*plan|behavioral\s*health|"
            r"blue\s*cross|blue\s*shield|bcbs|"
            r"aetna|cigna|humana|united\s*health|anthem|kaiser|molina|centene|"
            r"wellcare|ambetter|medicaid|medicare|tricare|"
            r"carelon|optum|magellan|beacon|"
            r"wellpoint|highmark|carefirst|premera|regence|"
            r"providence\s+health|medical\s*mutual)",
            re.IGNORECASE
        )
        # Check first few lines (common header location)
        for line in lines[:10]:
            stripped = line.strip()
            if stripped and carrier_re.search(stripped):
                # Skip nav/breadcrumb lines and label-prefixed lines
                if ">" in stripped and "home" in stripped.lower():
                    continue
                if re.match(r"^(plan\s*name|product\s*desc)", stripped, re.IGNORECASE):
                    continue
                data["carrier_name"] = stripped
                break
        # If still not found, scan whole document
        if not data["carrier_name"]:
            for line in lines:
                stripped = line.strip()
                if stripped and carrier_re.search(stripped) and len(stripped) < 80:
                    if ">" in stripped and "home" in stripped.lower():
                        continue
                    if re.match(r"^(plan\s*name|product\s*desc)", stripped, re.IGNORECASE):
                        continue
                    data["carrier_name"] = stripped
                    break

        # Clean up OCR-garbled carrier names using known mappings
        if data["carrier_name"]:
            _CARRIER_CLEAN = {
                "carelon": "Carelon Behavioral Health",
                "united": "UnitedHealthcare",
                "unitedhealth": "UnitedHealthcare",
                "aetna": "Aetna",
                "cigna": "Cigna",
                "anthem": "Anthem",
                "humana": "Humana",
                "kaiser": "Kaiser Permanente",
                "molina": "Molina Healthcare",
                "bcbs": "Blue Cross Blue Shield",
                "blue cross": "Blue Cross Blue Shield",
                "optum": "Optum",
                "magellan": "Magellan Health",
                "beacon": "Beacon Health",
            }
            for keyword, clean_name in _CARRIER_CLEAN.items():
                if keyword in data["carrier_name"].lower():
                    data["carrier_name"] = clean_name
                    break

    # ---- 8. Compute missing fields from what we have ----
    _compute_derived_fields(data)

    # ---- 9. Build multi-record output for Medicare multi-patient PDFs ----
    multi_records = _extract_medicare_records(full_text, lines, data)
    if len(multi_records) > 1:
        data["_records"] = multi_records

    # ---- 8. Add processing notice ----
    if used_ocr:
        data["_notice"] = "This PDF is an image/scanned document. Data was extracted using OCR — please verify accuracy."
    return data


def _compute_derived_fields(data):
    """Fill in fields that can be calculated from other fields."""
    for sl in data["service_lines"]:
        billed = _safe_float(sl.get("billed_amt"))
        allowed = _safe_float(sl.get("allowed_amt"))
        disallow = _safe_float(sl.get("disallow_amt"))
        deduct = _safe_float(sl.get("deduct_amt"))
        copay = _safe_float(sl.get("copay_coins_amt"))
        paid = _safe_float(sl.get("paid_to_provider_amt"))
        pt_resp = _safe_float(sl.get("patient_resp_amt"))

        # disallow = billed - allowed (if missing)
        if disallow == 0 and billed > 0 and allowed > 0 and billed > allowed:
            sl["disallow_amt"] = f"{billed - allowed:.2f}"

        # patient_resp = deduct + copay (if missing and both exist)
        if pt_resp == 0 and (deduct > 0 or copay > 0):
            sl["patient_resp_amt"] = f"{deduct + copay:.2f}"

        # paid_to_provider = allowed - deduct - copay (if missing)
        if paid == 0 and allowed > 0:
            computed_paid = allowed - deduct - copay
            if computed_paid > 0:
                sl["paid_to_provider_amt"] = f"{computed_paid:.2f}"

    # total_payable_to_provider from service lines if still empty
    if not data["total_payable_to_provider"] and data["service_lines"]:
        total = sum(_safe_float(sl.get("paid_to_provider_amt")) for sl in data["service_lines"])
        if total > 0:
            data["total_payable_to_provider"] = f"{total:.2f}"


def _safe_float(val):
    """Safely convert a string value to float, return 0.0 on failure."""
    if not val:
        return 0.0
    try:
        return float(str(val).replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        return 0.0


def _extract_service_lines_from_tables(tables, data):
    """Try every table; use the header row to map columns intelligently."""
    best_lines = []
    best_score = 0

    for table in tables:
        if not table or len(table) < 2:
            continue

        # Skip summary / totals tables
        first_cell = _normalise(str(table[0][0]) if table[0] and table[0][0] else "")
        if any(kw in first_cell for kw in ("service provider", "payee id", "payee totals",
                                             "provider totals", "net payable")):
            continue

        # Skip extremely fragmented tables (character-level splits)
        if table[0] and len(table[0]) > 50:
            continue

        # --- Try to detect header row ---
        # Sometimes the header spans multiple rows (multi-line headers).
        # Try first row, and also try joining first two rows.
        header_candidates = [table[0]]
        if len(table) > 2 and table[1]:
            # Merge row 0 and row 1 cell-by-cell for multi-line headers
            merged = []
            for i in range(max(len(table[0]), len(table[1]))):
                c0 = str(table[0][i]).strip() if i < len(table[0]) and table[0][i] else ""
                c1 = str(table[1][i]).strip() if i < len(table[1]) and table[1][i] else ""
                merged.append(f"{c0} {c1}".strip())
            header_candidates.append(merged)

        for header_row in header_candidates:
            col_map = {}
            for idx, cell in enumerate(header_row):
                key = _match_column(cell)
                if key and key not in col_map.values():
                    col_map[idx] = key

            # Need at least 2 recognised columns to consider this a service table
            if len(col_map) < 2:
                continue

            # Determine which rows are data (skip headers we used)
            data_start = 2 if header_row is not header_candidates[0] else 1

            candidate_lines = []
            for row in table[data_start:]:
                if not row:
                    continue
                cells = [str(c).strip() if c else "" for c in row]

                # Skip rows that look like sub-headers / totals / empty
                joined = _normalise(" ".join(cells))
                if not joined:
                    continue
                if "total" in joined and "payable" not in joined:
                    continue
                if "subtotal" in joined:
                    continue
                if joined.startswith("claim number"):
                    continue
                # Skip rows that are just a repeat of header text
                if "date" in joined and "service" in joined and "billed" in joined:
                    continue

                service_line = {
                    "date_of_service": "",
                    "description": "",
                    "units": "1",
                    "billed_amt": "0.00",
                    "disallow_amt": "0.00",
                    "allowed_amt": "0.00",
                    "deduct_amt": "0.00",
                    "copay_coins_amt": "0.00",
                    "cob_pmt_amt": "0.00",
                    "withhold_amt": "0.00",
                    "paid_to_provider_amt": "0.00",
                    "patient_resp_amt": "0.00",
                }

                for idx, key in col_map.items():
                    if idx >= len(cells):
                        continue
                    val = cells[idx]
                    # Handle newlines in cells (multi-line values)
                    val = val.replace("\n", " ").strip()

                    if key == "date_of_service":
                        # Extract first date-like value, handle "MM/DD/YY - MM/DD/YY"
                        dm = re.search(r"(\d{2}/\d{2}/\d{2,4})", val)
                        service_line[key] = dm.group(1) if dm else val
                    elif key == "description":
                        service_line[key] = val
                    elif key == "units":
                        # Handle "code/units" format like "99214/1"
                        unit_match = re.search(r"(\d+)$", val)
                        service_line[key] = unit_match.group(1) if unit_match else val
                    else:
                        service_line[key] = _clean_amount(val)

                # Only add if there's meaningful data
                has_date = bool(re.search(r"\d{2}/\d{2}", service_line["date_of_service"]))
                has_amount = any(
                    service_line[k] not in ("", "0.00")
                    for k in ("billed_amt", "allowed_amt", "paid_to_provider_amt")
                )
                if has_date or has_amount:
                    candidate_lines.append(service_line)

            # Keep the table that yields the most complete service lines
            score = 0
            for sl in candidate_lines:
                score += sum(1 for v in sl.values() if v and v != "0.00")
            if score > best_score:
                best_score = score
                best_lines = candidate_lines

    if best_lines:
        data["service_lines"] = best_lines


# ---------------------------------------------------------------------------
# UHC "Provider Remittance Advice" — OCR, multi-patient
# ---------------------------------------------------------------------------

def _is_uhc_remittance(full_text):
    """Detect UHC Provider Remittance Advice format."""
    has_pra = bool(re.search(r"PROVIDER\s+REMITTANCE\s+ADVICE", full_text, re.IGNORECASE))
    has_subscriber = bool(re.search(r"SUBSCRIBER\s+ID:", full_text))
    has_uhc = bool(re.search(r"UnitedHealthcare", full_text, re.IGNORECASE))
    return has_pra and has_subscriber and has_uhc


def _extract_uhc_remittance_claims(full_text):
    """Extract multi-patient claims from UHC Provider Remittance Advice OCR text.

    Structure:
    - Header with PAYMENT DATE, PAYEE info, PAYMENT AMOUNT
    - Per-patient blocks starting with SUBSCRIBER ID: line
    - Each block: header fields on SUBSCRIBER/MEMBER lines, then a
      SUBTOTAL line with CLAIM NUMBER + amounts
    - PAYEE TOTALS at the end
    """
    base = _new_data_record()
    base["carrier_name"] = "UnitedHealthcare"

    # --- Header fields ---
    m = re.search(r"PAYMENT\s+DATE:\s*(\d{2}/\d{2}/\d{2,4})", full_text)
    if m:
        base["payment_date"] = m.group(1)

    # Payment number — OCR often splits label/value across lines
    m = re.search(r"PAYMENT\s+NUMBER:\s*(\d\S+)", full_text)
    if not m:
        m = re.search(r"(\d{5}[A-Z]\d{10,})", full_text)
    if m:
        base["payment_number"] = m.group(1)

    m = re.search(r"PAYMENT\s+AMOUNT:\s*\$?([\d,.]+)", full_text)
    if not m:
        # Look for NET PAID AMOUNT in the header
        m = re.search(r"NET\s+PAID\s+AMOUNT\s+\$?([\d,.]+)", full_text)
    if m:
        base["total_payable_to_provider"] = m.group(1).replace(",", "")

    # --- Split at PATIENT: (page header) or SUBSCRIBER ID: to get per-patient blocks ---
    # UHC format: each page starts with "PATIENT: <name>" followed by "SUBSCRIBER ID: ..."
    # We split at PATIENT: when available, otherwise at SUBSCRIBER ID:
    blocks = re.split(r"(?=PATIENT:\s+[A-Z]|(?<!PATIENT[^\n]{0,50})\bSUBSCRIBER\s+ID:)", full_text)
    subscriber_blocks = [b for b in blocks if re.search(r"SUBSCRIBER\s+ID:", b)]

    if not subscriber_blocks:
        return base

    records = []
    for block in subscriber_blocks:
        rec = _new_data_record()
        rec["carrier_name"] = base["carrier_name"]
        rec["payment_date"] = base.get("payment_date", "")
        rec["payment_number"] = base.get("payment_number", "")

        # Subscriber ID
        m = re.search(r"SUBSCRIBER\s+ID:\s*(\S+)", block)
        if m:
            rec["subscriber_id"] = m.group(1)

        # Subscriber Name
        m = re.search(r"SUBSCRIBER\s+NAME:\s*([A-Z][A-Z ,.'`-]+?)(?:\s+DATE\s+RECEIVED:|\s*$)", block, re.MULTILINE)
        if m:
            rec["subscriber_name"] = m.group(1).strip().rstrip(",")

        # Patient name — prefer explicit PATIENT: label; fall back to subscriber name
        m = re.search(r"PATIENT:\s*([A-Z][A-Z ,.'`-]+?)(?:\s+SUBSCRIBER\s+ID:|\s+SUBSCRIBER\s+NAME:|\s+DATE\s+RECEIVED:|\n|\s*$)", block, re.MULTILINE)
        if m:
            rec["patient_name"] = m.group(1).strip()
        else:
            rec["patient_name"] = rec["subscriber_name"]

        # Date Received
        m = re.search(r"DATE\s+RECEIVED:\s*([\d/]+)", block)
        if m:
            rec["date_received"] = m.group(1)

        # Claim Number (from SUBSCRIBER line)
        m = re.search(r"CLAIM\s+NUMBER:\s*(\S+)", block)
        if m:
            rec["claim_number"] = m.group(1)

        # Patient Account
        m = re.search(r"PATIENT\s+ACCOU(?:NT)?:?\s*(\S+)", block)
        if m:
            rec["patient_account"] = m.group(1)

        # Member ID
        m = re.search(r"MEMBER\s+ID:\s*(\S+)", block)
        if m:
            rec["member_id"] = m.group(1)

        # Interest Amount
        m = re.search(r"INTEREST\s+AMOUNT:\s*\$?([\d,.]+)", block)
        if m:
            rec["interest_amount"] = m.group(1).replace(",", "")

        # PCP Number
        m = re.search(r"PCP\s+NUMBER:\s*(\S+)", block)
        if m:
            rec["pcp_number"] = m.group(1)

        # Remit Detail
        m = re.search(r"REMIT\s+DETAIL:\s*(.+?)(?:\s+PRODUCT\s+DESC:|\s*$)", block, re.MULTILINE)
        if m:
            rec["remit_detail"] = m.group(1).strip()

        # Product Desc
        m = re.search(r"PRODUCT\s+DESC:\s*(.+?)$", block, re.MULTILINE)
        if m:
            rec["product_desc"] = m.group(1).strip()

        # Servicing Provider NPI
        m = re.search(r"SERVICING\s+PROV\s+NPI:\s*(\d+)", block)
        if m:
            rec["servicing_prov_npi"] = m.group(1)

        # Servicing Provider Name
        m = re.search(r"SERVICING\s+PROV\s+NM:\s*(.+?)(?:\s+PCP\s+NAME:|\s*$)", block, re.MULTILINE)
        if m:
            rec["servicing_prov_nm"] = m.group(1).strip()

        # PCP Name
        m = re.search(r"PCP\s+NAME:\s*(.+?)(?:\s+BILLING\s+NPI:|\s*$)", block, re.MULTILINE)
        if m:
            rec["pcp_name"] = m.group(1).strip()

        # Billing NPI
        m = re.search(r"BILLING\s+NPI:\s*(\d+)", block)
        if m:
            rec["billing_npi"] = m.group(1)

        # --- Service line amounts from SUBTOTAL line ---
        # SUBTOTAL pattern: "CLAIM NUMBER: xxx| $225.00] $115.83 $109.17] $20.00] $0.00] $0.00] $69.17 $20.00"
        # OCR often garbles amounts (drops decimals, misreads digits).
        # Strategy: find the SUBTOTAL line, extract all dollar-like tokens,
        # then map to columns: BILLED, DISALLOW, ALLOWED, DEDUCT, COPAY, COB, PAID, PT_RESP

        subtotal_line = ""
        block_lines = block.split("\n")
        for bl in block_lines:
            # SUBTOTAL line has CLAIM NUMBER + multiple $ amounts
            if re.search(r"CLAIM\s+NUMBER:.*\$", bl):
                subtotal_line = bl
                break

        if subtotal_line:
            # Strip the CLAIM NUMBER prefix to avoid claim digits in amounts
            amounts_part = re.sub(r"^.*?CLAIM\s+NUMBER:\s*\S+\|?\s*", "", subtotal_line)
            # Extract all dollar-like tokens (with or without $ prefix, with .xx or not)
            raw_amounts = re.findall(r"[\$]?([\d,]+\.?\d*)\]?", amounts_part)
            # Filter out noise
            amounts = []
            for a in raw_amounts:
                a_clean = a.replace(",", "")
                # Skip long digit strings without decimals (leftover claim number fragments)
                if len(a_clean) >= 6 and "." not in a_clean:
                    continue
                # Skip single digits
                if len(a_clean) <= 1:
                    continue
                amounts.append(a_clean)

            # Typical UHC SUBTOTAL has 8 amount columns:
            # BILLED, DISALLOW, ALLOWED, DEDUCT, COPAY, COB, PAID_TO_PROVIDER, PATIENT_RESP
            if len(amounts) >= 8:
                billed, disallow, allowed, deduct = amounts[0], amounts[1], amounts[2], amounts[3]
                copay, cob, paid, pt_resp = amounts[4], amounts[5], amounts[6], amounts[7]
            elif len(amounts) >= 6:
                billed = amounts[0]
                allowed = amounts[1] if len(amounts) > 1 else "0.00"
                deduct = amounts[2] if len(amounts) > 2 else "0.00"
                copay = amounts[3] if len(amounts) > 3 else "0.00"
                paid = amounts[-2] if len(amounts) > 1 else "0.00"
                pt_resp = amounts[-1]
                disallow = "0.00"
                cob = "0.00"
            else:
                billed = amounts[0] if amounts else "0.00"
                paid = amounts[-1] if len(amounts) > 1 else "0.00"
                allowed = deduct = copay = cob = disallow = pt_resp = "0.00"

            # OCR fix: amounts without decimal points — add .00 or insert decimal
            def _fix_ocr_amount(val):
                """Fix OCR-garbled amounts: '1588' -> '15.88', '2000' -> '20.00'."""
                if not val or val == "0" or "." in val:
                    return _clean_amount(val)
                v = val.replace(",", "")
                if len(v) >= 3:
                    # Insert decimal before last 2 digits
                    return _clean_amount(v[:-2] + "." + v[-2:])
                return _clean_amount(val)

            billed = _fix_ocr_amount(billed)
            allowed = _fix_ocr_amount(allowed)
            deduct = _fix_ocr_amount(deduct)
            copay = _fix_ocr_amount(copay)
            cob = _fix_ocr_amount(cob)
            paid = _fix_ocr_amount(paid)
            pt_resp = _fix_ocr_amount(pt_resp)
            # Always compute disallow from billed - allowed; OCR garbles it too often
            try:
                disallow = "%.2f" % max(0, float(billed) - float(allowed))
            except (ValueError, TypeError):
                disallow = _fix_ocr_amount(disallow)

            # Find a service date from the block (skip DATE RECEIVED)
            dates = re.findall(r"(\d{2}/\d{2}/\d{2,4})", block)
            date_received = rec.get("date_received", "")
            svc_date = ""
            for d in dates:
                if d != date_received:
                    svc_date = d
                    break

            # Find procedure code (e.g., "9214-95" or "90214-95")
            code_match = re.search(r"(\d{4,5}-?\d{0,2})\s+POS", block)
            desc = code_match.group(1) if code_match else ""

            sl = {
                "date_of_service": svc_date or rec.get("date_received", ""),
                "description": desc,
                "units": "1",
                "billed_amt": billed,
                "disallow_amt": disallow,
                "allowed_amt": allowed,
                "deduct_amt": deduct,
                "copay_coins_amt": copay,
                "cob_pmt_amt": cob,
                "withhold_amt": "0.00",
                "paid_to_provider_amt": paid,
                "patient_resp_amt": pt_resp,
            }
            rec["service_lines"].append(sl)
            rec["total_payable_to_provider"] = paid

        _compute_derived_fields(rec)

        if rec["patient_name"] or rec["claim_number"]:
            records.append(rec)

    if not records:
        return base

    result = records[0]
    if len(records) > 1:
        result["_records"] = records
    return result


# ---------------------------------------------------------------------------
# Aetna "Explanation of Benefits" — normal text, multi-patient
# ---------------------------------------------------------------------------

def _is_aetna_eob(full_text):
    """Detect Aetna 'Explanation of Benefits' format (normal text)."""
    has_aetna = bool(re.search(r"\baetna\b", full_text, re.IGNORECASE))
    has_eob = bool(re.search(r"Explanation\s+Of\s+Benefits", full_text, re.IGNORECASE))
    has_patient_name = bool(re.search(r"Patient\s+Name:", full_text))
    has_claim_id = bool(re.search(r"Claim\s+ID:", full_text))
    return has_aetna and has_eob and has_patient_name and has_claim_id


def _extract_aetna_eob_claims(full_text, tables):
    """Extract multiple claims from Aetna 'Explanation of Benefits' PDFs.

    Format features:
    - 'Patient Name: LEONNA M ARNESON (self)'
    - 'Claim ID: ...  Recd: ...  Member ID: ...  Patient Account: ...'
    - Service line tables with SUBMITTED CHARGES, NEGOTIATED AMOUNT, etc.
    - 'ISSUED AMT: $53.83' or 'ISSUED AMT: NO PAY'
    - 'Claim Payment: $53.83', 'Total Patient Responsibility: $25.00'
    """
    # --- Extract shared header info ---
    base = _new_data_record()
    base["carrier_name"] = "Aetna"

    m = re.search(r"Trace\s*Number:\s*([\d]+)", full_text)
    if m:
        base["payment_number"] = m.group(1)

    m = re.search(r"Trace\s*Amount:\s*\$?([\d,.]+)", full_text)
    if m:
        base["total_payable_to_provider"] = m.group(1).replace(",", "")

    m = re.search(r"Printed:\s*(\d{2}/\d{2}/\d{4})", full_text)
    if m:
        base["payment_date"] = m.group(1)

    m = re.search(r"\bNPI:\s*([\d]+)", full_text)
    if m:
        base["billing_npi"] = m.group(1)

    m = re.search(r"TIN:\s*([\w]+)", full_text)
    if m:
        base["pcp_number"] = m.group(1)

    # --- Split into patient blocks at 'Patient Name:' ---
    patient_splits = re.split(r"(?=Patient\s+Name:)", full_text)
    patient_blocks = [b for b in patient_splits if re.match(r"Patient\s+Name:", b.strip())]

    if not patient_blocks:
        return base

    # --- Collect service line rows from tables ---
    svc_table_rows = []
    for t in tables:
        if not t or len(t) < 2:
            continue
        for row in t:
            if not row or not row[0]:
                continue
            cell0 = str(row[0]).strip()
            # Match date pattern in first cell (e.g. "02/10/26")
            if re.match(r"\d{2}/\d{2}/\d{2,4}", cell0):
                svc_table_rows.append(row)

    # --- Parse each patient block ---
    records = []
    table_idx = 0

    for block in patient_blocks:
        rec = _new_data_record()
        rec["carrier_name"] = base["carrier_name"]
        rec["billing_npi"] = base.get("billing_npi", "")
        rec["payment_number"] = base.get("payment_number", "")
        rec["payment_date"] = base.get("payment_date", "")
        rec["pcp_number"] = base.get("pcp_number", "")

        # Patient Name (strip relationship like "(self)", "(daughter)")
        m = re.search(r"Patient\s+Name:\s*(.+?)(?:\(.*?\))?\s*$", block, re.MULTILINE)
        if m:
            rec["patient_name"] = m.group(1).strip().rstrip("(").strip()

        # Claim ID
        m = re.search(r"Claim\s+ID:\s*(\S+)", block)
        if m:
            rec["claim_number"] = m.group(1)

        # Member ID
        m = re.search(r"Member\s+ID:\s*(\S+)", block)
        if m:
            rec["member_id"] = m.group(1)

        # Patient Account
        m = re.search(r"Patient\s+Account:\s*(\S+)", block)
        if m:
            rec["patient_account"] = m.group(1)

        # Date Received (Recd:)
        m = re.search(r"Recd?:\s*(\d{2}/\d{2}/\d{2,4})", block)
        if m:
            rec["date_received"] = m.group(1)

        # Subscriber/Member name
        m = re.search(r"(?:^|\n)\s*Member:\s*(.+?)(?:\s*$|\s+DIAG:)", block, re.MULTILINE)
        if m:
            rec["subscriber_name"] = m.group(1).strip()

        # Group Name
        m = re.search(r"Group\s+Name:\s*(.+?)$", block, re.MULTILINE)
        if m:
            rec["servicing_prov_nm"] = m.group(1).strip()

        # Product
        m = re.search(r"Product:\s*(.+?)$", block, re.MULTILINE)
        if m:
            rec["product_desc"] = m.group(1).strip()

        # DIAG codes
        m = re.search(r"DIAG:\s*(.+?)$", block, re.MULTILINE)
        if m:
            rec["remit_detail"] = "DIAG: " + m.group(1).strip()

        # Network Status
        m = re.search(r"Network\s+Status:\s*(\S+)", block)
        if m:
            if not rec["remit_detail"]:
                rec["remit_detail"] = m.group(1)
            else:
                rec["remit_detail"] += " | " + m.group(1)

        # ISSUED AMT (the payment amount per patient)
        m = re.search(r"ISSUED\s+AMT:\s*\$?([\d,.]+)", block)
        if m:
            rec["total_payable_to_provider"] = m.group(1).replace(",", "")
        else:
            # "NO PAY" case
            if re.search(r"ISSUED\s+AMT:\s*NO\s*PAY", block, re.IGNORECASE):
                rec["total_payable_to_provider"] = "0.00"

        # Claim Payment (another source for paid amount)
        m = re.search(r"Claim\s+Payment:\s*\$?([\d,.]+)", block)
        if m:
            paid_val = m.group(1).replace(",", "")
            if not rec["total_payable_to_provider"]:
                rec["total_payable_to_provider"] = paid_val

        # Total Patient Responsibility
        m = re.search(r"Total\s+Patient\s+Responsibility:\s*\$?([\d,.]+)", block)
        if m:
            rec["interest_amount"] = ""  # clear, not interest

        # --- Service lines from tables ---
        # Match table rows by looking for dates that fall within this block's context
        # Use sequential matching: each patient block consumes the next available table row(s)
        svc_date_from_block = rec.get("date_received", "")

        # Try to find service line data in the text block itself
        # Pattern: DATE PL CODE UNITS SUBMITTED NEGOTIATED COPAY ... PT_RESP PAYABLE
        svc_line_matches = re.findall(
            r"(\d{2}/\d{2}/\d{2,4})\s+(\d+)\s+(\d{5,7})\s+([\d.]+)\s+"
            r"([\d,.]+)\s+([\d,.]+)\s+([\d,.]+)",
            block
        )

        if svc_line_matches:
            for match in svc_line_matches:
                svc_date, pl, code, units, billed, negotiated, copay = match

                sl = {
                    "date_of_service": svc_date,
                    "description": code,
                    "units": str(int(float(units))) if units else "1",
                    "billed_amt": _clean_amount(billed),
                    "allowed_amt": _clean_amount(negotiated),
                    "copay_coins_amt": _clean_amount(copay),
                    "deduct_amt": "0.00",
                    "disallow_amt": "0.00",
                    "cob_pmt_amt": "0.00",
                    "withhold_amt": "0.00",
                    "paid_to_provider_amt": rec.get("total_payable_to_provider", "0.00"),
                    "patient_resp_amt": "0.00",
                }
                rec["service_lines"].append(sl)
        elif table_idx < len(svc_table_rows):
            # Fall back to table data
            row = svc_table_rows[table_idx]
            table_idx += 1
            vals = [str(c or "0").strip() for c in row]

            svc_date = vals[0] if vals else ""
            code = vals[2] if len(vals) > 2 else ""
            units = vals[3] if len(vals) > 3 else "1"
            billed = vals[4] if len(vals) > 4 else "0.00"
            negotiated = vals[5] if len(vals) > 5 else "0.00"
            copay = vals[6] if len(vals) > 6 else "0.00"
            # Patient resp and payable are near the end
            payable = "0.00"
            pt_resp = "0.00"
            if len(vals) >= 2:
                payable = vals[-1] if vals[-1] else "0.00"
            if len(vals) >= 3:
                pt_resp = vals[-2] if vals[-2] else "0.00"

            sl = {
                "date_of_service": svc_date,
                "description": code,
                "units": str(int(float(units))) if units and units != "0" else "1",
                "billed_amt": _clean_amount(billed),
                "allowed_amt": _clean_amount(negotiated),
                "copay_coins_amt": _clean_amount(copay),
                "deduct_amt": "0.00",
                "disallow_amt": "0.00",
                "cob_pmt_amt": "0.00",
                "withhold_amt": "0.00",
                "paid_to_provider_amt": _clean_amount(payable),
                "patient_resp_amt": _clean_amount(pt_resp),
            }
            rec["service_lines"].append(sl)

        # Extract patient_resp from Total Patient Responsibility if
        # we have a service line but no pt_resp yet
        pt_resp_match = re.search(r"Total\s+Patient\s+Responsibility:\s*\$?([\d,.]+)", block)
        if pt_resp_match and rec["service_lines"]:
            pt_resp_val = _clean_amount(pt_resp_match.group(1))
            for sl in rec["service_lines"]:
                if sl["patient_resp_amt"] == "0.00":
                    sl["patient_resp_amt"] = pt_resp_val

        # Compute derived fields
        _compute_derived_fields(rec)

        if rec["patient_name"] or rec["claim_number"]:
            records.append(rec)

    if not records:
        return base

    result = records[0]
    if len(records) > 1:
        result["_records"] = records
    return result


def _extract_aetna_claims(full_text, tables):
    """Handle Aetna 'Summary of Claim Payment' PDFs with reversed text labels.

    These PDFs have reversed labels like 'tneitaP :emaN' (Patient Name)
    but normal values. Each patient block starts with 'tneitaP :emaN'.
    Service line data is in extracted tables.
    """
    # --- Extract header info (Trace Number, carrier, etc.) ---
    base = _new_data_record()
    base["carrier_name"] = "Aetna"

    # Prefer non-reversed TraceNumber (page 2) over reversed version
    m = re.search(r"TraceNumber:\s*([\d]+)", full_text)
    if m:
        base["payment_number"] = m.group(1)
    else:
        m = re.search(r"Trace\s*:rebmuN\s*([\d]+)", full_text)
        if m:
            base["payment_number"] = m.group(1)[::-1]

    m = re.search(r"Trace\s*(?::tnuomA|:Amount|Amount:?)\s*\$?([\d,.]+)", full_text)
    if not m:
        m = re.search(r"TraceAmount:\s*\$?([\d,.]+)", full_text)

    # NPI (only match non-reversed NPI: label, not reversed :NIP which is PIN:)
    m = re.search(r"(?<!\S)NPI:\s*([\d]+)", full_text)
    if m:
        base["billing_npi"] = m.group(1)

    m = re.search(r"(?:detnirP|Printed):?\s*(\d{2}/\d{2}/\d{4})", full_text)
    if m:
        base["payment_date"] = m.group(1)

    # --- Split into patient blocks ---
    patient_splits = re.split(r"tneitaP :emaN\s+", full_text)
    if len(patient_splits) < 2:
        # No Aetna patient blocks found, return base with whatever we have
        return base

    # --- Collect tables with service line data (rows starting with date) ---
    svc_tables = []
    for t in tables:
        for row in t:
            if row[0] and re.match(r"\d{2}/\d{2}/\d{2}", str(row[0])):
                svc_tables.append(row)
                break

    # --- Parse each patient block ---
    records = []
    for idx, block in enumerate(patient_splits[1:]):
        rec = _new_data_record()
        rec["carrier_name"] = base["carrier_name"]
        rec["billing_npi"] = base.get("billing_npi", "")
        rec["payment_number"] = base.get("payment_number", "")
        rec["payment_date"] = base.get("payment_date", "")

        # Patient name (first line of block)
        first_line = block.split("\n")[0].strip()
        # Remove trailing parenthetical like "(esuops)"
        name = re.sub(r"\s*\(.*\)\s*$", "", first_line).strip()
        rec["patient_name"] = name

        # Claim ID
        m = re.search(r"mialC ID:\s*(\S+)", block)
        if m:
            rec["claim_number"] = m.group(1)

        # Member ID
        m = re.search(r"rebmeM ID:\s*(\S+)", block)
        if m:
            rec["member_id"] = m.group(1)

        # Patient Account
        m = re.search(r"(?:tnuoccA|Account)\s+(\w+)", block)
        if m:
            rec["patient_account"] = m.group(1)

        # Subscriber name (reversed "rebmeM" label — each word is individually reversed)
        m = re.search(r":rebmeM\s+([A-Z][A-Z ]+?)(?:\s+DIAG:|\s*$)", block)
        if m:
            raw = m.group(1).strip()
            # Each word is char-reversed individually, then words stay in order
            rec["subscriber_name"] = " ".join(w[::-1] for w in raw.split())

        # Date received (Recd date, reversed label — date digits are reversed)
        m = re.search(r":dceR\s+(\d{2}/\d{2}/\d{2})", block)
        if m:
            # Reverse the date string: "62/31/20" → "02/13/26"
            rec["date_received"] = m.group(1)[::-1]

        # Product / Plan (each word is char-reversed individually)
        m = re.search(r":tcudorP\s+(.+?)(?:\s+krowteN|\n)", block)
        if m:
            prod = m.group(1).strip()
            rec["product_desc"] = " ".join(w[::-1] for w in prod.split())

        # ISSUED AMT (this is the paid amount)
        m = re.search(r"ISSUED AMT:\s*\$?([\d,.]+)", block)
        if m:
            rec["total_payable_to_provider"] = m.group(1).replace(",", "")

        # Claim Payment (fallback for total payable)
        if not rec["total_payable_to_provider"]:
            m = re.search(r"Claim Payment:\s*\$?([\d,.]+)", block)
            if m:
                rec["total_payable_to_provider"] = m.group(1).replace(",", "")

        # Build service line from matching table
        if idx < len(svc_tables):
            row = svc_tables[idx]
            # Table columns: DATE, PL, CODE, UNITS, BILLED, NEGOTIATED, COPAY, ..., PT_RESP, PAYABLE
            vals = [str(c or "0") for c in row]
            svc_date = vals[0] if vals else ""
            proc_code = vals[2] if len(vals) > 2 else ""
            units = vals[3] if len(vals) > 3 else "1"
            billed = vals[4] if len(vals) > 4 else "0.00"
            allowed = vals[5] if len(vals) > 5 else "0.00"  # Negotiated = allowed
            copay = vals[6] if len(vals) > 6 else "0.00"
            # Payable amount is last non-empty value
            payable = vals[-1] if vals else "0.00"
            # Patient resp is second to last
            pt_resp = vals[-2] if len(vals) >= 2 else "0.00"

            try:
                disallow = "%.2f" % (float(billed) - float(allowed))
            except (ValueError, TypeError):
                disallow = "0.00"

            sl = {
                "date_of_service": svc_date,
                "description": proc_code,
                "units": str(int(float(units))) if units else "1",
                "billed_amt": _clean_amount(billed),
                "disallow_amt": _clean_amount(disallow),
                "allowed_amt": _clean_amount(allowed),
                "deduct_amt": "0.00",
                "copay_coins_amt": _clean_amount(copay),
                "cob_pmt_amt": "0.00",
                "withhold_amt": "0.00",
                "paid_to_provider_amt": _clean_amount(payable),
                "patient_resp_amt": _clean_amount(pt_resp),
            }
            rec["service_lines"].append(sl)
        else:
            # No matching table, try regex from text
            m = re.search(
                r"(\d{2}/\d{2}/\d{2})\s+\d+\s+(\d{5,7})\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)",
                block
            )
            if m:
                billed = m.group(4)
                allowed = m.group(5)
                try:
                    disallow = "%.2f" % (float(billed) - float(allowed))
                except (ValueError, TypeError):
                    disallow = "0.00"
                sl = {
                    "date_of_service": m.group(1),
                    "description": m.group(2),
                    "units": "1",
                    "billed_amt": _clean_amount(billed),
                    "disallow_amt": _clean_amount(disallow),
                    "allowed_amt": _clean_amount(allowed),
                    "deduct_amt": "0.00",
                    "copay_coins_amt": "0.00",
                    "cob_pmt_amt": "0.00",
                    "withhold_amt": "0.00",
                    "paid_to_provider_amt": rec.get("total_payable_to_provider", "0.00"),
                    "patient_resp_amt": "0.00",
                }
                rec["service_lines"].append(sl)

        _compute_derived_fields(rec)

        # Only add records with actual data
        if rec["patient_name"] or rec["claim_number"]:
            records.append(rec)

    if not records:
        return base

    # Return first record as primary, all as _records
    result = records[0]
    if len(records) > 1:
        result["_records"] = records
    return result


def _is_medicare_format(full_text):
    """Detect if text is a Medicare Remittance Advice format."""
    # Standard header
    if re.search(r"MEDICARE\s+REMITTANCE\s+ADVICE", full_text, re.IGNORECASE):
        return True
    # Fixed-width column header
    if re.search(r"PERF\s+PROV.*SERV\s+DATE.*BILLED.*ALLOWED.*PROV[_ ]PD", full_text):
        return True
    # Spaced-out text from some PDFs: "M E D I C A R E" or "P_E_R_F_ _P_R_O_V_"
    collapsed = full_text.replace(" ", "").replace("_", "")
    if re.search(r"MEDICAREREMITTANCEADVICE", collapsed, re.IGNORECASE):
        return True
    if re.search(r"PERFPROV.*SERVDATE.*BILLED.*ALLOWED.*PROVPD", collapsed):
        return True
    # OCR fallback: MEDICARE keyword + NAME...MID...ICN block
    if (re.search(r"\bMEDICARE\b", full_text, re.IGNORECASE)
            and re.search(r"\bNAME\b.+\bMID\b.+\bICN\b", full_text)):
        return True
    # NAME...MID...ICN blocks present (even without MEDICARE keyword)
    if re.search(r"\bNAME\b.+\bMID\b.+\bICN\b", full_text):
        name_blocks = re.findall(r"^\s*NAME\s+[A-Z]", full_text, re.MULTILINE | re.IGNORECASE)
        if len(name_blocks) >= 2:
            return True
    return False


def _extract_medicare_claims(full_text, lines, data):
    """Handle Medicare Remittance Advice (fixed-width text) format.

    Detects Medicare format, then extracts patient info from the first
    claim block and builds service lines from CLAIM TOTALS rows.
    """
    # --- Detect Medicare format ---
    is_medicare = _is_medicare_format(full_text)
    if not is_medicare:
        return

    # --- Extract header info (NPI, DATE, CHECK/EFT, carrier) ---
    for line in lines:
        if not data["billing_npi"]:
            m = re.search(r"\bNPI:\s*([\d]+)", line)
            if m:
                data["billing_npi"] = m.group(1)
        if not data["date_received"]:
            m = re.search(r"\bDATE:\s*([\d/]+)", line)
            if m:
                data["date_received"] = m.group(1)
        if not data["payment_number"]:
            m = re.search(r"CHECK/EFT\s*#?:?\s*([\d]+)", line)
            if m:
                data["payment_number"] = m.group(1)
        if not data["payment_date"]:
            m = re.search(r"\bDATE:\s*([\d/]+)", line)
            if m:
                data["payment_date"] = m.group(1)

    # --- Find claim blocks (each starts with "NAME ...") ---
    claim_blocks = []
    current_block = []
    for line in lines:
        if re.match(r"\s*NAME\s+[A-Z]", line, re.IGNORECASE):
            if current_block:
                claim_blocks.append(current_block)
            current_block = [line]
        elif current_block:
            current_block.append(line)
    if current_block:
        claim_blocks.append(current_block)

    if not claim_blocks:
        return

    # --- Process first block for patient header fields ---
    first_block_text = " ".join(claim_blocks[0])

    if not data["patient_name"]:
        m = re.search(r"\bNAME\s+([A-Z][A-Za-z,. \-]+?)\s{2,}MID\b", first_block_text)
        if not m:
            m = re.search(r"\bNAME\s+([A-Z][A-Za-z,. \-]+?)\s+MID\b", first_block_text)
        if m:
            data["patient_name"] = m.group(1).strip()

    if not data["member_id"]:
        m = re.search(r"\bMID\s+([\w]+)", first_block_text)
        if m:
            data["member_id"] = m.group(1)

    if not data["patient_account"]:
        m = re.search(r"\bACNT\s+([\w]+)", first_block_text)
        if m:
            data["patient_account"] = m.group(1)

    if not data["claim_number"]:
        m = re.search(r"\bICN\s+(\d{10,})", first_block_text)
        if m:
            data["claim_number"] = m.group(1)

    # --- Extract service lines from ALL claim blocks ---
    for block in claim_blocks:
        _parse_medicare_block(block, data)

    # --- Set total payable from service lines ---
    if data["service_lines"] and not data["total_payable_to_provider"]:
        total = sum(float(sl["paid_to_provider_amt"]) for sl in data["service_lines"])
        if total > 0:
            data["total_payable_to_provider"] = f"{total:.2f}"


def _extract_medicare_records(full_text, lines, base_data):
    """Return one parsed record per Medicare claim block, if applicable."""
    is_medicare = _is_medicare_format(full_text)
    if not is_medicare:
        return [base_data]

    # Split into claim blocks starting with NAME ... MID ... ACNT ... ICN
    claim_blocks = []
    current_block = []
    for line in lines:
        if re.match(r"\s*NAME\s+[A-Z]", line, re.IGNORECASE):
            if current_block:
                claim_blocks.append(current_block)
            current_block = [line]
        elif current_block:
            current_block.append(line)
    if current_block:
        claim_blocks.append(current_block)

    if len(claim_blocks) <= 1:
        return [base_data]

    records = []
    for block in claim_blocks:
        block_text = " ".join(block)
        rec = _new_data_record()

        # Copy shared payment/provider context from parsed base data.
        for key in (
            "date_received", "payment_date", "payment_number",
            "billing_npi", "servicing_prov_npi", "servicing_prov_nm",
            "carrier_name", "carrier_id", "product_desc", "remit_detail",
            "interest_amount", "pcp_number", "pcp_name",
        ):
            rec[key] = base_data.get(key, "")

        m_name = re.search(r"\bNAME\s+([A-Z][A-Za-z,. \-]+?)\s+MID\b", block_text)
        if not m_name:
            m_name = re.search(r"\bNAME\s+([A-Z][A-Za-z,. \-]+?)\s{2,}MID\b", block_text)
        if m_name:
            rec["patient_name"] = m_name.group(1).strip()
            rec["subscriber_name"] = rec["patient_name"]

        m_mid = re.search(r"\bMID\s+([\w]+)", block_text)
        if m_mid:
            rec["member_id"] = m_mid.group(1)

        m_acnt = re.search(r"\bACNT\s+([\w]+)", block_text)
        if m_acnt:
            rec["patient_account"] = m_acnt.group(1)

        m_icn = re.search(r"\bICN\s+(\d{10,})", block_text)
        if m_icn:
            rec["claim_number"] = m_icn.group(1)

        # Reuse existing Medicare service-line parser for this block.
        _parse_medicare_block(block, rec)
        _compute_derived_fields(rec)

        # Keep only blocks that produced a patient or claim signal.
        if rec["patient_name"] or rec["claim_number"] or rec["service_lines"]:
            records.append(rec)

    return records if records else [base_data]


def _parse_medicare_block(block_lines, data):
    """Parse a single Medicare claim block into a service line."""
    block_text = " ".join(block_lines)

    # --- Find PERF PROV NPI and service data line ---
    # Format: 10-digit-NPI MMDD MMDDYY POS NOS PROC [MODS] BILLED ALLOWED DEDUCT COINS ...
    data_line_match = None
    for line in block_lines:
        m = re.match(
            r"\s*(\d{10})\s+(\d{4})\s+(\d{6})\s+(\d{1,2})\s+"
            r"([\d.]+)\s+(\d{4,5})\s*(\d{0,2})",
            line
        )
        if m:
            data_line_match = m
            # Also extract servicing provider NPI
            if not data["servicing_prov_npi"]:
                data["servicing_prov_npi"] = m.group(1)
            break

    # --- Parse CLAIM TOTALS line ---
    # CLAIM TOTALS  BILLED  ALLOWED  DEDUCT  COINS  GRP/RC-AMT  PROV_PD
    claim_totals = None
    claim_totals_amounts = []
    for line in block_lines:
        m = re.search(r"CLAIM\s+TOTALS[,]?\s+([\d.]+(?:\s+[\d.]+)*)", line)
        if m:
            claim_totals_amounts = re.findall(r"[\d.]+", m.group(1))
            claim_totals = m
            break

    # --- Parse PT RESP ---
    pt_resp = "0.00"
    for line in block_lines:
        m = re.search(r"PT\s+RESP\s+([\d.]+)", line)
        if m:
            pt_resp = m.group(1)
            break

    # --- Build service line ---
    if data_line_match or claim_totals:
        # Date from data line
        serv_date = ""
        proc_code = ""
        units = "1"
        if data_line_match:
            date_raw = data_line_match.group(3)  # MMDDYY
            serv_date = f"{date_raw[:2]}/{date_raw[2:4]}/{date_raw[4:]}"
            proc_code = data_line_match.group(6)
            mods = data_line_match.group(7)
            if mods:
                proc_code += " " + mods
            nos = data_line_match.group(5)
            try:
                units = str(int(float(nos)))
            except ValueError:
                units = nos

        # Amounts from CLAIM TOTALS (more reliable than data line)
        # Format: BILLED ALLOWED DEDUCT COINS [GRP/RC-AMT] [PROV_PD]
        if claim_totals and len(claim_totals_amounts) >= 4:
            billed = claim_totals_amounts[0]
            allowed = claim_totals_amounts[1]
            deduct = claim_totals_amounts[2]
            coins = claim_totals_amounts[3]
            # 6 numbers: ..., GRP/RC-AMT, PROV_PD
            if len(claim_totals_amounts) >= 6:
                prov_pd = claim_totals_amounts[5]
            # 5 numbers: ..., GRP/RC-AMT (PROV_PD is 0/missing)
            elif len(claim_totals_amounts) == 5:
                prov_pd = "0.00"
            else:
                prov_pd = "0.00"
        else:
            # Fallback: try to extract amounts from the data line text
            billed = allowed = deduct = coins = prov_pd = "0.00"
            if data_line_match:
                # Grab remaining numbers after the proc/mods
                rest = data_line_match.string[data_line_match.end():]
                amounts = re.findall(r"[\d.]+", rest)
                if len(amounts) >= 4:
                    billed, allowed, deduct, coins = amounts[0], amounts[1], amounts[2], amounts[3]
                if amounts:
                    prov_pd = amounts[-1]

        # Compute disallow (billed - allowed)
        try:
            disallow = f"{float(billed) - float(allowed):.2f}"
        except (ValueError, TypeError):
            disallow = "0.00"

        service_line = {
            "date_of_service": serv_date,
            "description": proc_code,
            "units": units,
            "billed_amt": _clean_amount(billed),
            "disallow_amt": _clean_amount(disallow),
            "allowed_amt": _clean_amount(allowed),
            "deduct_amt": _clean_amount(deduct),
            "copay_coins_amt": _clean_amount(coins),
            "cob_pmt_amt": "0.00",
            "withhold_amt": "0.00",
            "paid_to_provider_amt": _clean_amount(prov_pd),
            "patient_resp_amt": _clean_amount(pt_resp),
        }

        data["service_lines"].append(service_line)


def _extract_service_lines_from_text(lines, data):
    """Regex-based extraction for UnitedHealthcare-style text PDFs."""
    service_pattern = re.compile(
        r"(\d{2}/\d{2}/\d{2,4})\s*[-–]?\s*(?:\d{2}/\d{2}/\d{2,4})?\s+"
        r"(.+?)\s+"
        r"(\d+)\s+"
        r"\$([\d,.]+)\s+"
        r"\$?([\d,.]+)\s+"
        r"\$?([\d,.]+)\s+"
        r"(?:\$?([\d,.]+)\s+)?"
        r"\$?([\d,.]+)\s+"
        r"\$?([\d,.]+)\s+"
        r"\$?([\d,.]+)\s+"
        r"\$?([\d,.]+)\s+"
        r"\$?([\d,.]+)"
    )

    for line in lines:
        m = service_pattern.search(line)
        if m:
            service_line = {
                "date_of_service": m.group(1),
                "description": m.group(2).strip(),
                "units": m.group(3),
                "billed_amt": _clean_amount(m.group(4)),
                "disallow_amt": _clean_amount(m.group(5)),
                "allowed_amt": _clean_amount(m.group(6)),
                "deduct_amt": _clean_amount(m.group(7)),
                "copay_coins_amt": _clean_amount(m.group(8)),
                "cob_pmt_amt": _clean_amount(m.group(9)),
                "withhold_amt": _clean_amount(m.group(10)),
                "paid_to_provider_amt": _clean_amount(m.group(11)),
                "patient_resp_amt": _clean_amount(m.group(12)),
            }
            data["service_lines"].append(service_line)


def _extract_ocr_service_lines(lines, data):
    """Generic OCR fallback: find lines starting with a date followed by amounts.

    OCR text is messy — this looks for any line containing a date pattern
    (MM/DD/YYYY or MM/DD/YY) followed by dollar amounts, and extracts
    whatever amounts it can find.
    """
    date_pattern = re.compile(r"(\d{2}/\d{2}/\d{2,4})")
    amount_pattern = re.compile(r"(\d+\.\d{2})")

    seen_dates = set()
    for line in lines:
        dm = date_pattern.search(line)
        if not dm:
            continue

        date_val = dm.group(1)
        # Skip header/label lines
        line_lower = line.lower()
        if any(kw in line_lower for kw in ("date of", "service date", "from date", "showing")):
            continue

        # Extract all dollar amounts from this line
        amounts = amount_pattern.findall(line)
        if not amounts:
            continue

        # Avoid duplicate entries for the same date (OCR often repeats)
        date_key = f"{date_val}_{len(amounts)}_{amounts[0] if amounts else ''}"
        if date_key in seen_dates:
            continue
        seen_dates.add(date_key)

        # Map amounts to our fields based on count
        # Different PDFs have different column orders; use best-guess mapping
        service_line = {
            "date_of_service": date_val,
            "description": "",
            "units": "1",
            "billed_amt": "0.00",
            "disallow_amt": "0.00",
            "allowed_amt": "0.00",
            "deduct_amt": "0.00",
            "copay_coins_amt": "0.00",
            "cob_pmt_amt": "0.00",
            "withhold_amt": "0.00",
            "paid_to_provider_amt": "0.00",
            "patient_resp_amt": "0.00",
        }

        if len(amounts) >= 1:
            service_line["billed_amt"] = amounts[0]
        if len(amounts) >= 2:
            # Second amount: if smaller than billed, likely paid/allowed
            service_line["paid_to_provider_amt"] = amounts[1]
        if len(amounts) >= 3:
            service_line["deduct_amt"] = amounts[2]
        if len(amounts) >= 4:
            service_line["copay_coins_amt"] = amounts[3]

        # Only add if we got at least one non-zero amount
        if any(service_line[k] != "0.00" for k in ("billed_amt", "paid_to_provider_amt")):
            data["service_lines"].append(service_line)


def _extract_amounts_from_text(text, data):
    """Fallback: extract individual amounts from raw text.

    Works on both single-line and multi-line OCR output where the label
    and amount may be separated by newlines/whitespace.
    """
    # Collapse newlines to spaces so label + amount on adjacent lines still match.
    collapsed = re.sub(r"\s*\n\s*", " ", text)

    # Order matters: extract specific labeled amounts first, then fallbacks.
    # Use ordered list so billed is matched before paid (prevents column-shift
    # mismatches in OCR where "Charge Amt ($) ... Amt Paid ($) 225.00" appears
    # with the number belonging to the charge, not the paid column).
    ordered_patterns = [
        ("billed_amt",            r"(?:BILLED|CHARGE)\s*(?:AMT|AMOUNT)?\s*(?:\(?\$?\)?)?\s*(?:[\S\s]{0,30}?)([\d]+\.\d{2})"),
        ("allowed_amt",           r"ALLOWED\s*(?:AMT|AMOUNT)?[\s\S]{0,80}?([\d]+\.\d{2})"),
        ("deduct_amt",            r"DEDUCT(?:IBLE)?\s*(?:AMT|AMOUNT)?[\s\S]{0,80}?([\d]+\.\d{2})"),
        ("copay_coins_amt",       r"CO(?:PAY|IN)/?(?:COINS?|PAY)?\s*(?:AMT|AMOUNT)?[\s\S]{0,30}?([\d]+\.\d{2})"),
        ("disallow_amt",          r"DISALLOW(?:ED)?\s*(?:AMT|AMOUNT)?\s*\$?([\d,.]+)"),
        ("cob_pmt_amt",           r"COB\s*(?:PMT|PAYMENT)?\s*(?:AMT|AMOUNT)?\s*\$?([\d,.]+)"),
        ("withhold_amt",          r"WITHHOLD\s*(?:AMT|AMOUNT)?\s*\$?([\d,.]+)"),
        ("paid_to_provider_amt",  r"(?:PAID\s*TO\s*PROVIDER|NET\s*PAID)\s*(?:AMT|AMOUNT)?\s*(?:\(?\$?\)?)?\s*\$?([\d]+\.\d{2})"),
        ("paid_to_provider_amt",  r"AMT\s*PAID\s*(?:\(?\$?\)?)?\s*\$?([\d]+\.\d{2})"),
        ("patient_resp_amt",      r"PATIENT\s*RESP(?:ONSIBILITY)?\s*(?:AMT|AMOUNT)?\s*\$?([\d,.]+)"),
    ]

    service_line = {}
    matched_positions = {}  # Track where each match was found
    for key, pattern in ordered_patterns:
        if key in service_line:
            continue  # already found
        m = re.search(pattern, collapsed, re.IGNORECASE)
        if m:
            val = _clean_amount(m.group(1))
            pos = m.start(1)
            # If this amount was already claimed by a prior field at the same
            # position, skip it (avoids OCR column-merge double-counting).
            if any(abs(pos - p) < 5 for p in matched_positions.values()):
                continue
            service_line[key] = val
            matched_positions[key] = pos

    # If allowed > 0 but no explicit paid-to-provider, use allowed as paid
    if service_line.get("allowed_amt") and service_line.get("allowed_amt") != "0.00":
        if not service_line.get("paid_to_provider_amt") or service_line["paid_to_provider_amt"] == "0.00":
            service_line["paid_to_provider_amt"] = service_line["allowed_amt"]

    # Also try to grab service date from the text
    date_match = re.search(
        r"(?:SERVICE\s+DATE|SERV(?:ICE)?\s+DATE)S?\s*:?\s*(\d{2}/\d{2}/\d{2,4})",
        collapsed, re.IGNORECASE
    )

    if service_line:
        all_keys = {k for k, _ in ordered_patterns}
        for key in all_keys:
            service_line.setdefault(key, "0.00")
        service_line.setdefault("date_of_service",
                                date_match.group(1) if date_match else "")
        service_line.setdefault("description", "")
        service_line.setdefault("units", "1")
        data["service_lines"].append(service_line)

        # Also fill total_payable if we found paid amount
        if not data["total_payable_to_provider"] and service_line.get("paid_to_provider_amt", "0.00") != "0.00":
            data["total_payable_to_provider"] = service_line["paid_to_provider_amt"]
