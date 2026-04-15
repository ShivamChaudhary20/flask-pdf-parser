import os
import uuid
from flask import Flask, render_template, request, jsonify
from werkzeug.utils import secure_filename
from pdf_parser import extract_eob_data
from models import db, EOBRecord, ServiceLine
import pdfplumber

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "uploads")
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32 MB max
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "eob_data.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

with app.app_context():
    db.create_all()

ALLOWED_EXTENSIONS = {"pdf"}

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload_pdf():
    if "pdf_file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["pdf_file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "Only PDF files are allowed"}), 400

    # Save with a unique name to avoid collisions
    filename = secure_filename(file.filename)
    unique_name = f"{uuid.uuid4().hex}_{filename}"
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
    file.save(filepath)

    try:
        data = extract_eob_data(filepath)

        def norm(value):
            return (value or "").strip().lower()

        def find_existing_record(parsed_claim):
            claim_number = norm(parsed_claim.get("claim_number"))
            patient_name = norm(parsed_claim.get("patient_name"))
            payment_number = norm(parsed_claim.get("payment_number"))
            patient_account = norm(parsed_claim.get("patient_account"))

            # Primary duplicate key: claim number (optionally scoped by patient/payment).
            if claim_number:
                query = EOBRecord.query.filter(EOBRecord.claim_number.ilike(claim_number))
                if patient_name:
                    query = query.filter(EOBRecord.patient_name.ilike(patient_name))
                if payment_number:
                    query = query.filter(EOBRecord.payment_number.ilike(payment_number))
                existing = query.order_by(EOBRecord.id.desc()).first()
                if existing:
                    return existing

            # Fallback duplicate key when claim number is missing.
            if patient_name and (payment_number or patient_account):
                query = EOBRecord.query.filter(EOBRecord.patient_name.ilike(patient_name))
                if payment_number:
                    query = query.filter(EOBRecord.payment_number.ilike(payment_number))
                if patient_account:
                    query = query.filter(EOBRecord.patient_account.ilike(patient_account))
                existing = query.order_by(EOBRecord.id.desc()).first()
                if existing:
                    return existing

            return None

        def save_record(parsed_claim):
            used_ocr = bool(data.get("_notice"))
            rec = find_existing_record(parsed_claim)
            is_update = rec is not None

            if not rec:
                rec = EOBRecord()
                db.session.add(rec)

            rec.filename = filename
            rec.used_ocr = used_ocr
            rec.patient_name = parsed_claim.get("patient_name", "")
            rec.subscriber_id = parsed_claim.get("subscriber_id", "")
            rec.member_id = parsed_claim.get("member_id", "")
            rec.subscriber_name = parsed_claim.get("subscriber_name", "")
            rec.date_received = parsed_claim.get("date_received", "")
            rec.pcp_number = parsed_claim.get("pcp_number", "")
            rec.pcp_name = parsed_claim.get("pcp_name", "")
            rec.claim_number = parsed_claim.get("claim_number", "")
            rec.remit_detail = parsed_claim.get("remit_detail", "")
            rec.patient_account = parsed_claim.get("patient_account", "")
            rec.product_desc = parsed_claim.get("product_desc", "")
            rec.servicing_prov_npi = parsed_claim.get("servicing_prov_npi", "")
            rec.servicing_prov_nm = parsed_claim.get("servicing_prov_nm", "")
            rec.billing_npi = parsed_claim.get("billing_npi", "")
            rec.carrier_id = parsed_claim.get("carrier_id", "")
            rec.carrier_name = parsed_claim.get("carrier_name", "")
            rec.payment_number = parsed_claim.get("payment_number", "")
            rec.payment_date = parsed_claim.get("payment_date", "")
            rec.interest_amount = parsed_claim.get("interest_amount", "")
            rec.total_payable_to_provider = parsed_claim.get("total_payable_to_provider", "")

            # Replace line items fully on update to prevent duplicate service lines.
            rec.service_lines.clear()
            for sl in parsed_claim.get("service_lines", []):
                rec.service_lines.append(ServiceLine(
                    date_of_service=sl.get("date_of_service", ""),
                    description=sl.get("description", ""),
                    units=sl.get("units", "1"),
                    billed_amt=sl.get("billed_amt", "0.00"),
                    disallow_amt=sl.get("disallow_amt", "0.00"),
                    allowed_amt=sl.get("allowed_amt", "0.00"),
                    deduct_amt=sl.get("deduct_amt", "0.00"),
                    copay_coins_amt=sl.get("copay_coins_amt", "0.00"),
                    cob_pmt_amt=sl.get("cob_pmt_amt", "0.00"),
                    withhold_amt=sl.get("withhold_amt", "0.00"),
                    paid_to_provider_amt=sl.get("paid_to_provider_amt", "0.00"),
                    patient_resp_amt=sl.get("patient_resp_amt", "0.00"),
                ))
            return rec, is_update

        parsed_claims = data.get("_records") or [data]

        # If the parser returned a notice AND no usable data was extracted
        # (e.g. OCR completely failed), skip DB save and return immediately.
        if data.get("_notice"):
            any_data = any(
                c.get("patient_name") or c.get("claim_number") or c.get("service_lines")
                for c in parsed_claims
            )
            if not any_data:
                response_records = []
                for claim in parsed_claims:
                    claim_payload = dict(claim)
                    claim_payload["_notice"] = data["_notice"]
                    claim_payload.pop("_records", None)
                    claim_payload.pop("_raw_text", None)
                    response_records.append(claim_payload)
                primary = response_records[0]
                return jsonify({
                    "success": True,
                    "data": primary,
                    "records": response_records,
                    "total_records": len(response_records),
                    "updated_existing": 0,
                })

        saved_records = []
        updated_count = 0
        skipped = 0
        for claim in parsed_claims:
            # Skip claims with no meaningful data
            has_data = (
                claim.get("patient_name")
                or claim.get("claim_number")
                or claim.get("service_lines")
            )
            if not has_data:
                skipped += 1
                continue
            record, is_update = save_record(claim)
            saved_records.append(record)
            if is_update:
                updated_count += 1

        if not saved_records:
            db.session.rollback()
            return jsonify({
                "error": "PDF was parsed but no patient or claim data could be extracted."
            }), 400

        db.session.commit()

        response_records = []
        for idx, rec in enumerate(saved_records):
            # Find matching parsed claim for this saved record
            claim_payload = {}
            for claim in parsed_claims:
                if (claim.get("claim_number") == rec.claim_number
                        and claim.get("patient_name") == rec.patient_name):
                    claim_payload = dict(claim)
                    break
            if not claim_payload:
                claim_payload = saved_records[idx].to_dict()
            claim_payload["_record_id"] = rec.id
            if data.get("_notice"):
                claim_payload["_notice"] = data["_notice"]
            # Strip internal keys that cause circular refs or bloat
            claim_payload.pop("_records", None)
            claim_payload.pop("_raw_text", None)
            response_records.append(claim_payload)

        primary = response_records[0]
        return jsonify({
            "success": True,
            "data": primary,
            "records": response_records,
            "total_records": len(response_records),
            "updated_existing": updated_count,
        })
    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Failed to parse PDF: {str(e)}"}), 500
    finally:
        # Clean up uploaded file after successful processing
        # Keep file on error for debugging
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception:
                pass


@app.route("/debug-upload", methods=["POST"])
def debug_upload():
    """Debug endpoint: shows raw text + tables extracted by pdfplumber."""
    if "pdf_file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["pdf_file"]
    if not allowed_file(file.filename):
        return jsonify({"error": "Only PDF files are allowed"}), 400

    filename = secure_filename(file.filename)
    unique_name = f"{uuid.uuid4().hex}_{filename}"
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
    file.save(filepath)

    try:
        pages_data = []
        with pdfplumber.open(filepath) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                tables = page.extract_tables() or []
                pages_data.append({
                    "page": i + 1,
                    "text_lines": text.split("\n"),
                    "tables": tables,
                })
        return jsonify({"pages": pages_data})
    finally:
        if os.path.exists(filepath):
            os.remove(filepath)


# ===== History & Records API =====

@app.route("/history")
def history_page():
    return render_template("history.html")


@app.route("/api/records")
def get_records():
    """List all EOB records, newest first."""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    search = request.args.get("search", "").strip()

    query = EOBRecord.query
    if search:
        like = f"%{search}%"
        query = query.filter(
            db.or_(
                EOBRecord.patient_name.ilike(like),
                EOBRecord.claim_number.ilike(like),
                EOBRecord.member_id.ilike(like),
                EOBRecord.filename.ilike(like),
            )
        )
    query = query.order_by(EOBRecord.created_at.desc())
    paginated = query.paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        "records": [r.to_dict() for r in paginated.items],
        "total": paginated.total,
        "page": paginated.page,
        "pages": paginated.pages,
    })


@app.route("/api/records/<int:record_id>")
def get_record(record_id):
    """Get a single EOB record by ID."""
    record = db.get_or_404(EOBRecord, record_id)
    return jsonify(record.to_dict())


@app.route("/api/records/<int:record_id>", methods=["DELETE"])
def delete_record(record_id):
    """Delete an EOB record."""
    record = db.get_or_404(EOBRecord, record_id)
    db.session.delete(record)
    db.session.commit()
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# JSON error handlers — prevent Flask from returning HTML error pages
# ---------------------------------------------------------------------------

@app.errorhandler(400)
def bad_request(e):
    return jsonify({"error": "Bad request", "detail": str(e)}), 400

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found", "detail": str(e)}), 404

@app.errorhandler(413)
def file_too_large(e):
    return jsonify({"error": "File too large. Maximum upload size is 32 MB."}), 413

@app.errorhandler(500)
def internal_error(e):
    db.session.rollback()
    return jsonify({"error": f"Server error: {str(e)}"}), 500

@app.errorhandler(Exception)
def unhandled_exception(e):
    db.session.rollback()
    import traceback
    traceback.print_exc()
    return jsonify({"error": f"Failed to parse PDF: {str(e)}"}), 500


if __name__ == "__main__":
    app.run(debug=False, port=5000)
