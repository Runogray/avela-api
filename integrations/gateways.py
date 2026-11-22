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
    def init_transaction(self, *, amount_kobo: int, email: str, metadata: dict,
                         subaccount: str = "", transaction_charge: int | None = None,
                         callback_url: str = "", channels: list[str] | None = None) -> dict:
        """Return {'reference': str, 'checkout_url': str}. With `subaccount`, the
        payment settles to that account less `transaction_charge` (kobo), which
        the main account keeps; the main account bears the gateway fee."""

    @abstractmethod
    def verify(self, reference: str) -> bool:
        ...

    @abstractmethod
    def fetch(self, reference: str) -> dict:
        """{'status': 'success'|..., 'amount': kobo} for a transaction ({} if unknown)."""

    @abstractmethod
    def payout(self, *, amount_kobo: int, bank_account: dict) -> dict:
        ...

    @abstractmethod
    def list_banks(self) -> list[dict]:
        """Nigerian banks as [{'name', 'code'}]."""

    @abstractmethod
    def resolve_account(self, *, account_number: str, bank_code: str) -> str:
        """The account holder's name, or "" if the account can't be found."""

    @abstractmethod
    def create_subaccount(self, *, business_name: str, bank_code: str, account_number: str,
                          email: str = "") -> str:
        """A subaccount that receives payments straight into this bank account.
        Returns its code ("" on failure)."""


# Common Nigerian banks with their CBN/Paystack codes, for the stub (dev/tests).
STUB_BANKS = [
    {"name": "Access Bank", "code": "044"}, {"name": "Ecobank Nigeria", "code": "050"},
    {"name": "Fidelity Bank", "code": "070"}, {"name": "First Bank of Nigeria", "code": "011"},
    {"name": "First City Monument Bank", "code": "214"}, {"name": "Guaranty Trust Bank", "code": "058"},
    {"name": "Keystone Bank", "code": "082"}, {"name": "Kuda Bank", "code": "50211"},
    {"name": "Moniepoint MFB", "code": "50515"}, {"name": "OPay Digital Services Limited (OPay)", "code": "999992"},
    {"name": "PalmPay", "code": "999991"}, {"name": "Polaris Bank", "code": "076"},
    {"name": "Stanbic IBTC Bank", "code": "221"}, {"name": "Sterling Bank", "code": "232"},
    {"name": "Union Bank of Nigeria", "code": "032"}, {"name": "United Bank For Africa", "code": "033"},
    {"name": "Unity Bank", "code": "215"}, {"name": "Wema Bank", "code": "035"},
    {"name": "Zenith Bank", "code": "057"},
]


class StubPaymentGateway(PaymentGateway):
    """No live keys: payments "succeed" when the guest returns, so the whole
    flow can be run and tested locally. Never offered to guests in production
    (card gifts need a live key there; see card_payments_enabled)."""

    def init_transaction(self, *, amount_kobo, email, metadata, subaccount="",
                         transaction_charge=None, callback_url="", channels=None):
        ref = "stub_" + uuid.uuid4().hex[:18]
        # Skip the checkout page: go straight back as if the guest had paid.
        sep = "&" if "?" in callback_url else "?"
        url = f"{callback_url}{sep}reference={ref}" if callback_url else f"https://pay.example/{ref}"
        return {"reference": ref, "checkout_url": url}

    def verify(self, reference):
        return reference.startswith("stub_")

    def fetch(self, reference):
        return {"status": "success"} if reference.startswith("stub_") else {}

    def payout(self, *, amount_kobo, bank_account):
        return {"transfer_ref": "tr_" + uuid.uuid4().hex[:16], "status": "success"}

    def list_banks(self):
        return STUB_BANKS

    def resolve_account(self, *, account_number, bank_code):
        known = any(b["code"] == bank_code for b in STUB_BANKS)
        return "TEST ACCOUNT HOLDER" if known and len(account_number) == 10 and account_number.isdigit() else ""

    def create_subaccount(self, *, business_name, bank_code, account_number, email=""):
        return "ACCT_stub" + uuid.uuid4().hex[:10]


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
        # Paystack's firewall (Cloudflare 1010) rejects Python's default
        # "Python-urllib" agent, so name ourselves.
        req.add_header("User-Agent", "Avela/1.0 (+https://avelaevent.com)")
        try:
            with request.urlopen(req, timeout=20) as r:
                return json.loads(r.read() or "{}")
        except error.HTTPError as e:
            raw = e.read()
            try:
                return json.loads(raw or "{}")
            except ValueError:
                return {"status": False, "message": f"Paystack error {e.code}"}
        except (error.URLError, TimeoutError, ValueError):
            return {"status": False, "message": "Paystack unreachable"}

    def init_transaction(self, *, amount_kobo, email, metadata, subaccount="",
                         transaction_charge=None, callback_url="", channels=None):
        body = {"amount": amount_kobo, "email": email or "guests@avelaevent.com",
                "metadata": metadata}
        if subaccount:
            # The host's share settles straight to their bank; we keep the flat
            # transaction_charge, and (bearer "account") pay Paystack's fee from it.
            body.update(subaccount=subaccount, bearer="account")
            if transaction_charge is not None:
                body["transaction_charge"] = transaction_charge
        if callback_url:
            body["callback_url"] = callback_url
        if channels:
            body["channels"] = channels
        res = self._call("POST", "/transaction/initialize", body)
        d = res.get("data") or {}
        return {"reference": d.get("reference", ""),
                "checkout_url": d.get("authorization_url", "")}

    def verify(self, reference):
        return self.fetch(reference).get("status") == "success"

    def fetch(self, reference):
        res = self._call("GET", f"/transaction/verify/{reference}")
        d = res.get("data") or {}
        return {"status": d.get("status", ""), "amount": d.get("amount")} if d else {}

    def list_banks(self):
        res = self._call("GET", "/bank?country=nigeria&currency=NGN&perPage=100&use_cursor=false")
        return [{"name": b["name"], "code": b["code"]}
                for b in (res.get("data") or []) if b.get("active", True) and not b.get("is_deleted")]

    def resolve_account(self, *, account_number, bank_code):
        res = self._call("GET", f"/bank/resolve?account_number={account_number}&bank_code={bank_code}")
        return ((res.get("data") or {}).get("account_name") or "").strip() if res.get("status") else ""

    def create_subaccount(self, *, business_name, bank_code, account_number, email=""):
        body = {"business_name": business_name[:100] or "Avela host",
                # The docs name it bank_code; their example sends settlement_bank.
                "bank_code": bank_code, "settlement_bank": bank_code,
                "account_number": account_number, "percentage_charge": 0}
        if email:
            body["primary_contact_email"] = email
        res = self._call("POST", "/subaccount", body)
        return (res.get("data") or {}).get("subaccount_code", "") if res.get("status") else ""

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


def card_payments_enabled() -> bool:
    """Guests may pay by card only with a live gateway, or locally while DEBUG
    (the stub). Production without a key never offers it."""
    return bool(settings.PAYSTACK_SECRET_KEY) or settings.DEBUG


# Use live Paystack when a secret key is configured, else the stub.
payment_gateway: PaymentGateway = (
    PaystackGateway(settings.PAYSTACK_SECRET_KEY)
    if settings.PAYSTACK_SECRET_KEY
    else StubPaymentGateway()
)
identity_verifier: IdentityVerifier = StubIdentityVerifier()
