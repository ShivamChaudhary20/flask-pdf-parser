// ===== PDF Upload & Auto-Fill Logic =====

const pdfInput = document.getElementById('pdfInput');
const uploadArea = document.getElementById('uploadArea');
const uploadStatus = document.getElementById('uploadStatus');
const claimSwitcher = document.getElementById('claimSwitcher');
const claimSelect = document.getElementById('claimSelect');
const claimMeta = document.getElementById('claimMeta');

let parsedClaims = [];

// Drag & Drop support
uploadArea.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadArea.style.borderColor = '#2d5a85';
    uploadArea.style.background = 'linear-gradient(135deg, #d0e6ff 0%, #e0d6ff 100%)';
});

uploadArea.addEventListener('dragleave', () => {
    uploadArea.style.borderColor = '#7baaf7';
    uploadArea.style.background = '';
});

uploadArea.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadArea.style.borderColor = '#7baaf7';
    uploadArea.style.background = '';
    const files = e.dataTransfer.files;
    if (files.length > 0 && files[0].type === 'application/pdf') {
        handleFileUpload(files[0]);
    } else {
        showStatus('Please drop a PDF file.', true);
    }
});

pdfInput.addEventListener('change', () => {
    if (pdfInput.files.length > 0) {
        handleFileUpload(pdfInput.files[0]);
    }
});

function showStatus(message, isError = false) {
    uploadStatus.textContent = message;
    uploadStatus.className = 'upload-status show' + (isError ? ' error' : '');
}

function showNotice(message) {
    // Remove any existing notice
    const existing = document.getElementById('ocrNotice');
    if (existing) existing.remove();

    const notice = document.createElement('div');
    notice.id = 'ocrNotice';
    notice.className = 'ocr-notice';
    notice.innerHTML = `
        <span class="ocr-notice-icon">&#9888;</span>
        <span class="ocr-notice-text">${message}</span>
        <button class="ocr-notice-close" onclick="this.parentElement.remove()">&times;</button>
    `;
    const container = document.querySelector('.container');
    const uploadSection = document.getElementById('uploadArea');
    container.insertBefore(notice, uploadSection.nextSibling);
}

function handleFileUpload(file) {
    showStatus('');
    // Clear any prior notice
    const oldNotice = document.getElementById('ocrNotice');
    if (oldNotice) oldNotice.remove();

    const formData = new FormData();
    formData.append('pdf_file', file);

    // Show loading state
    uploadStatus.innerHTML = '<span class="spinner"></span> Parsing PDF...';
    uploadStatus.className = 'upload-status show';

    fetch('/upload', {
        method: 'POST',
        body: formData
    })
    .then(res => res.json())
    .then(result => {
        if (result.error) {
            showStatus('Error: ' + result.error, true);
            return;
        }

        const records = result.records && result.records.length
            ? result.records
            : (result.data._records && result.data._records.length
                ? result.data._records
                : [result.data]);

        parsedClaims = records;
        renderClaimSelector(records);

        // Show notice if PDF was image-based / OCR-processed
        if (result.data._notice) {
            showNotice(result.data._notice);
        }

        // Check if any meaningful data was extracted
        const first = records[0] || {};
        const hasData = first.patient_name || first.claim_number || first.member_id ||
                        (first.service_lines && first.service_lines.length > 0);

        if (!hasData) {
            showStatus('PDF parsed but no data could be extracted. Try a different file.', true);
            return;
        }

        const ocrTag = result.data._notice ? ' (via OCR — verify data)' : '';
        if (records.length > 1) {
            showStatus(`PDF parsed successfully! Found ${records.length} patient claims${ocrTag}.`);
        } else {
            showStatus(`PDF parsed successfully! Form auto-filled${ocrTag}.`);
        }

        fillForm(records[0]);
    })
    .catch(err => {
        showStatus('Upload failed: ' + err.message, true);
    });
}

function renderClaimSelector(records) {
    if (!records || records.length <= 1) {
        claimSwitcher.style.display = 'none';
        claimSelect.innerHTML = '';
        claimMeta.textContent = '';
        return;
    }

    claimSwitcher.style.display = 'flex';
    claimSelect.innerHTML = '';

    records.forEach((rec, idx) => {
        const labelName = rec.patient_name || 'Unknown Patient';
        const labelClaim = rec.claim_number ? ` | Claim ${rec.claim_number}` : '';
        const opt = document.createElement('option');
        opt.value = String(idx);
        opt.textContent = `${idx + 1}. ${labelName}${labelClaim}`;
        claimSelect.appendChild(opt);
    });

    claimSelect.value = '0';
    claimMeta.textContent = `${records.length} claims extracted from this PDF`;
}

claimSelect.addEventListener('change', () => {
    const idx = parseInt(claimSelect.value, 10);
    if (Number.isNaN(idx) || !parsedClaims[idx]) {
        return;
    }
    fillForm(parsedClaims[idx]);
});

function resetFormValues() {
    document.getElementById('billingForm').reset();
    document.querySelectorAll('.filled').forEach(el => el.classList.remove('filled'));

    document.getElementById('serviceBody').innerHTML = `<tr class="empty-row">
        <td colspan="13" style="text-align:center;color:#999;padding:20px;">
            Upload a PDF to auto-fill service line details
        </td></tr>`;

    ['totalBilled','totalDisallow','totalAllowed','totalDeduct','totalCopay',
     'totalCob','totalWithhold','totalPaidProvider','totalPatientResp'].forEach(id => {
        document.getElementById(id).value = '';
    });
}

// ===== Auto-Fill Form from Extracted Data =====

function fillForm(data) {
    resetFormValues();

    const fieldMap = {
        'patientName': data.patient_name,
        'claimNumber': data.claim_number,
        'patientAccount': data.patient_account,
        'subscriberId': data.subscriber_id,
        'memberId': data.member_id,
        'subscriberName': data.subscriber_name,
        'dateReceived': data.date_received,
        'pcpNumber': data.pcp_number,
        'pcpName': data.pcp_name,
        'remitDetail': data.remit_detail,
        'productDesc': data.product_desc,
        'servicingProvNm': data.servicing_prov_nm,
        'servicingProvNpi': data.servicing_prov_npi,
        'billingNpi': data.billing_npi,
        'carrierId': data.carrier_name || data.carrier_id,
        'interestAmount': data.interest_amount,
        'totalPayable': data.total_payable_to_provider,
    };

    // Fill each form field
    for (const [id, value] of Object.entries(fieldMap)) {
        const el = document.getElementById(id);
        if (el && value) {
            el.value = value;
            el.classList.add('filled');
        }
    }

    // Update top bar patient info
    if (data.patient_name) {
        document.getElementById('topPatientInfo').textContent = 'PATIENT: ' + data.patient_name;
    }

    // Auto-set payment amount from total payable
    if (data.total_payable_to_provider) {
        const payAmtEl = document.getElementById('paymentAmount');
        payAmtEl.value = data.total_payable_to_provider;
        payAmtEl.classList.add('filled');
    }

    // Auto-set Payment Code to IP (Insurance Payment) for EOB/remittance PDFs
    const payCodeEl = document.getElementById('paymentCode');
    if (data.total_payable_to_provider || data.carrier_name) {
        payCodeEl.value = 'IP';
        payCodeEl.classList.add('filled');
    }

    // Auto-detect Payment Method from payment_number
    const payMethodEl = document.getElementById('paymentMethod');
    if (data.payment_number) {
        const pn = data.payment_number.toUpperCase();
        if (pn.includes('EFT') || pn.includes('ACH')) {
            payMethodEl.value = 'eft';
        } else {
            payMethodEl.value = 'check';
        }
        payMethodEl.classList.add('filled');
    }

    // Fill check/payment number into Authorization #
    if (data.payment_number) {
        const authEl = document.getElementById('authorizationNum');
        authEl.value = data.payment_number;
        authEl.classList.add('filled');
    }

    // Deposit Date from payment_date (fallback to today)
    const depositEl = document.getElementById('depositDate');
    if (data.payment_date) {
        // Convert MM/DD/YY to YYYY-MM-DD for date input
        const parts = data.payment_date.split('/');
        if (parts.length === 3) {
            let yr = parts[2];
            if (yr.length === 2) yr = '20' + yr;
            depositEl.value = `${yr}-${parts[0].padStart(2,'0')}-${parts[1].padStart(2,'0')}`;
        } else {
            depositEl.value = new Date().toISOString().split('T')[0];
        }
    } else {
        depositEl.value = new Date().toISOString().split('T')[0];
    }
    depositEl.classList.add('filled');

    // Set copay from first service line
    if (data.service_lines && data.service_lines.length > 0) {
        const firstLine = data.service_lines[0];
        if (firstLine.copay_coins_amt && firstLine.copay_coins_amt !== '0.00') {
            const copayEl = document.getElementById('copayAmount');
            copayEl.value = firstLine.copay_coins_amt;
            copayEl.classList.add('filled');
        }

        // Patient balance = sum of patient_resp_amt
        let totalPatResp = 0;
        data.service_lines.forEach(line => {
            totalPatResp += parseFloat(line.patient_resp_amt) || 0;
        });
        if (totalPatResp > 0) {
            const balEl = document.getElementById('patientBalance');
            balEl.value = totalPatResp.toFixed(2);
            balEl.classList.add('filled');
        }
    }

    // Fill service lines table
    fillServiceLines(data.service_lines || []);
}

// ===== Service Lines Table =====

function fillServiceLines(lines) {
    const tbody = document.getElementById('serviceBody');
    tbody.innerHTML = '';

    if (lines.length === 0) {
        tbody.innerHTML = `<tr class="empty-row">
            <td colspan="13" style="text-align:center;color:#999;padding:20px;">
                No service lines found in PDF
            </td></tr>`;
        return;
    }

    const totals = {
        billed: 0, disallow: 0, allowed: 0, deduct: 0,
        copay: 0, cob: 0, withhold: 0, paid: 0, patResp: 0
    };

    lines.forEach((line, idx) => {
        const tr = document.createElement('tr');
        const vals = [
            line.date_of_service,
            line.description,
            line.units,
            line.billed_amt,
            line.disallow_amt,
            line.allowed_amt,
            line.deduct_amt,
            line.copay_coins_amt,
            line.cob_pmt_amt,
            line.withhold_amt,
            line.paid_to_provider_amt,
            line.patient_resp_amt
        ];

        // Accumulate totals
        totals.billed += parseFloat(line.billed_amt) || 0;
        totals.disallow += parseFloat(line.disallow_amt) || 0;
        totals.allowed += parseFloat(line.allowed_amt) || 0;
        totals.deduct += parseFloat(line.deduct_amt) || 0;
        totals.copay += parseFloat(line.copay_coins_amt) || 0;
        totals.cob += parseFloat(line.cob_pmt_amt) || 0;
        totals.withhold += parseFloat(line.withhold_amt) || 0;
        totals.paid += parseFloat(line.paid_to_provider_amt) || 0;
        totals.patResp += parseFloat(line.patient_resp_amt) || 0;

        let html = `<td style="text-align:center;font-weight:600;">${idx + 1}</td>`;
        vals.forEach(v => {
            const val = v || '';
            html += `<td><input type="text" value="${escapeHtml(val)}" class="filled"></td>`;
        });

        tr.innerHTML = html;
        tbody.appendChild(tr);
    });

    // Fill totals
    document.getElementById('totalBilled').value = totals.billed.toFixed(2);
    document.getElementById('totalDisallow').value = totals.disallow.toFixed(2);
    document.getElementById('totalAllowed').value = totals.allowed.toFixed(2);
    document.getElementById('totalDeduct').value = totals.deduct.toFixed(2);
    document.getElementById('totalCopay').value = totals.copay.toFixed(2);
    document.getElementById('totalCob').value = totals.cob.toFixed(2);
    document.getElementById('totalWithhold').value = totals.withhold.toFixed(2);
    document.getElementById('totalPaidProvider').value = totals.paid.toFixed(2);
    document.getElementById('totalPatientResp').value = totals.patResp.toFixed(2);
}

// ===== Utility =====

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function clearForm() {
    document.getElementById('billingForm').reset();
    document.getElementById('topPatientInfo').textContent = 'PATIENT: --';
    parsedClaims = [];
    claimSwitcher.style.display = 'none';
    claimSelect.innerHTML = '';
    claimMeta.textContent = '';

    // Remove .filled class from all inputs
    document.querySelectorAll('.filled').forEach(el => el.classList.remove('filled'));

    // Reset service table
    document.getElementById('serviceBody').innerHTML = `<tr class="empty-row">
        <td colspan="13" style="text-align:center;color:#999;padding:20px;">
            Upload a PDF to auto-fill service line details
        </td></tr>`;

    // Clear totals
    ['totalBilled','totalDisallow','totalAllowed','totalDeduct','totalCopay',
     'totalCob','totalWithhold','totalPaidProvider','totalPatientResp'].forEach(id => {
        document.getElementById(id).value = '';
    });

    // Clear upload status
    uploadStatus.className = 'upload-status';
    pdfInput.value = '';
}

function submitForm() {
    const patientName = document.getElementById('patientName').value;
    const claimNumber = document.getElementById('claimNumber').value;
    const paymentAmount = document.getElementById('paymentAmount').value;

    if (!patientName || !claimNumber) {
        alert('Please fill in Patient Name and Claim Number before submitting.');
        return;
    }

    alert(`Payment submitted!\n\nPatient: ${patientName}\nClaim: ${claimNumber}\nAmount: $${paymentAmount || '0.00'}`);
}

// ===== Load Record from History =====

(function checkRecordParam() {
    const params = new URLSearchParams(window.location.search);
    const recordId = params.get('record');
    if (!recordId) return;

    fetch(`/api/records/${recordId}`)
        .then(r => r.json())
        .then(data => {
            showStatus(`Loaded record #${data.id} from history.`);
            fillForm(data);
        })
        .catch(() => showStatus('Failed to load record.', true));
})();
