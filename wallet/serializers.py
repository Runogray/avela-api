from rest_framework import serializers

from accounts.models import BankAccount
from .models import LedgerEntry, WithdrawalRequest


class BankAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankAccount
        fields = ["bank_name", "account_number", "account_name"]


class LedgerEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = LedgerEntry
        fields = ["id", "kind", "amount_kobo", "memo", "created_at"]


class WithdrawalRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = WithdrawalRequest
        fields = ["id", "amount_kobo", "fee_kobo", "net_kobo", "status", "created_at"]
