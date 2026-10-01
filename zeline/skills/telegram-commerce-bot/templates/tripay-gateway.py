# Tripay Payment Gateway — Standalone Module
# Copy into your bot project. Fill in credentials from tripay.co.id

import hashlib
import hmac
import json
import requests
from datetime import datetime


class TripayPayment:
    def __init__(self, api_key, private_key, merchant_code):
        self.api_key = api_key
        self.private_key = private_key
        self.merchant_code = merchant_code
        self.base_url = "https://tripay.co.id/api-sandbox"  # Ganti ke api untuk production

    def _sign(self, merchant_ref, amount):
        raw = f"{self.merchant_code}{merchant_ref}{amount}"
        return hmac.new(
            self.private_key.encode(),
            raw.encode(),
            hashlib.sha256
        ).hexdigest()

    def create_qris(self, amount, customer_name="Customer", order_id=None, expired=24):
        """Generate QRIS payment. Returns dict with qr_url (PNG), pay_url, reference."""
        import uuid
        merchant_ref = order_id or f"INV-{uuid.uuid4().hex[:10].upper()}"

        payload = {
            "method": "QRISC",
            "merchant_ref": merchant_ref,
            "amount": amount,
            "customer_name": customer_name[:50],
            "order_items": [
                {"name": "Digital Product", "price": amount, "quantity": 1}
            ],
            "expired_time": int((datetime.now().timestamp() + expired * 3600)),
            "signature": self._sign(merchant_ref, amount)
        }

        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            r = requests.post(f"{self.base_url}/transaction/create", json=payload, headers=headers, timeout=30)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError):
            # A timeout may occur after creation. Never automatically retry a POST.
            return {"success": False, "error": "Creation uncertain; reconcile merchant_ref before retry",
                    "merchant_ref": merchant_ref}

        if data.get("success"):
            return {
                "success": True,
                "reference": data["data"]["reference"],
                "merchant_ref": data["data"]["merchant_ref"],
                "qr_url": data["data"]["qr_url"],
                "qr_string": data["data"]["qr_string"],
                "pay_url": data["data"].get("pay_url") or data["data"].get("checkout_url"),
                "amount": data["data"]["amount"],
                "status": data["data"]["status"],
            }
        return {"success": False, "error": data.get("message", "Unknown error")}

    def check_payment(self, reference):
        """Check payment status by Tripay reference."""
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            r = requests.get(
                f"{self.base_url}/transaction/detail", params={"reference": reference},
                headers=headers, timeout=30
            )
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError):
            return {"success": False, "error": "Payment lookup failed; retry later"}

        if data.get("success"):
            return {
                "success": True,
                "reference": data["data"].get("reference"),
                "status": data["data"]["status"],  # UNPAID, PAID, EXPIRED, FAILED
                "amount": data["data"]["amount"],
                "paid_at": data["data"].get("paid_at"),
            }
        return {"success": False, "error": data.get("message")}

    @staticmethod
    def verify_callback(raw_body, signature, private_key):
        """Authenticate exact HTTP bytes against X-Callback-Signature, before JSON parsing.

        Authentication alone does not authorize delivery: match the stored reference,
        amount and merchant_ref, require PAID, and deduplicate the order separately.
        """
        if not isinstance(raw_body, bytes) or not isinstance(signature, str):
            return False
        if not private_key or len(signature) != 64:
            return False
        expected = hmac.new(private_key.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature.encode('utf-8'), expected.encode('ascii'))
