"""
External service interfaces. Real implementations (Paystack, a NIN provider)
drop in behind these without touching domain code. Stubs let the whole system
run and be tested without live keys.
"""
import json
import uuid
from abc import ABC, abstractmethod
from urllib import error, request

from django.conf import settings


# --- Payments ---------------------------------------------------------------
class PaymentGateway(ABC):
    @abstractmethod
    def init_transaction(self, *, amount_kobo: int, email: str, metadata: dict) -> dict:
        """Return {'reference': str, 'checkout_url': str}."""

    @abstractmethod
    def verify(self, reference: str) -> bool:
        ...

    @abstractmethod
    def payout(self, *, amount_kobo: int, bank_account: dict) -> dict:
        ...


class StubPaymentGateway(PaymentGateway):
    def init_transaction(self, *, amount_kobo, email, metadata):
        ref = "stub_" + uuid.uuid4().hex[:18]
        return {"reference": ref, "checkout_url": f"https://pay.example/{ref}"}

    def verify(self, reference):
        return reference.startswith("stub_")

    def payout(self, *, amount_kobo, bank_account):
        return {"transfer_ref": "tr_" + uuid.uuid4().hex[:16], "status": "success"}


class PaystackGateway(PaymentGateway):
    """Live Paystack integration (used when PAYSTACK_SECRET_KEY is set).

    Uses urllib so no extra dependency is required. Amounts are in kobo, which
    is exactly what Paystack expects.
    """
    BASE = "https://api.paystack.co"

    def __init__(self, secret_key: str):
        self.secret = secret_key

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = request.Request(f"{self.BASE}{path}", data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.secret}")
        req.add_header("Content-Type", "application/json")
        try:
            with request.urlopen(req, timeout=20) as r:
                return json.loads(r.read() or "{}")
        except error.HTTPError as e:
            return json.loads(e.read() or "{}")

    def init_transaction(self, *, amount_kobo, email, metadata):
        res = self._call("POST", "/transaction/initialize", {
            "amount": amount_kobo, "email": email or "guest@avela.local",
            "metadata": metadata,
        })
        d = res.get("data") or {}
        return {"reference": d.get("reference", ""),
                "checkout_url": d.get("authorization_url", "")}

    def verify(self, reference):
        res = self._call("GET", f"/transaction/verify/{reference}")
        return (res.get("data") or {}).get("status") == "success"

    def payout(self, *, amount_kobo, bank_account):
        # Real payouts use Paystack transfers (recipient + transfer); left as a
        # follow-up. Returns a pending marker so the withdrawal flow proceeds.
        return {"transfer_ref": "ps_" + uuid.uuid4().hex[:16], "status": "pending"}


# --- Identity (NIN) ---------------------------------------------------------
class IdentityVerifier(ABC):
    @abstractmethod
    def verify_nin(self, *, nin: str, first_name: str, last_name: str) -> bool:
        ...


class StubIdentityVerifier(IdentityVerifier):
    def verify_nin(self, *, nin, first_name, last_name):
        return len(nin) == 11 and nin.isdigit()


# Use live Paystack when a secret key is configured, else the stub.
payment_gateway: PaymentGateway = (
    PaystackGateway(settings.PAYSTACK_SECRET_KEY)
    if settings.PAYSTACK_SECRET_KEY
    else StubPaymentGateway()
)
identity_verifier: IdentityVerifier = StubIdentityVerifier()
