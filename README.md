# Flask PDF Parser (EOB / Remittance Auto-Fill)

A Flask application that parses medical EOB/remittance PDFs, extracts claim/payment/service-line data, auto-fills a billing form UI, and stores parsed records in SQLite with searchable history.

## What This Project Does

- Upload a PDF from the web UI.
- Extract header fields (patient, claim, payer/payment details).
- Extract service lines (DOS, CPT/description, billed/allowed/paid/patient responsibility, etc.).
- Handle multiple remittance formats (text tables, fixed-width Medicare-style, OCR fallback).
- Persist results in database tables for later review.
- Provide a history page with search, record view/load, and delete.

## Core Flow

1. User uploads PDF on /.
2. app.py saves file temporarily in uploads/ and calls extract_eob_data in pdf_parser.py.
3. pdf_parser.py attempts extraction in layered order:
   - pdfplumber text + table extraction
   - Header pattern matching (many aliases/patterns)
   - Dynamic table-to-column mapping for service lines
   - Medicare fixed-width parser fallback
   - Text regex service-line fallback
   - OCR fallback for scanned/image PDFs (pytesseract + pdf2image)
   - Derived field computation when possible
4. Parsed result is saved to SQLite via SQLAlchemy models.
5. JSON is returned to frontend (static/js/main.js), which auto-fills form fields and totals.

## Data Storage

Database: SQLite (local file eob_data.db)

Tables:

- eob_records
  - Stores parsed header/payment metadata per upload.
  - Includes OCR flag and timestamps.
- service_lines
  - Stores line items linked to eob_records via foreign key.
  - Cascade delete enabled when parent record is deleted.

Main model files:

- models.py defines EOBRecord and ServiceLine.
- app.py writes each upload result to DB.

## API and Pages

Pages:

- GET / : upload + auto-fill UI
- GET /history : saved scan history UI

API:

- POST /upload : parse uploaded PDF, save DB record, return parsed JSON
- POST /debug-upload : debug parser output
- GET /api/records : paginated record list (+ search)
- GET /api/records/<id> : fetch one saved record
- DELETE /api/records/<id> : delete saved record and lines

## Project Structure

- app.py : Flask routes, upload handling, DB persistence
- pdf_parser.py : parsing engine (patterns, tables, OCR fallback)
- models.py : SQLAlchemy models
- templates/index.html : main form page
- templates/history.html : history/search page
- static/js/main.js : upload and auto-fill logic
- static/css/style.css : UI styling
- requirements.txt : Python dependencies

## Setup

1. Create and activate a virtual environment.
2. Install dependencies from requirements.txt.
3. Ensure OCR system tools are installed (for scanned PDFs):
   - tesseract
   - poppler-utils (pdftoppm)
4. Run Flask app:

   python app.py

5. Open http://127.0.0.1:5000

## OCR Notes

- OCR is used only when normal PDF text extraction is insufficient.
- OCR mode returns a notice in response so user can manually verify extracted values.
- Extraction accuracy for scanned documents depends on image quality and scan clarity.

## Current Matching Behavior (AdvancedMD-Oriented)

The parser and frontend auto-fill are tuned to map major payment posting fields such as:

- Patient, claim number, chart/account
- Responsible party/subscriber
- Carrier name
- Payment number/date
- Payment amount
- Service-line DOS/code/charge/allowed/payment/patient responsibility

Payment code/method and deposit date are auto-populated where source signals are present.

## Limitations

- Payer templates vary widely; uncommon layouts may still need additional patterns.
- Some fields are legitimately blank in source PDFs (e.g., COB/withhold/deduct on many remits).
- For best OCR results, use clean, high-resolution scans.

## Development Tips

- Use /debug-upload to inspect extracted raw parse behavior.
- Add new regex patterns/aliases in pdf_parser.py for new payer formats.
- Keep test PDFs from each payer/format to regression test extraction quality.

## Security / Privacy Note

This tool processes potentially sensitive medical billing data. Use in trusted/local environments and apply your organization’s compliance and retention policies.
