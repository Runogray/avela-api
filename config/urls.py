from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from accounts.views import NotificationViewSet
from events.views import EventViewSet, ReserveView
from events.host_views import (HostEventViewSet, HostGoalViewSet,
                               HostPhotoViewSet, HostRSVPViewSet,
                               HostWishViewSet, HostWishlistItemViewSet,
                               InviteViewSet)
from wallet.views import WalletViewSet
from integrations.views import gift_status, payment_webhook, verify_nin

router = DefaultRouter()
router.register("events", EventViewSet, basename="event")
router.register("wallet", WalletViewSet, basename="wallet")

# Authenticated host-side CRUD (owner manages their own events + nested data).
router.register("host/events", HostEventViewSet, basename="host-event")
router.register("host/wishlist-items", HostWishlistItemViewSet, basename="host-wishlist-item")
router.register("host/goals", HostGoalViewSet, basename="host-goal")
router.register("host/wishes", HostWishViewSet, basename="host-wish")
router.register("host/rsvps", HostRSVPViewSet, basename="host-rsvp")
router.register("host/photos", HostPhotoViewSet, basename="host-photo")

# Engagement — the current user's invites received + notifications feed.
router.register("invites", InviteViewSet, basename="invite")
router.register("notifications", NotificationViewSet, basename="notification")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("accounts.urls")),
    path("api/", include(router.urls)),
    path("api/wishlist-items/<uuid:item_id>/reserve/",
         ReserveView.as_view({"post": "create"}), name="reserve"),
    path("api/wishlist-items/<uuid:item_id>/bought/",
         ReserveView.as_view({"post": "bought"}), name="reserve-bought"),
    path("api/wishlist-items/<uuid:item_id>/release-hold/",
         ReserveView.as_view({"post": "release_hold"}), name="reserve-release"),
    path("api/webhooks/payment/", payment_webhook, name="payment-webhook"),
    path("api/gifts/<uuid:gift_id>/status/", gift_status, name="gift-status"),
    path("api/verify-nin/", verify_nin, name="verify-nin"),
]
