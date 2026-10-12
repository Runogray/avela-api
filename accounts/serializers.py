"""Accounts serializers — auth (register/login) and the current-user shape.

The `name` field maps to the User's first_name (v1 stores the full display name
there; last_name is unused). Shapes mirror what the frontend auth service
expects (src/lib/auth.ts).
"""
from django.contrib.auth import authenticate, get_user_model
from rest_framework import serializers

from .models import Notification

User = get_user_model()


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "kind", "title", "body", "created_at", "read_at"]
        read_only_fields = fields


class UserSerializer(serializers.ModelSerializer):
    """Public-facing current-user representation."""

    name = serializers.CharField(source="first_name", allow_blank=True, required=False)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "name",
            "phone",
            "gender",
            "avatar",
            "email_verified",
            "phone_verified",
            "nin_verified",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "email_verified",
            "phone_verified",
            "nin_verified",
            "created_at",
        ]


class RegisterSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=6, style={"input_type": "password"})
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def create(self, validated_data):
        return User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            first_name=validated_data.get("name", "").strip(),
            phone=validated_data.get("phone", "").strip(),
        )


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    def validate(self, attrs):
        email = attrs["email"].strip().lower()
        user = authenticate(
            request=self.context.get("request"),
            username=email,  # USERNAME_FIELD is email
            password=attrs["password"],
        )
        if not user:
            raise serializers.ValidationError("Invalid email or password.")
        if not user.is_active:
            raise serializers.ValidationError("This account is disabled.")
        attrs["user"] = user
        return attrs
