import os
import uuid
from flask import Flask, render_template, request, jsonify
from werkzeug.utils import secure_filename
from pdf_parser import extract_eob_data
from models import db, EOBRecord, ServiceLine
import pdfplumber

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "uploads")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB max
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

        # Save to database
        used_ocr = bool(data.get("_notice"))
        record = EOBRecord(
            filename=filename,
            used_ocr=used_ocr,
            patient_name=data.get("patient_name", ""),
            subscriber_id=data.get("subscriber_id", ""),
            member_id=data.get("member_id", ""),
            subscriber_name=data.get("subscriber_name", ""),
            date_received=data.get("date_received", ""),
            pcp_number=data.get("pcp_number", ""),
            pcp_name=data.get("pcp_name", ""),
            claim_number=data.get("claim_number", ""),
            remit_detail=data.get("remit_detail", ""),
            patient_account=data.get("patient_account", ""),
            product_desc=data.get("product_desc", ""),
            servicing_prov_npi=data.get("servicing_prov_npi", ""),
            servicing_prov_nm=data.get("servicing_prov_nm", ""),
            billing_npi=data.get("billing_npi", ""),
            carrier_id=data.get("carrier_id", ""),
            carrier_name=data.get("carrier_name", ""),
            payment_number=data.get("payment_number", ""),
            payment_date=data.get("payment_date", ""),
            interest_amount=data.get("interest_amount", ""),
            total_payable_to_provider=data.get("total_payable_to_provider", ""),
        )
        for sl in data.get("service_lines", []):
            record.service_lines.append(ServiceLine(
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
        db.session.add(record)
        db.session.commit()

        data["_record_id"] = record.id
        return jsonify({"success": True, "data": data})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to parse PDF: {str(e)}"}), 500
    finally:
        # Clean up uploaded file after processing
        if os.path.exists(filepath):
            os.remove(filepath)


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


if __name__ == "__main__":
    app.run(debug=True, port=5000)
