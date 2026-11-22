"""
One-off, after the Paystack key is set: give hosts who saved bank details
before card gifts existed a bank code, their bank's account name and the
Paystack subaccount card gifts settle into.

    python manage.py setup_paystack_subaccounts          # do it
    python manage.py setup_paystack_subaccounts --dry-run
"""
from django.core.management.base import BaseCommand

from accounts.models import BankAccount
from integrations.gateways import payment_gateway

# The names the old bank picker offered, with their CBN/Paystack codes.
OLD_NAMES = {
    "access bank": "044", "ecobank": "050", "fidelity bank": "070", "first bank": "011",
    "fcmb": "214", "gtbank": "058", "keystone bank": "082", "kuda": "50211",
    "moniepoint": "50515", "opay": "999992", "palmpay": "999991", "polaris bank": "076",
    "providus bank": "101", "stanbic ibtc": "221", "sterling bank": "232", "uba": "033",
    "union bank": "032", "wema bank": "035", "zenith bank": "057",
}


class Command(BaseCommand):
    help = "Set up Paystack subaccounts for bank accounts saved before card gifts."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, dry_run=False, **opts):
        done = skipped = 0
        for bank in BankAccount.objects.select_related("user").filter(paystack_subaccount=""):
            who = bank.user.email
            code = bank.bank_code or OLD_NAMES.get(bank.bank_name.strip().lower(), "")
            if not code:
                self.stdout.write(f"SKIP {who}: unknown bank {bank.bank_name!r}; ask them to re-save their bank details")
                skipped += 1
                continue
            name = payment_gateway.resolve_account(account_number=bank.account_number, bank_code=code)
            if not name:
                self.stdout.write(f"SKIP {who}: {bank.bank_name} account not found; ask them to re-save their bank details")
                skipped += 1
                continue
            if dry_run:
                self.stdout.write(f"WOULD SET UP {who}: {bank.bank_name} ({code}), {name}")
                done += 1
                continue
            sub = payment_gateway.create_subaccount(
                business_name=(bank.user.first_name or name).strip(), bank_code=code,
                account_number=bank.account_number, email=bank.user.email)
            if not sub:
                self.stdout.write(f"FAILED {who}: Paystack didn't create the subaccount")
                skipped += 1
                continue
            bank.bank_code, bank.account_name, bank.paystack_subaccount = code, name, sub
            bank.save(update_fields=["bank_code", "account_name", "paystack_subaccount"])
            self.stdout.write(f"OK {who}: {bank.bank_name}, {name}")
            done += 1
        self.stdout.write(f"{done} set up, {skipped} need attention.")
