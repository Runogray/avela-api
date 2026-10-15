"""
Serve stored images as real files instead of inlining them in JSON.

Covers, gallery photos and wishlist item images are stored as data URLs
(the app uploads them that way). Inlining them made every event response
megabytes large, so the API now returns a short, content-addressed URL:

    /api/img/<kind>/<id>/<digest>/

The digest changes whenever the image does, so responses can be cached by
browsers for a year. When the app sends one of these URLs back (an edit
that kept a photo, or moved a gallery photo to the cover), it is resolved
to the stored image, so nothing is lost or re-uploaded.
"""
import base64
import binascii
import hashlib
import re

from django.conf import settings
from django.http import HttpResponse, HttpResponseNotFound, HttpResponseRedirect
from django.views.decorators.http import require_GET
from rest_framework import serializers

from .models import Event, EventPhoto, WishlistItem

# kind -> (model, field holding the image)
KINDS = {
    "cover": (Event, "cover_image"),
    "photo": (EventPhoto, "image"),
    "item": (WishlistItem, "image"),
}
_URL_RE = re.compile(r"/api/img/(cover|photo|item)/([0-9a-f-]{36})/[0-9a-f]{6,40}/?$")
_DATA_RE = re.compile(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.*)$", re.S)


def digest(value: str) -> str:
    return hashlib.sha1(value.encode()).hexdigest()[:12]


def image_url(request, kind: str, pk, value: str) -> str:
    """Public URL for a stored image ("" if none; external URLs pass through)."""
    if not value:
        return ""
    if not value.startswith("data:"):
        return value
    path = f"/api/img/{kind}/{pk}/{digest(value)}/"
    base = getattr(settings, "API_PUBLIC_URL", "")
    if base:
        return base.rstrip("/") + path
    return request.build_absolute_uri(path) if request is not None else path


def resolve_image_value(value: str) -> str:
    """Turn one of our image URLs back into the stored image data."""
    if not value:
        return value
    m = _URL_RE.search(value)
    if not m:
        return value
    model, field = KINDS[m.group(1)]
    obj = model.objects.filter(pk=m.group(2)).only(field).first()
    if obj is None:
        raise serializers.ValidationError("That image no longer exists. Please upload it again.")
    return getattr(obj, field)


class StoredImageField(serializers.CharField):
    """Reads as a cacheable URL; writes accept a data URL, an external URL,
    or one of our own image URLs (resolved back to the stored image)."""

    def __init__(self, kind: str, **kwargs):
        kwargs.setdefault("allow_blank", True)
        kwargs.setdefault("required", False)
        kwargs.setdefault("trim_whitespace", False)
        self.kind = kind
        super().__init__(**kwargs)

    def get_attribute(self, instance):
        return (instance.pk, getattr(instance, self.source))

    def to_representation(self, pair):
        pk, value = pair
        return image_url(self.context.get("request"), self.kind, pk, value or "")

    def to_internal_value(self, data):
        return resolve_image_value(super().to_internal_value(data))


@require_GET
def serve_image(request, kind, pk, sha):
    if kind not in KINDS:
        return HttpResponseNotFound()
    model, field = KINDS[kind]
    obj = model.objects.filter(pk=pk).only(field).first()
    value = getattr(obj, field, "") if obj else ""
    if not value:
        return HttpResponseNotFound()
    if not value.startswith("data:"):
        return HttpResponseRedirect(value)
    m = _DATA_RE.match(value)
    if not m:
        return HttpResponseNotFound()
    try:
        body = base64.b64decode(m.group(2), validate=False)
    except (binascii.Error, ValueError):
        return HttpResponseNotFound()
    resp = HttpResponse(body, content_type=m.group(1))
    current = digest(value)
    # Content-addressed: the URL changes when the image does, so cache hard.
    # An old URL (image since replaced) still works but isn't cached long.
    resp["Cache-Control"] = ("public, max-age=31536000, immutable" if sha == current
                             else "public, max-age=60")
    resp["ETag"] = f'"{current}"'
    return resp
