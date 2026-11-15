from rest_framework import serializers
from .models import MonetaryGift, Wish


class CreateGiftSerializer(serializers.Serializer):
    amount_kobo = serializers.IntegerField(min_value=100)
    goal_id = serializers.UUIDField(required=False, allow_null=True)
    giver_name = serializers.CharField(required=False, allow_blank=True, max_length=120)
    message = serializers.CharField(required=False, allow_blank=True)
    show_name = serializers.BooleanField(default=True)
    show_amount = serializers.BooleanField(default=True)


class CreateTransferGiftSerializer(CreateGiftSerializer):
    giver_name = serializers.CharField(max_length=120)
    giver_email = serializers.EmailField(required=False, allow_blank=True)
    wishlist_item_id = serializers.UUIDField(required=False, allow_null=True)


class PublicGiftSerializer(serializers.ModelSerializer):
    """Honours both privacy axes: name and amount each hidden independently."""
    giver = serializers.SerializerMethodField()
    amount_kobo = serializers.SerializerMethodField()

    class Meta:
        model = MonetaryGift
        fields = ["id", "giver", "amount_kobo", "message", "created_at"]

    def get_giver(self, obj):
        return obj.public_giver

    def get_amount_kobo(self, obj):
        return obj.amount_kobo if obj.show_amount else None


class WishSerializer(serializers.ModelSerializer):
    """A guest's well wishes for the host. The email is write-only."""
    guest_name = serializers.CharField(max_length=120)
    guest_email = serializers.EmailField(required=False, allow_blank=True, write_only=True)
    # About six lines on the narrowest wish card in the app.
    message = serializers.CharField(max_length=220)

    class Meta:
        model = Wish
        fields = ["id", "guest_name", "guest_email", "message", "host_reply", "created_at"]
        read_only_fields = ["host_reply", "created_at"]

    def validate_guest_name(self, v):
        v = v.strip()
        if not v:
            raise serializers.ValidationError("Add your name.")
        return v

    def validate_guest_email(self, v):
        return (v or "").strip().lower()

    def validate_message(self, v):
        v = v.strip()
        if not v:
            raise serializers.ValidationError("Write a message.")
        return v
