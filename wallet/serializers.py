from rest_framework import serializers

from accounts.models import BankAccount
from .models import LedgerEntry, WithdrawalRequest


class BankAccountSerializer(serializers.ModelSerializer):
    # Guests can pay this host by card (their Paystack subaccount exists).
    card_ready = serializers.SerializerMethodField()

    class Meta:
        model = BankAccount
        fields = ["bank_name", "bank_code", "account_number", "account_name", "card_ready"]
        read_only_fields = ["account_name", "card_ready"]

    def get_card_ready(self, obj) -> bool:
        return bool(obj.paystack_subaccount)

    def validate_account_number(self, v):
        v = (v or "").strip()
        if not (v.isdigit() and len(v) == 10):
            raise serializers.ValidationError("Enter the 10-digit account number.")
        return v


class LedgerEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = LedgerEntry
        fields = ["id", "kind", "amount_kobo", "memo", "created_at"]


class WithdrawalRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = WithdrawalRequest
        fields = ["id", "amount_kobo", "fee_kobo", "net_kobo", "status", "created_at"]
