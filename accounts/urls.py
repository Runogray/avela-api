from django.urls import path

from .views import (ChangeEmailView, ChangePasswordView, ForgotPasswordView,
                    LoginView, LogoutView, MeView, RegisterView,
                    ResetPasswordView, SendEmailOTPView, VerifyEmailView)

urlpatterns = [
    path("auth/register/", RegisterView.as_view(), name="auth-register"),
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/logout/", LogoutView.as_view(), name="auth-logout"),
    path("auth/forgot-password/", ForgotPasswordView.as_view(), name="auth-forgot-password"),
    path("auth/reset-password/", ResetPasswordView.as_view(), name="auth-reset-password"),
    path("auth/me/", MeView.as_view(), name="auth-me"),
    path("auth/change-password/", ChangePasswordView.as_view(), name="auth-change-password"),
    path("auth/change-email/", ChangeEmailView.as_view(), name="auth-change-email"),
    path("auth/send-email-otp/", SendEmailOTPView.as_view(), name="auth-send-email-otp"),
    path("auth/verify-email/", VerifyEmailView.as_view(), name="auth-verify-email"),
]
