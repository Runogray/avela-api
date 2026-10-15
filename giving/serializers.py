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
    class Meta:
        model = Wish
        fields = ["id", "guest_name", "message", "host_reply", "created_at"]
        read_only_fields = ["host_reply", "created_at"]
