from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

db = SQLAlchemy()


class EOBRecord(db.Model):
    """Stores the parsed header fields from each uploaded PDF."""
    __tablename__ = "eob_records"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    filename = db.Column(db.String(255), nullable=False)
    used_ocr = db.Column(db.Boolean, default=False)

    # Patient & Claim
    patient_name = db.Column(db.String(255), default="")
    subscriber_id = db.Column(db.String(100), default="")
    member_id = db.Column(db.String(100), default="")
    subscriber_name = db.Column(db.String(255), default="")
    date_received = db.Column(db.String(50), default="")
    pcp_number = db.Column(db.String(100), default="")
    pcp_name = db.Column(db.String(255), default="")
    claim_number = db.Column(db.String(100), default="")
    remit_detail = db.Column(db.String(255), default="")
    patient_account = db.Column(db.String(100), default="")
    product_desc = db.Column(db.String(255), default="")

    # Provider & Billing
    servicing_prov_npi = db.Column(db.String(50), default="")
    servicing_prov_nm = db.Column(db.String(255), default="")
    billing_npi = db.Column(db.String(50), default="")
    carrier_id = db.Column(db.String(100), default="")
    carrier_name = db.Column(db.String(255), default="")
    payment_number = db.Column(db.String(100), default="")
    payment_date = db.Column(db.String(50), default="")
    interest_amount = db.Column(db.String(50), default="")
    total_payable_to_provider = db.Column(db.String(50), default="")

    # Relationship
    service_lines = db.relationship("ServiceLine", backref="eob_record",
                                    cascade="all, delete-orphan", lazy=True)

    def to_dict(self):
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "filename": self.filename,
            "used_ocr": self.used_ocr,
            "patient_name": self.patient_name,
            "subscriber_id": self.subscriber_id,
            "member_id": self.member_id,
            "subscriber_name": self.subscriber_name,
            "date_received": self.date_received,
            "pcp_number": self.pcp_number,
            "pcp_name": self.pcp_name,
            "claim_number": self.claim_number,
            "remit_detail": self.remit_detail,
            "patient_account": self.patient_account,
            "product_desc": self.product_desc,
            "servicing_prov_npi": self.servicing_prov_npi,
            "servicing_prov_nm": self.servicing_prov_nm,
            "billing_npi": self.billing_npi,
            "carrier_id": self.carrier_id,
            "carrier_name": self.carrier_name,
            "payment_number": self.payment_number,
            "payment_date": self.payment_date,
            "interest_amount": self.interest_amount,
            "total_payable_to_provider": self.total_payable_to_provider,
            "service_lines": [sl.to_dict() for sl in self.service_lines],
        }


class ServiceLine(db.Model):
    """Stores individual service line items for an EOB record."""
    __tablename__ = "service_lines"

    id = db.Column(db.Integer, primary_key=True)
    eob_record_id = db.Column(db.Integer, db.ForeignKey("eob_records.id"), nullable=False)

    date_of_service = db.Column(db.String(50), default="")
    description = db.Column(db.String(255), default="")
    units = db.Column(db.String(20), default="1")
    billed_amt = db.Column(db.String(50), default="0.00")
    disallow_amt = db.Column(db.String(50), default="0.00")
    allowed_amt = db.Column(db.String(50), default="0.00")
    deduct_amt = db.Column(db.String(50), default="0.00")
    copay_coins_amt = db.Column(db.String(50), default="0.00")
    cob_pmt_amt = db.Column(db.String(50), default="0.00")
    withhold_amt = db.Column(db.String(50), default="0.00")
    paid_to_provider_amt = db.Column(db.String(50), default="0.00")
    patient_resp_amt = db.Column(db.String(50), default="0.00")

    def to_dict(self):
        return {
            "id": self.id,
            "date_of_service": self.date_of_service,
            "description": self.description,
            "units": self.units,
            "billed_amt": self.billed_amt,
            "disallow_amt": self.disallow_amt,
            "allowed_amt": self.allowed_amt,
            "deduct_amt": self.deduct_amt,
            "copay_coins_amt": self.copay_coins_amt,
            "cob_pmt_amt": self.cob_pmt_amt,
            "withhold_amt": self.withhold_amt,
            "paid_to_provider_amt": self.paid_to_provider_amt,
            "patient_resp_amt": self.patient_resp_amt,
        }
