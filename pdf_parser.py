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


# ---------------------------------------------------------------------------
# OCR fallback for scanned / image-based PDFs
# ---------------------------------------------------------------------------

def _ocr_pdf(pdf_path):
    """Convert PDF to images and run OCR on each page."""
    try:
        images = convert_from_path(pdf_path, dpi=300)
        pages_text = []
        for img in images:
            text = pytesseract.image_to_string(img)
            pages_text.append(text)
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

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            full_text += page_text + "\n"
            page_tables = page.extract_tables()
            if page_tables:
                tables.extend(page_tables)

    # --- OCR fallback for image-based / scanned PDFs ---
    text_stripped = full_text.strip()
    used_ocr = False
    if len(text_stripped) < 50:
        if OCR_AVAILABLE:
            full_text = _ocr_pdf(pdf_path)
            used_ocr = bool(full_text.strip())
            # OCR doesn't produce structured tables, so tables stays empty
        else:
            # Can't extract — return empty data with a notice
            return {
                "patient_name": "", "subscriber_id": "", "member_id": "",
                "servicing_prov_npi": "", "subscriber_name": "",
                "interest_amount": "", "servicing_prov_nm": "",
                "date_received": "", "pcp_number": "", "claim_number": "",
                "remit_detail": "", "pcp_name": "", "patient_account": "",
                "product_desc": "", "billing_npi": "", "carrier_id": "",
                "carrier_name": "", "payment_number": "", "payment_date": "",
                "total_payable_to_provider": "", "service_lines": [],
                "_notice": "This PDF is an image/scanned document. OCR engine is not installed. Please install tesseract to enable OCR.",
            }

    data = {
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

    # ---- 1. Extract header fields using multi-pattern matching ----
    lines = full_text.split("\n")
    for line in lines:
        for field, patterns in FIELD_PATTERNS.items():
            if data[field]:  # already found
                continue
            for pattern in patterns:
                m = re.search(pattern, line, re.IGNORECASE)
                if m:
                    data[field] = m.group(1).strip()
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

    # ---- 7. Extract carrier name from first line if still empty ----
    if not data["carrier_name"]:
        for line in lines[:5]:
            stripped = line.strip()
            if stripped and re.search(
                r"(?:insurance|healthcare|health\s*plan|blue\s*cross|blue\s*shield|"
                r"aetna|cigna|humana|united|anthem|kaiser|molina|centene|"
                r"wellcare|ambetter|medicaid|medicare|tricare)",
                stripped, re.IGNORECASE
            ):
                data["carrier_name"] = stripped
                break

    # ---- 8. Compute missing fields from what we have ----
    _compute_derived_fields(data)
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


def _extract_medicare_claims(full_text, lines, data):
    """Handle Medicare Remittance Advice (fixed-width text) format.

    Detects Medicare format, then extracts patient info from the first
    claim block and builds service lines from CLAIM TOTALS rows.
    """
    # --- Detect Medicare format ---
    is_medicare = bool(
        re.search(r"MEDICARE\s+REMITTANCE\s+ADVICE", full_text, re.IGNORECASE)
        or re.search(r"PERF\s+PROV.*SERV\s+DATE.*BILLED.*ALLOWED.*PROV\s+PD", full_text)
    )
    if not is_medicare:
        return

    # --- Extract header info (NPI, DATE, CHECK/EFT) ---
    for line in lines:
        if not data["billing_npi"]:
            m = re.search(r"\bNPI:\s*([\d]+)", line)
            if m:
                data["billing_npi"] = m.group(1)
        if not data["date_received"]:
            m = re.search(r"\bDATE:\s*([\d/]+)", line)
            if m:
                data["date_received"] = m.group(1)

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
    for line in block_lines:
        m = re.search(
            r"CLAIM\s+TOTALS\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)",
            line
        )
        if m:
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
        if claim_totals:
            billed = claim_totals.group(1)
            allowed = claim_totals.group(2)
            deduct = claim_totals.group(3)
            coins = claim_totals.group(4)
            prov_pd = claim_totals.group(6)
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
    """Fallback: extract individual amounts from raw text."""
    patterns = {
        "billed_amt": r"(?:BILLED|CHARGE)\s*(?:AMT|AMOUNT)?\s*(?:\(\$?\))?\s*\$?([\d,.]+)",
        "disallow_amt": r"DISALLOW(?:ED)?\s*(?:AMT|AMOUNT)?\s*\$?([\d,.]+)",
        "allowed_amt": r"ALLOWED\s*(?:AMT|AMOUNT)?\s*\$?([\d,.]+)",
        "deduct_amt": r"DEDUCT(?:IBLE)?\s*(?:AMT|AMOUNT)?\s*\$?([\d,.]+)",
        "copay_coins_amt": r"CO(?:PAY|IN)/?(?:COINS?|PAY)?\s*(?:AMT|AMOUNT)?\s*\$?([\d,.]+)",
        "cob_pmt_amt": r"COB\s*(?:PMT|PAYMENT)?\s*(?:AMT|AMOUNT)?\s*\$?([\d,.]+)",
        "withhold_amt": r"WITHHOLD\s*(?:AMT|AMOUNT)?\s*\$?([\d,.]+)",
        "paid_to_provider_amt": r"(?:PAID\s*TO\s*PROVIDER|AMT\s*PAID|AMOUNT\s*PAID|NET\s*PAID)\s*(?:AMT|AMOUNT)?\s*\$?([\d,.]+)",
        "patient_resp_amt": r"PATIENT\s*RESP(?:ONSIBILITY)?\s*(?:AMT|AMOUNT)?\s*\$?([\d,.]+)",
    }

    service_line = {}
    for key, pattern in patterns.items():
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            service_line[key] = _clean_amount(m.group(1))

    if service_line:
        for key in patterns:
            service_line.setdefault(key, "0.00")
        service_line.setdefault("date_of_service", "")
        service_line.setdefault("description", "")
        service_line.setdefault("units", "1")
        data["service_lines"].append(service_line)
