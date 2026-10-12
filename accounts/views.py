"""Accounts auth views — register, login, current user.

Token auth (DRF `authtoken`): register and login both return a token the
frontend stores and sends as `Authorization: Token <token>`.
"""
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import generics, mixins, permissions, status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import EmailOTP
from .serializers import (LoginSerializer, NotificationSerializer,
                          RegisterSerializer, UserSerializer)
from .services import notify, send_email_otp, send_password_reset_otp

User = get_user_model()


def _auth_payload(user):
    token, _ = Token.objects.get_or_create(user=user)
    return {"token": token.key, "user": UserSerializer(user).data}


class RegisterView(APIView):
    """POST /api/auth/register/ -> create account, email an OTP, return {token, user}."""

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        send_email_otp(user)  # kicks off email verification (best-effort)
        notify(user, "system", "Welcome to Avela 🎉",
               "Create a celebration to start receiving gifts, RSVPs and good wishes.")
        return Response(_auth_payload(user), status=status.HTTP_201_CREATED)


class LoginView(APIView):
    """POST /api/auth/login/ -> validate credentials, return {token, user}."""

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        return Response(_auth_payload(serializer.validated_data["user"]))


class MeView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/auth/me/ -> read, update, or delete the current user."""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user


class ChangePasswordView(APIView):
    """POST /api/auth/change-password/ -> set a new password for the current user."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        current = request.data.get("current_password", "")
        new = str(request.data.get("new_password", ""))
        user = request.user
        # If the user has a usable password, require the current one to match.
        if user.has_usable_password() and current and not user.check_password(current):
            return Response(
                {"detail": "Current password is incorrect."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(new) < 6:
            return Response(
                {"detail": "Password must be at least 6 characters."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user.set_password(new)
        user.save(update_fields=["password"])
        # Rotate the token so other sessions are invalidated.
        Token.objects.filter(user=user).delete()
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"detail": "Password updated.", "token": token.key})


class ForgotPasswordView(APIView):
    """POST /api/auth/forgot-password/ {email} -> email a reset code.

    Always returns 200 (no account enumeration)."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = str(request.data.get("email", "")).strip().lower()
        user = User.objects.filter(email__iexact=email).first()
        if user is not None:
            send_password_reset_otp(user)
        return Response({"detail": "If that email exists, a reset code has been sent."})


class ResetPasswordView(APIView):
    """POST /api/auth/reset-password/ {email, code, new_password} -> reset + login."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = str(request.data.get("email", "")).strip().lower()
        code = str(request.data.get("code", "")).strip()
        new = str(request.data.get("new_password", ""))
        user = User.objects.filter(email__iexact=email).first()
        if user is None or not EmailOTP.verify(user, code):
            return Response({"detail": "That code is invalid or has expired."},
                            status=status.HTTP_400_BAD_REQUEST)
        if len(new) < 6:
            return Response({"detail": "Password must be at least 6 characters."},
                            status=status.HTTP_400_BAD_REQUEST)
        user.set_password(new)
        user.email_verified = True  # proved control of the inbox
        user.save(update_fields=["password", "email_verified"])
        Token.objects.filter(user=user).delete()
        return Response(_auth_payload(user))  # {token, user} -> auto sign-in


class ChangeEmailView(APIView):
    """POST /api/auth/change-email/ -> set a new email (unverified) + send OTP."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        email = str(request.data.get("email", "")).strip().lower()
        if "@" not in email or "." not in email:
            return Response({"detail": "Enter a valid email address."},
                            status=status.HTTP_400_BAD_REQUEST)
        if User.objects.filter(email__iexact=email).exclude(pk=request.user.pk).exists():
            return Response({"detail": "That email is already in use."},
                            status=status.HTTP_400_BAD_REQUEST)
        user = request.user
        user.email = email
        user.email_verified = False
        user.save(update_fields=["email", "email_verified"])
        send_email_otp(user)
        return Response(UserSerializer(user).data)


class SendEmailOTPView(APIView):
    """POST /api/auth/send-email-otp/ -> (re)send a verification code by email."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if request.user.email_verified:
            return Response({"detail": "Email already verified."})
        send_email_otp(request.user)
        return Response({"detail": "Verification code sent."})


class VerifyEmailView(APIView):
    """POST /api/auth/verify-email/ -> verify the emailed OTP, mark email verified."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        code = str(request.data.get("code", "")).strip()
        if not (code.isdigit() and len(code) == 6):
            return Response(
                {"detail": "Enter the 6-digit code."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user = request.user
        if not user.email_verified and not EmailOTP.verify(user, code):
            return Response(
                {"detail": "That code is invalid or has expired."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not user.email_verified:
            user.email_verified = True
            user.save(update_fields=["email_verified"])
        return Response(UserSerializer(user).data)


class LogoutView(APIView):
    """POST /api/auth/logout/ -> invalidate the current token."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class NotificationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """GET /api/notifications/ + POST /api/notifications/read-all/."""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = NotificationSerializer

    def get_queryset(self):
        return self.request.user.notifications.all()

    @action(detail=False, methods=["get"])
    def unread_count(self, request):
        n = request.user.notifications.filter(read_at__isnull=True).count()
        return Response({"unread": n})

    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        request.user.notifications.filter(read_at__isnull=True).update(
            read_at=timezone.now())
        return Response({"status": "ok"})
