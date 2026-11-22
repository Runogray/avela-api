from datetime import timedelta

from django.core import mail
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from accounts.models import BankAccount, Notification, User
from events.models import ContributionGoal, Event, EventPhoto, WishlistItem
from wallet.models import Wallet
from .models import MonetaryGift, Wish


class _GiftFixtures(APITestCase):
    """Shared host, event, goal, item and helpers for the gift tests."""

    def setUp(self):
        self.host = User.objects.create_user("host@example.test", "pw-123456", first_name="Amara")
        self.event = Event.objects.create(
            host=self.host, slug="amara-30", title="#Amara30", occasion="birthday",
            starts_at=timezone.now() + timedelta(days=30), is_published=True,
        )
        self.goal = ContributionGoal.objects.create(event=self.event, name="Trip fund", target_kobo=50_000_00)
        self.item = WishlistItem.objects.create(event=self.event, name="Kettle", price_kobo=30_000_00)
        self.start_url = f"/api/events/{self.event.slug}/transfers/"

    def add_bank(self, user=None):
        return BankAccount.objects.create(user=user or self.host, bank_name="GTBank",
                                          account_number="0123456789", account_name="Amara Obi")

    def as_host(self, user=None):
        token, _ = Token.objects.get_or_create(user=user or self.host)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def start(self, **extra):
        body = {"amount_kobo": 10_000_00, "giver_name": "Tobi", "goal_id": str(self.goal.id)}
        body.update(extra)
        return self.client.post(self.start_url, body, format="json")



class BankTransferGiftTests(_GiftFixtures):
    """Guest pays the host's own bank account; the host confirms it arrived."""

    def test_public_event_flags_whether_transfers_are_possible(self):
        url = f"/api/events/{self.event.slug}/"
        self.assertFalse(self.client.get(url).data["accepts_transfers"])
        self.add_bank()
        self.assertTrue(self.client.get(url).data["accepts_transfers"])
        self.assertNotIn("account_number", str(self.client.get(url).data))

    def test_without_a_bank_account_the_guest_gets_a_clear_409(self):
        res = self.start()
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["code"], "no_bank_account")
        self.assertFalse(MonetaryGift.objects.exists())

    def test_start_returns_account_and_reference_and_stays_pending(self):
        self.add_bank()
        res = self.start()
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["account_number"], "0123456789")
        self.assertEqual(res.data["account_name"], "Amara Obi")
        self.assertRegex(res.data["reference"], r"^AV-[2-9A-HJKMNP-Z]{6}$")
        gift = MonetaryGift.objects.get(id=res.data["gift_id"])
        self.assertEqual(gift.method, MonetaryGift.Method.BANK_TRANSFER)
        self.assertEqual(gift.status, MonetaryGift.Status.INITIATED)
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.raised_kobo, 0)  # not counted until confirmed

    def test_giver_name_is_required(self):
        self.add_bank()
        self.assertEqual(self.start(giver_name="").status_code, 400)

    def test_cash_toward_a_physical_item_links_the_item(self):
        self.add_bank()
        res = self.start(goal_id=None, wishlist_item_id=str(self.item.id))
        gift = MonetaryGift.objects.get(id=res.data["gift_id"])
        self.assertEqual(gift.wishlist_item, self.item)
        self.assertIsNone(gift.goal)

    def test_guest_marks_sent_and_host_is_notified_once(self):
        self.add_bank()
        gift_id = self.start().data["gift_id"]
        url = f"{self.start_url}{gift_id}/sent/"
        self.assertTrue(self.client.post(url).data["marked_sent"])
        self.client.post(url)
        self.assertEqual(Notification.objects.filter(user=self.host, title="Confirm a transfer").count(), 1)
        # One email to the host, with what they need to check their bank.
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ["host@example.test"])
        self.assertEqual(email.subject, "Confirm a ₦10,000 gift from Tobi")
        ref = MonetaryGift.objects.get(id=gift_id).gateway_reference
        for expected in (ref, "Trip fund", "GTBank ••6789", f"/events/{self.event.id}"):
            self.assertIn(expected, email.body)
        html = email.alternatives[0][0]
        self.assertIn("Review and confirm", html)
        self.assertIn(ref, html)

    def test_no_email_until_the_guest_says_they_sent_it(self):
        self.add_bank()
        self.start()
        self.assertEqual(len(mail.outbox), 0)

    def test_an_email_failure_never_breaks_the_guests_tap(self):
        from unittest import mock
        self.add_bank()
        gift_id = self.start().data["gift_id"]
        with mock.patch("accounts.emails.send_contribution_to_confirm", side_effect=RuntimeError("smtp down")):
            res = self.client.post(f"{self.start_url}{gift_id}/sent/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["marked_sent"])

    def test_host_confirm_counts_toward_goal_but_not_the_wallet(self):
        self.add_bank()
        gift_id = self.start().data["gift_id"]
        self.as_host()
        listed = self.client.get(f"/api/host/events/{self.event.id}/transfers/").data
        self.assertEqual([g["id"] for g in listed], [gift_id])
        self.assertEqual(listed[0]["for_name"], "Trip fund")

        res = self.client.post(f"/api/host/events/{self.event.id}/transfers/{gift_id}/confirm/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["status"], "paid")
        self.goal.refresh_from_db()
        self.host.refresh_from_db()
        self.assertEqual(self.goal.raised_kobo, 10_000_00)
        self.assertEqual(self.host.cumulative_received_kobo, 10_000_00)
        wallet = Wallet.objects.filter(host=self.host).first()
        self.assertTrue(wallet is None or (wallet.available_kobo == 0 and wallet.pending_kobo == 0))

        # Confirming twice doesn't double count.
        self.client.post(f"/api/host/events/{self.event.id}/transfers/{gift_id}/confirm/")
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.raised_kobo, 10_000_00)

    def test_host_decline_removes_it_and_cannot_decline_a_confirmed_gift(self):
        self.add_bank()
        a = self.start().data["gift_id"]
        b = self.start().data["gift_id"]
        self.as_host()
        base = f"/api/host/events/{self.event.id}/transfers/"
        self.assertEqual(self.client.post(f"{base}{a}/decline/").data["status"], "failed")
        self.assertEqual([g["id"] for g in self.client.get(base).data], [b])
        self.client.post(f"{base}{b}/confirm/")
        self.assertEqual(self.client.post(f"{base}{b}/decline/").status_code, 409)

    def test_another_host_cannot_see_or_confirm(self):
        self.add_bank()
        gift_id = self.start().data["gift_id"]
        other = User.objects.create_user("other@example.test", "pw-123456")
        self.as_host(other)
        base = f"/api/host/events/{self.event.id}/transfers/"
        self.assertEqual(self.client.get(base).status_code, 404)
        self.assertEqual(self.client.post(f"{base}{gift_id}/confirm/").status_code, 404)
        self.assertEqual(MonetaryGift.objects.get(id=gift_id).status, MonetaryGift.Status.INITIATED)

    def test_confirmed_cash_toward_a_physical_item_shows_on_the_guest_page(self):
        self.add_bank()
        gift_id = self.start(goal_id=None, wishlist_item_id=str(self.item.id)).data["gift_id"]
        public = f"/api/events/{self.event.slug}/"
        item = lambda: next(w for w in self.client.get(public).data["wishlist_items"] if w["id"] == str(self.item.id))
        self.assertEqual(item()["raised_kobo"], 0)  # pending doesn't count
        self.as_host()
        self.client.post(f"/api/host/events/{self.event.id}/transfers/{gift_id}/confirm/")
        self.client.credentials()  # back to an anonymous guest
        self.assertEqual(item()["raised_kobo"], 10_000_00)
        self.as_host()
        host_items = self.client.get(f"/api/host/events/{self.event.id}/").data["wishlist_items"]
        self.assertEqual(host_items[0]["raised_kobo"], 10_000_00)

    def test_a_gift_with_contributions_cannot_be_deleted(self):
        self.add_bank()
        self.as_host()
        # Pending transfer on the item, confirmed one on the goal.
        self.start(goal_id=None, wishlist_item_id=str(self.item.id))
        gift_id = self.start().data["gift_id"]
        self.client.post(f"/api/host/events/{self.event.id}/transfers/{gift_id}/confirm/")

        for url in (f"/api/host/wishlist-items/{self.item.id}/", f"/api/host/goals/{self.goal.id}/"):
            res = self.client.delete(url)
            self.assertEqual(res.status_code, 409, url)
            self.assertEqual(res.data["code"], "has_contributions")
        self.assertTrue(WishlistItem.objects.filter(id=self.item.id).exists())
        host = self.client.get(f"/api/host/events/{self.event.id}/").data
        self.assertTrue(host["wishlist_items"][0]["has_contributions"])
        self.assertTrue(host["goals"][0]["has_contributions"])

    def test_a_gift_with_only_declined_transfers_can_be_deleted(self):
        self.add_bank()
        self.as_host()
        gift_id = self.start(goal_id=None, wishlist_item_id=str(self.item.id)).data["gift_id"]
        self.client.post(f"/api/host/events/{self.event.id}/transfers/{gift_id}/decline/")
        self.assertEqual(self.client.delete(f"/api/host/wishlist-items/{self.item.id}/").status_code, 204)


class HostPhotoTests(APITestCase):
    def setUp(self):
        self.host = User.objects.create_user("photos@example.test", "pw-123456")
        self.event = Event.objects.create(
            host=self.host, slug="photos-1", title="#Photos", occasion="birthday",
            starts_at=timezone.now() + timedelta(days=5), is_published=True)
        token, _ = Token.objects.get_or_create(user=self.host)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def test_host_can_add_reorder_and_delete_photos(self):
        base = f"/api/host/events/{self.event.id}/photos/"
        a = self.client.post(base, {"image": "data:image/jpeg;base64,AAA", "order": 1}, format="json").data
        b = self.client.post(base, {"image": "data:image/jpeg;base64,BBB", "order": 2}, format="json").data
        self.client.patch(f"/api/host/photos/{b['id']}/", {"order": 0}, format="json")
        public = self.client.get(f"/api/events/{self.event.slug}/").data["gallery"]
        self.assertEqual([u.split("/")[-3] for u in public], [b["id"], a["id"]])  # /api/img/photo/<id>/<sha>/
        self.assertEqual(self.client.delete(f"/api/host/photos/{a['id']}/").status_code, 204)
        public = self.client.get(f"/api/events/{self.event.slug}/").data["gallery"]
        self.assertEqual([u.split("/")[-3] for u in public], [b["id"]])

    def test_another_host_cannot_touch_photos(self):
        photo = self.client.post(f"/api/host/events/{self.event.id}/photos/",
                                 {"image": "data:image/jpeg;base64,AAA"}, format="json").data
        other = User.objects.create_user("intruder@example.test", "pw-123456")
        token, _ = Token.objects.get_or_create(user=other)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        self.assertEqual(self.client.patch(f"/api/host/photos/{photo['id']}/", {"order": 9}, format="json").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/host/photos/{photo['id']}/").status_code, 404)


class CoverPositionTests(APITestCase):
    def setUp(self):
        self.host = User.objects.create_user("cover@example.test", "pw-123456")
        self.event = Event.objects.create(
            host=self.host, slug="cover-1", title="#Cover", occasion="birthday",
            starts_at=timezone.now() + timedelta(days=5), is_published=True,
            cover_image="data:image/jpeg;base64,AAA")
        token, _ = Token.objects.get_or_create(user=self.host)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def test_host_sets_focus_point_and_guests_get_it(self):
        url = f"/api/host/events/{self.event.id}/"
        self.assertEqual(self.client.patch(url, {"cover_position": "35% 20%"}, format="json").status_code, 200)
        self.assertEqual(self.client.get(f"/api/events/{self.event.slug}/").data["cover_position"], "35% 20%")
        self.assertEqual(self.client.patch(url, {"cover_position": ""}, format="json").status_code, 200)

    def test_bad_focus_points_are_rejected(self):
        url = f"/api/host/events/{self.event.id}/"
        for bad in ("left top", "120% 0%", "50%", "50% 50%; background:red"):
            self.assertEqual(self.client.patch(url, {"cover_position": bad}, format="json").status_code, 400, bad)


class WishlistItemTakenTests(_GiftFixtures):
    """A wishlist item is bought or funded once; then it's off the list."""

    def item_state(self):
        items = self.client.get(f"/api/events/{self.event.slug}/").data["wishlist_items"]
        return next(w for w in items if w["id"] == str(self.item.id))

    def reserve(self, name="Ada"):
        return self.client.post(f"/api/wishlist-items/{self.item.id}/reserve/",
                                {"guest_name": name, "show_name": True}, format="json")

    def give(self, amount_kobo):
        return self.start(goal_id=None, wishlist_item_id=str(self.item.id), amount_kobo=amount_kobo)

    def bought(self, token):
        return self.client.post(f"/api/wishlist-items/{self.item.id}/bought/",
                                {"hold_token": token}, format="json")

    def test_buying_holds_the_item_for_ten_minutes(self):
        self.add_bank()
        res = self.reserve()
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["hold_token"])
        self.assertEqual(self.item_state()["availability"], "held")
        self.assertNotIn("hold_token", self.item_state())  # never leaked to others
        held = self.reserve("Kemi")
        self.assertEqual(held.status_code, 409)
        self.assertEqual(held.data["code"], "item_held")
        self.assertEqual(self.give(5_000_00).data["code"], "item_held")
        until = WishlistItem.objects.get(id=self.item.id).reserved_until
        self.assertAlmostEqual((until - timezone.now()).total_seconds(), 600, delta=5)

    def test_ive_bought_it_is_pending_until_the_host_confirms(self):
        self.add_bank()
        token = self.reserve().data["hold_token"]
        self.assertEqual(self.bought("wrong-token").status_code, 409)
        res = self.bought(token)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.item_state()["availability"], "pending")
        self.assertEqual(self.reserve("Kemi").data["code"], "item_pending")
        self.assertEqual(self.give(5_000_00).data["code"], "item_pending")
        self.assertTrue(Notification.objects.filter(user=self.host, title="Confirm a bought gift").exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Kettle", mail.outbox[0].subject)
        # Still pending after the 10 minutes are up.
        WishlistItem.objects.filter(id=self.item.id).update(reserved_until=timezone.now() - timedelta(hours=1))
        self.assertEqual(self.item_state()["availability"], "pending")
        # The host marks it as received -> bought for good.
        self.as_host()
        confirm = self.client.post(f"/api/host/wishlist-items/{self.item.id}/confirm-bought/")
        self.assertEqual(confirm.status_code, 200)
        self.client.credentials()
        self.assertEqual(self.item_state()["availability"], "bought")
        self.assertEqual(self.reserve("Kemi").data["code"], "item_bought")

    def test_host_cannot_confirm_a_gift_nobody_bought(self):
        self.as_host()
        self.assertEqual(self.client.post(f"/api/host/wishlist-items/{self.item.id}/confirm-bought/").status_code, 409)

    def test_gifts_awaiting_confirmation_that_cover_the_price_make_it_pending(self):
        self.add_bank()
        a = self.give(20_000_00).data["gift_id"]
        b = self.give(10_000_00).data["gift_id"]
        self.client.post(f"{self.start_url}{a}/sent/")
        self.assertEqual(self.item_state()["remaining_kobo"], 10_000_00)  # what's still free to give
        self.assertEqual(self.item_state()["availability"], "available")
        self.client.post(f"{self.start_url}{b}/sent/")
        self.assertEqual(self.item_state()["availability"], "pending")
        self.assertEqual(self.give(1_000_00).data["code"], "item_pending")
        # The host says one never arrived: the item reopens.
        self.as_host()
        self.client.post(f"/api/host/events/{self.event.id}/transfers/{b}/decline/")
        self.client.credentials()
        self.assertEqual(self.item_state()["availability"], "available")

    def test_an_unconfirmed_hold_lapses_and_the_item_is_free_again(self):
        self.add_bank()
        token = self.reserve().data["hold_token"]
        WishlistItem.objects.filter(id=self.item.id).update(reserved_until=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.item_state()["availability"], "available")
        # Nobody else took it yet: a late "I've bought it" still counts.
        self.assertEqual(self.bought(token).status_code, 200)
        self.assertEqual(self.item_state()["availability"], "pending")

    def test_a_lapsed_hold_taken_by_someone_else_cannot_be_confirmed(self):
        self.add_bank()
        old = self.reserve().data["hold_token"]
        WishlistItem.objects.filter(id=self.item.id).update(reserved_until=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.reserve("Kemi").status_code, 200)
        res = self.bought(old)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["code"], "item_held")

    def test_guest_can_cancel_their_hold(self):
        self.add_bank()
        token = self.reserve().data["hold_token"]
        res = self.client.post(f"/api/wishlist-items/{self.item.id}/release-hold/", {"hold_token": token}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.item_state()["availability"], "available")

    def test_host_can_release_a_bought_item_but_others_cannot(self):
        self.add_bank()
        self.bought(self.reserve().data["hold_token"])
        other = User.objects.create_user("nosy@example.test", "pw-123456")
        self.as_host(other)
        self.assertEqual(self.client.post(f"/api/host/wishlist-items/{self.item.id}/release/").status_code, 404)
        self.as_host()
        res = self.client.post(f"/api/host/wishlist-items/{self.item.id}/release/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["availability"], "available")
        self.assertEqual(res.data["reserved_by_name"], "")

    def test_confirmed_gifts_that_cover_the_price_close_the_item(self):
        self.add_bank()
        a = self.give(20_000_00).data["gift_id"]
        b = self.give(10_000_00).data["gift_id"]
        self.assertEqual(self.item_state()["availability"], "available")  # pending doesn't count
        self.as_host()
        base = f"/api/host/events/{self.event.id}/transfers/"
        self.client.post(f"{base}{a}/confirm/")
        self.assertEqual(self.item_state()["remaining_kobo"], 10_000_00)
        self.client.post(f"{base}{b}/confirm/")
        self.client.credentials()
        state = self.item_state()
        self.assertEqual(state["availability"], "funded")
        self.assertEqual(state["remaining_kobo"], 0)
        self.assertEqual(self.give(1_000_00).data["code"], "item_funded")
        self.assertEqual(self.reserve().data["code"], "item_funded")

    def test_a_contribution_cannot_exceed_what_is_left(self):
        self.add_bank()
        res = self.give(30_000_01)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["code"], "over_remaining")
        self.assertIn("₦30,000", res.data["detail"])
        self.assertEqual(self.give(30_000_00).status_code, 201)

    def test_cannot_buy_outright_once_friends_are_chipping_in(self):
        self.add_bank()
        gift_id = self.give(5_000_00).data["gift_id"]
        # A guest who only started a gift doesn't block buying...
        token = self.reserve().data["hold_token"]
        self.client.post(f"/api/wishlist-items/{self.item.id}/release-hold/", {"hold_token": token}, format="json")
        # ...but one who says they've sent it does.
        self.client.post(f"{self.start_url}{gift_id}/sent/")
        res = self.reserve()
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["code"], "has_contributions")

    def test_cash_goals_keep_accepting_gifts(self):
        self.add_bank()
        self.assertEqual(self.start(amount_kobo=60_000_00).status_code, 201)  # over the 50k target


class ImageServingTests(APITestCase):
    """Images go out as cacheable URLs, not megabytes of inline data."""

    PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
           "z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==")

    def setUp(self):
        self.host = User.objects.create_user("img@example.test", "pw-123456")
        self.event = Event.objects.create(
            host=self.host, slug="img-1", title="#Img", occasion="birthday",
            starts_at=timezone.now() + timedelta(days=5), is_published=True, cover_image=self.PNG)
        self.item = WishlistItem.objects.create(event=self.event, name="Kettle", price_kobo=30_000_00, image=self.PNG)
        token, _ = Token.objects.get_or_create(user=self.host)
        self.auth = {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def test_public_event_sends_urls_and_the_url_serves_the_image(self):
        data = self.client.get(f"/api/events/{self.event.slug}/").data
        cover = data["cover_image"]
        self.assertTrue(cover.startswith("http://testserver/api/img/cover/"), cover)
        self.assertTrue(data["wishlist_items"][0]["image"].startswith("http://testserver/api/img/item/"))
        res = self.client.get(cover.replace("http://testserver", ""))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "image/png")
        self.assertIn("immutable", res["Cache-Control"])
        self.assertTrue(res.content.startswith(b"\x89PNG"))

    def test_sending_our_url_back_keeps_the_image(self):
        url = self.client.get(f"/api/host/events/{self.event.id}/", **self.auth).data["cover_image"]
        res = self.client.patch(f"/api/host/events/{self.event.id}/", {"cover_image": url}, format="json", **self.auth)
        self.assertEqual(res.status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.cover_image, self.PNG)
        # Moving the cover into the gallery copies the image itself.
        photo = self.client.post(f"/api/host/events/{self.event.id}/photos/", {"image": url, "order": 1},
                                 format="json", **self.auth)
        self.assertEqual(photo.status_code, 201)
        self.assertEqual(EventPhoto.objects.get(id=photo.data["id"]).image, self.PNG)

    def test_public_event_query_count_does_not_grow_with_items(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        url = f"/api/events/{self.event.slug}/"
        with CaptureQueriesContext(connection) as one:
            self.client.get(url)
        for i in range(8):
            WishlistItem.objects.create(event=self.event, name=f"Gift {i}", price_kobo=10_000_00)
        with CaptureQueriesContext(connection) as nine:
            self.client.get(url)
        self.assertEqual(len(nine), len(one))


class GuestEmailAndThankYouTests(_GiftFixtures):
    """Guests who leave an email hear when their gift lands, and get thanked."""

    def test_guest_is_emailed_when_the_host_confirms_and_can_be_thanked(self):
        self.add_bank()
        gift_id = self.start(giver_email="Tobi@Example.test").data["gift_id"]
        self.assertEqual(MonetaryGift.objects.get(id=gift_id).giver_email, "tobi@example.test")
        self.as_host()
        base = f"/api/host/events/{self.event.id}/transfers/{gift_id}"
        # Can't thank before the gift is received.
        self.assertEqual(self.client.post(f"{base}/thank-you/", {"message": "Thanks!"}, format="json").status_code, 409)
        mail.outbox.clear()
        self.client.post(f"{base}/confirm/")
        self.assertEqual([m.to for m in mail.outbox], [["tobi@example.test"]])
        self.assertEqual(mail.outbox[0].subject, "Amara received your gift")
        self.assertIn("₦10,000", mail.outbox[0].body)
        mail.outbox.clear()
        self.assertEqual(self.client.post(f"{base}/thank-you/", {"message": ""}, format="json").status_code, 400)
        res = self.client.post(f"{base}/thank-you/", {"message": "Thank you Tobi!\nSee you there."}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["thanked_at"])
        self.assertEqual(mail.outbox[0].subject, "A thank-you from Amara")
        self.assertIn("See you there.", mail.outbox[0].body)
        self.assertIn("Thank you Tobi!<br>See you there.", mail.outbox[0].alternatives[0][0])

    def test_guest_is_told_when_the_transfer_did_not_arrive(self):
        self.add_bank()
        gift_id = self.start(giver_email="tobi@example.test").data["gift_id"]
        self.as_host()
        mail.outbox.clear()
        self.client.post(f"/api/host/events/{self.event.id}/transfers/{gift_id}/decline/")
        self.assertEqual(mail.outbox[0].to, ["tobi@example.test"])
        self.assertIn("hasn't arrived", mail.outbox[0].subject)

    def test_no_guest_email_means_no_mail_but_the_thank_you_still_reaches_the_gift_page(self):
        self.add_bank()
        started = self.start().data
        self.as_host()
        mail.outbox.clear()
        self.client.post(f"/api/host/events/{self.event.id}/transfers/{started['gift_id']}/confirm/")
        self.assertEqual(mail.outbox, [])
        res = self.client.post(f"/api/host/events/{self.event.id}/transfers/{started['gift_id']}/thank-you/",
                               {"message": "Thanks"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(mail.outbox, [])
        self.client.credentials()
        page = self.client.get(f"/api/my-gifts/{started['gift_token']}/").data
        self.assertEqual(page["thank_you_message"], "Thanks")

    def test_bad_guest_emails_are_rejected(self):
        self.add_bank()
        self.assertEqual(self.start(giver_email="not-an-email").status_code, 400)
        res = self.client.post(f"/api/wishlist-items/{self.item.id}/reserve/",
                               {"guest_name": "Ada", "guest_email": "nope"}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_buyer_is_emailed_when_received_and_can_be_thanked(self):
        token = self.client.post(f"/api/wishlist-items/{self.item.id}/reserve/",
                                 {"guest_name": "Ada Obi", "guest_email": "ada@example.test"},
                                 format="json").data["hold_token"]
        self.client.post(f"/api/wishlist-items/{self.item.id}/bought/", {"hold_token": token}, format="json")
        self.as_host()
        mail.outbox.clear()
        self.client.post(f"/api/host/wishlist-items/{self.item.id}/confirm-bought/")
        self.assertEqual(mail.outbox[0].to, ["ada@example.test"])
        self.assertIn("Kettle", mail.outbox[0].body)
        self.assertIn("Hi Ada,", mail.outbox[0].body)
        res = self.client.post(f"/api/host/wishlist-items/{self.item.id}/thank-you/",
                               {"message": "Love the kettle!"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["reserved_by_email"], "ada@example.test")
        self.assertTrue(res.data["thanked_at"])
        # Putting it back on the list clears the buyer's details.
        released = self.client.post(f"/api/host/wishlist-items/{self.item.id}/release/").data
        self.assertEqual(released["reserved_by_email"], "")


class GuestGiftPageTests(_GiftFixtures):
    """Each gift has a private page the guest can return to without an account."""

    def page(self, token):
        self.client.credentials()
        return self.client.get(f"/api/my-gifts/{token}/")

    def test_cash_gift_page_follows_the_gift_from_sent_to_thanked(self):
        self.add_bank()
        started = self.start(giver_email="tobi@example.test", wishlist_item_id=str(self.item.id),
                             goal_id=None).data
        token = started["gift_token"]
        self.assertGreaterEqual(len(token), 20)
        data = self.page(token).data
        self.assertEqual((data["kind"], data["stage"], data["amount_kobo"]), ("cash", "started", 10_000_00))
        self.assertEqual(data["item"]["name"], "Kettle")
        self.assertEqual(data["event"]["title"], "#Amara30")
        self.assertEqual(data["event"]["host_first_name"], "Amara")
        self.client.post(f"/api/events/{self.event.slug}/transfers/{started['gift_id']}/sent/")
        self.assertEqual(self.page(token).data["stage"], "sent")
        self.as_host()
        base = f"/api/host/events/{self.event.id}/transfers/{started['gift_id']}"
        mail.outbox.clear()
        self.client.post(f"{base}/confirm/")
        self.assertIn(f"/gift/{token}", mail.outbox[0].body)
        self.client.post(f"{base}/thank-you/", {"message": "Thank you Tobi!"}, format="json")
        self.assertIn(f"/gift/{token}", mail.outbox[-1].body)
        data = self.page(token).data
        self.assertEqual(data["stage"], "received")
        self.assertEqual(data["thank_you_message"], "Thank you Tobi!")
        self.assertTrue(data["thanked_at"])

    def test_declined_transfer_shows_as_not_received(self):
        self.add_bank()
        started = self.start().data
        self.as_host()
        self.client.post(f"/api/host/events/{self.event.id}/transfers/{started['gift_id']}/decline/")
        self.assertEqual(self.page(started["gift_token"]).data["stage"], "not_received")

    def test_bought_gift_page_and_thanks_without_an_email(self):
        res = self.client.post(f"/api/wishlist-items/{self.item.id}/reserve/",
                               {"guest_name": "Ada Obi"}, format="json").data
        token = res["gift_token"]
        self.assertEqual(self.page(token).data["stage"], "held")
        self.client.post(f"/api/wishlist-items/{self.item.id}/bought/", {"hold_token": res["hold_token"]},
                         format="json")
        data = self.page(token).data
        self.assertEqual((data["kind"], data["stage"], data["giver_name"]), ("item", "sent", "Ada Obi"))
        self.as_host()
        self.client.post(f"/api/host/wishlist-items/{self.item.id}/confirm-bought/")
        thanked = self.client.post(f"/api/host/wishlist-items/{self.item.id}/thank-you/",
                                   {"message": "Love it"}, format="json")
        self.assertEqual(thanked.status_code, 200)
        data = self.page(token).data
        self.assertEqual((data["stage"], data["thank_you_message"]), ("received", "Love it"))
        # Put back on the list: the old link no longer shows the gift.
        self.as_host()
        self.client.post(f"/api/host/wishlist-items/{self.item.id}/release/")
        self.assertEqual(self.page(token).status_code, 404)

    def test_tokens_are_private_and_unguessable(self):
        self.assertEqual(self.page("short").status_code, 404)
        self.assertEqual(self.page("x" * 24).status_code, 404)
        self.add_bank()
        # The public event never exposes gift tokens.
        token = self.start().data["gift_token"]
        self.assertNotIn(token, str(self.client.get(f"/api/events/{self.event.slug}/").data))


class WellWishesTests(_GiftFixtures):
    """Guests' well wishes reach the host, privately."""

    def setUp(self):
        super().setUp()
        from django.core.cache import cache
        cache.clear()  # throttle counts live in the cache
        self.url = f"/api/events/{self.event.slug}/wishes/"

    def send(self, **extra):
        body = {"guest_name": "  Ada Obi ", "guest_email": "Ada@Example.test", "message": "Happy birthday!\nLove, Ada"}
        body.update(extra)
        return self.client.post(self.url, body, format="json")

    def test_wish_is_stored_for_the_host_who_is_notified(self):
        res = self.send()
        self.assertEqual(res.status_code, 201)
        self.assertNotIn("guest_email", res.data)  # never echoed back
        wish = Wish.objects.get()
        self.assertEqual((wish.guest_name, wish.guest_email), ("Ada Obi", "ada@example.test"))
        note = Notification.objects.get(user=self.host)
        self.assertEqual((note.kind, note.title), ("wish", "New well wishes"))
        self.assertIn("Ada Obi", note.body)
        self.as_host()
        listed = self.client.get(f"/api/host/events/{self.event.id}/wishes/").data
        self.assertEqual(listed[0]["guest_email"], "ada@example.test")
        self.assertEqual(listed[0]["message"], "Happy birthday!\nLove, Ada")

    def test_wishes_are_not_published_on_the_invite(self):
        self.send()
        data = self.client.get(f"/api/events/{self.event.slug}/").data
        self.assertEqual(data["wishes"], [])
        self.assertNotIn("Happy birthday", str(data))
        self.assertNotIn("ada@example.test", str(data))

    def test_blank_or_overlong_wishes_are_rejected(self):
        self.assertEqual(self.send(guest_name="  ").status_code, 400)
        self.assertEqual(self.send(message="   ").status_code, 400)
        self.assertEqual(self.send(message="x" * 221).status_code, 400)
        self.assertEqual(self.send(message="x" * 220).status_code, 201)
        self.assertEqual(self.send(guest_email="nope").status_code, 400)
        self.assertEqual(self.send(guest_email="").status_code, 201)
        self.assertEqual(Wish.objects.count(), 2)

    def test_other_hosts_cannot_read_them(self):
        self.send()
        other = User.objects.create_user("other@example.test", "pw-123456")
        self.as_host(other)
        self.assertEqual(self.client.get(f"/api/host/events/{self.event.id}/wishes/").status_code, 404)

    def test_each_visitor_is_limited_per_hour(self):
        body = {"guest_name": "Ada", "message": "Hi"}
        def post(ip):
            return self.client.post(self.url, body, format="json", HTTP_X_FORWARDED_FOR=ip)
        for _ in range(30):
            self.assertEqual(post("10.0.0.1").status_code, 201)
        self.assertEqual(post("10.0.0.1").status_code, 429)
        # Another guest (a different address behind nginx) isn't affected.
        self.assertEqual(post("10.0.0.2").status_code, 201)


class NotificationLinkTests(_GiftFixtures):
    """Host notifications point at the gift they're about and can be read one by one."""

    def test_transfer_notification_opens_its_gift_and_can_be_marked_read(self):
        self.add_bank()
        started = self.start().data
        self.client.post(f"/api/events/{self.event.slug}/transfers/{started['gift_id']}/sent/")
        self.as_host()
        self.assertEqual(self.client.get("/api/notifications/unread_count/").data["unread"], 1)
        n = self.client.get("/api/notifications/").data[0]
        self.assertEqual(n["title"], "Confirm a transfer")
        self.assertEqual(n["link"],
                         f"/events/{self.event.id}?gift={self.goal.id}&contribution={started['gift_id']}")
        res = self.client.post(f"/api/notifications/{n['id']}/read/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["read_at"])
        self.assertEqual(self.client.get("/api/notifications/unread_count/").data["unread"], 0)

    def test_bought_gift_and_wish_notifications_link_to_the_right_place(self):
        hold = self.client.post(f"/api/wishlist-items/{self.item.id}/reserve/",
                                {"guest_name": "Ada"}, format="json").data["hold_token"]
        self.client.post(f"/api/wishlist-items/{self.item.id}/bought/", {"hold_token": hold}, format="json")
        from django.core.cache import cache
        cache.clear()
        self.client.post(f"/api/events/{self.event.slug}/wishes/",
                         {"guest_name": "Ada", "message": "Hi"}, format="json")
        links = dict(Notification.objects.filter(user=self.host).values_list("title", "link"))
        self.assertEqual(links["Confirm a bought gift"], f"/events/{self.event.id}?gift={self.item.id}")
        self.assertEqual(links["New well wishes"], f"/events/{self.event.id}#well-wishes")

    def test_hosts_cannot_mark_someone_elses_notification(self):
        self.add_bank()
        started = self.start().data
        self.client.post(f"/api/events/{self.event.slug}/transfers/{started['gift_id']}/sent/")
        n = Notification.objects.get(user=self.host)
        other = User.objects.create_user("other@example.test", "pw-123456")
        self.as_host(other)
        self.assertEqual(self.client.post(f"/api/notifications/{n.id}/read/").status_code, 404)
        n.refresh_from_db()
        self.assertIsNone(n.read_at)


class NotificationBackfillTests(_GiftFixtures):
    """Notifications from before links existed get one worked out from their words."""

    def run_backfill(self):
        from importlib import import_module
        from django.apps import apps
        import_module("accounts.migrations.0007_notification_link_backfill").backfill(apps, None)

    def test_old_notifications_get_links_from_their_words(self):
        self.add_bank()
        started = self.start().data
        gift = MonetaryGift.objects.get(id=started["gift_id"])
        self.item.purchased_at = timezone.now()
        self.item.save(update_fields=["purchased_at"])
        mk = lambda kind, title, body: Notification.objects.create(user=self.host, kind=kind, title=title, body=body)
        transfer = mk("contribution", "Confirm a transfer",
                      f"Tobi says they sent ₦10,000.00 for {self.event.title} (ref {gift.gateway_reference}). "
                      "Confirm it once it's in your account.")
        bought = mk("contribution", "Confirm a bought gift",
                    f"Ada says they bought Kettle for {self.event.title}. Mark it as received once it arrives, "
                    "or put it back on your list.")
        wish = mk("wish", "New well wishes", f"Ada left you a note on {self.event.title}.")
        wallet = mk("wallet", "Wallet funded", "₦5,000 was added to your wallet.")
        lost = mk("contribution", "Confirm a transfer", "Someone says they sent money (ref AV-NOPE99).")
        self.run_backfill()
        for n in (transfer, bought, wish, wallet, lost):
            n.refresh_from_db()
        self.assertEqual(transfer.link, f"/events/{self.event.id}?gift={self.goal.id}&contribution={gift.id}")
        self.assertEqual(bought.link, f"/events/{self.event.id}?gift={self.item.id}")
        self.assertEqual(wish.link, f"/events/{self.event.id}#well-wishes")
        self.assertEqual(wallet.link, "/wallet")
        self.assertEqual(lost.link, "")  # can't be matched: left alone

    def test_host_email_button_opens_the_contribution(self):
        self.add_bank()
        started = self.start().data
        mail.outbox.clear()
        self.client.post(f"/api/events/{self.event.slug}/transfers/{started['gift_id']}/sent/")
        self.assertIn(f"/events/{self.event.id}?gift={self.goal.id}&amp;contribution={started['gift_id']}",
                      mail.outbox[0].alternatives[0][0])


class OccasionTests(_GiftFixtures):
    """Graduation and Farewell (a celebration of life) are occasions of their own."""

    def test_hosts_can_create_graduation_and_farewell_events(self):
        self.as_host()
        for occasion in ("graduation", "farewell"):
            res = self.client.post("/api/host/events/", {
                "title": f"#{occasion}", "occasion": occasion,
                "starts_at": (timezone.now() + timedelta(days=10)).isoformat(),
            }, format="json")
            self.assertEqual(res.status_code, 201, res.data)
            self.assertEqual(res.data["occasion"], occasion)
            self.assertEqual(Event.objects.get(id=res.data["id"]).occasion, occasion)

    def test_unknown_occasions_are_still_rejected(self):
        self.as_host()
        res = self.client.post("/api/host/events/", {
            "title": "#x", "occasion": "picnic",
            "starts_at": (timezone.now() + timedelta(days=10)).isoformat(),
        }, format="json")
        self.assertEqual(res.status_code, 400)


    def test_tributes_on_a_farewell_are_called_tributes(self):
        from django.core.cache import cache
        cache.clear()
        self.event.occasion = "farewell"
        self.event.save(update_fields=["occasion"])
        self.client.post(f"/api/events/{self.event.slug}/wishes/",
                         {"guest_name": "Ada", "message": "She was so kind."}, format="json")
        n = Notification.objects.get(user=self.host, kind="wish")
        self.assertEqual(n.title, "New tribute")
        self.assertIn("left a tribute", n.body)


class RsvpTests(_GiftFixtures):
    """Guests say whether they're attending events that have a venue."""

    def setUp(self):
        super().setUp()
        from django.core.cache import cache
        cache.clear()
        self.event.venue = "Eko Hotel, Lagos"
        self.event.save(update_fields=["venue"])
        self.url = f"/api/events/{self.event.slug}/rsvp/"

    def reply(self, **extra):
        body = {"guest_name": " Ada Obi ", "status": "yes", "party_size": 3,
                "guest_email": "Ada@Example.test", "guest_phone": "0803 000 0000"}
        body.update(extra)
        return self.client.post(self.url, body, format="json")

    def test_a_reply_is_saved_with_its_party_size_and_can_be_changed(self):
        res = self.reply()
        self.assertEqual(res.status_code, 201)
        token = res.data["token"]
        self.assertGreaterEqual(len(token), 20)
        self.assertNotIn("guest_email", res.data)
        from events.models import RSVP
        r = RSVP.objects.get()
        self.assertEqual((r.guest_name, r.status, r.party_size, r.guest_email),
                         ("Ada Obi", "yes", 3, "ada@example.test"))
        # Same device changes its mind: the same reply is updated.
        res = self.reply(token=token, status="maybe", party_size=2)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(RSVP.objects.count(), 1)
        self.assertEqual(RSVP.objects.get().status, "maybe")

    def test_no_venue_or_switched_off_means_no_rsvps(self):
        self.event.venue = ""
        self.event.save(update_fields=["venue"])
        self.assertEqual(self.reply().data["code"], "rsvp_closed")
        self.event.venue = "Eko Hotel"
        self.event.rsvp_enabled = False
        self.event.save(update_fields=["venue", "rsvp_enabled"])
        self.assertEqual(self.reply().status_code, 409)

    def test_party_size_is_capped_by_the_host_and_a_no_counts_once(self):
        self.event.rsvp_party_max = 2
        self.event.save(update_fields=["rsvp_party_max"])
        res = self.reply(party_size=3)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["party_size"], ["You can bring a plus-one."])
        res = self.reply(status="no", party_size=9)
        self.assertEqual((res.status_code, res.data["party_size"]), (201, 1))
        self.assertEqual(self.reply(status="later").status_code, 400)
        self.assertEqual(self.reply(guest_name="  ").status_code, 400)

    def test_host_sees_replies_and_settings_but_guests_dont(self):
        self.reply()
        public = self.client.get(f"/api/events/{self.event.slug}/").data
        self.assertEqual((public["rsvp_enabled"], public["rsvp_party_max"], public["rsvp_yes_count"]), (True, 4, 1))
        self.assertNotIn("ada@example.test", str(public))
        self.as_host()
        rows = self.client.get(f"/api/host/events/{self.event.id}/rsvps/").data
        self.assertEqual((rows[0]["party_size"], rows[0]["guest_email"], rows[0]["guest_phone"]),
                         (3, "ada@example.test", "0803 000 0000"))
        res = self.client.patch(f"/api/host/events/{self.event.id}/", {"rsvp_party_max": 6, "rsvp_enabled": False},
                                format="json")
        self.assertEqual((res.status_code, res.data["rsvp_party_max"], res.data["rsvp_enabled"]), (200, 6, False))
        self.assertEqual(self.client.patch(f"/api/host/events/{self.event.id}/", {"rsvp_party_max": 50},
                                           format="json").status_code, 400)
        other = User.objects.create_user("other@example.test", "pw-123456")
        self.as_host(other)
        self.assertEqual(self.client.get(f"/api/host/events/{self.event.id}/rsvps/").status_code, 404)


from django.test import override_settings


@override_settings(PAYSTACK_SECRET_KEY="sk_test_avela", PAYSTACK_WEBHOOK_SECRET="")
class CardGiftTests(_GiftFixtures):
    """Guests pay by card on Paystack; it settles straight to the host's bank."""

    def setUp(self):
        super().setUp()
        self.as_host()
        res = self.client.put("/api/wallet/bank-account/", {
            "bank_name": "Guaranty Trust Bank", "bank_code": "058", "account_number": "0123456789"},
            format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.client.credentials()

    def test_fee_covers_paystack_so_the_host_gets_the_whole_gift(self):
        from giving.services import card_fee_kobo, gateway_fee_kobo
        self.assertEqual(card_fee_kobo(1_000_000), 25_400)      # ₦10,000 → ₦254 fee
        self.assertEqual(card_fee_kobo(100_000), 1_600)         # ₦1,000 → ₦16 (no ₦100 under ₦2,500)
        self.assertEqual(card_fee_kobo(20_000_000), 200_000)    # ₦200,000 → capped at ₦2,000
        for naira in list(range(100, 5_000, 7)) + [2_460, 2_470, 2_500, 150_000, 1_000_000]:
            gift = naira * 100
            fee = card_fee_kobo(gift)
            total = gift + fee
            self.assertEqual(total % 100, 0, naira)               # whole naira for the guest
            self.assertGreaterEqual(fee, gateway_fee_kobo(total), naira)  # Avela never loses
            self.assertLess(fee - gateway_fee_kobo(total), 200, naira)    # and keeps < ₦2 extra

    def test_bank_details_are_looked_up_and_set_up_for_card_gifts(self):
        from accounts.models import BankAccount
        bank = BankAccount.objects.get(user=self.host)
        self.assertEqual((bank.bank_code, bank.account_name), ("058", "TEST ACCOUNT HOLDER"))
        self.assertTrue(bank.paystack_subaccount.startswith("ACCT_"))
        first = bank.paystack_subaccount
        self.as_host()
        # Re-saving the same account keeps its subaccount; a typo is refused.
        self.client.put("/api/wallet/bank-account/", {
            "bank_name": "Guaranty Trust Bank", "bank_code": "058", "account_number": "0123456789"}, format="json")
        self.assertEqual(BankAccount.objects.get(user=self.host).paystack_subaccount, first)
        self.assertEqual(self.client.put("/api/wallet/bank-account/", {
            "bank_name": "GTBank", "bank_code": "058", "account_number": "12345"}, format="json").status_code, 400)
        self.assertEqual(self.client.put("/api/wallet/bank-account/", {
            "bank_name": "Nowhere", "bank_code": "999", "account_number": "0123456789"}, format="json").status_code, 400)
        res = self.client.post("/api/wallet/resolve-account/", {"bank_code": "058", "account_number": "0123456789"},
                               format="json")
        self.assertEqual(res.data["account_name"], "TEST ACCOUNT HOLDER")
        self.assertTrue(any(b["code"] == "058" for b in self.client.get("/api/wallet/banks/").data))

    def test_card_gift_from_checkout_to_received(self):
        public = self.client.get(f"/api/events/{self.event.slug}/").data
        self.assertTrue(public["accepts_card"])
        quote = self.client.get(f"/api/events/{self.event.slug}/card-fee/?amount_kobo=1000000").data
        self.assertEqual(quote["total_kobo"], 1_025_400)
        res = self.client.post(f"/api/events/{self.event.slug}/card-gifts/", {
            "amount_kobo": 1_000_000, "giver_name": "Tobi", "giver_email": "tobi@example.test",
            "goal_id": str(self.goal.id)}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual((res.data["fee_kobo"], res.data["total_kobo"]), (25_400, 1_025_400))
        token = res.data["gift_token"]
        self.assertIn(f"/gift/{token}?reference=stub_", res.data["checkout_url"])
        self.assertTrue(res.data["checkout_url"].startswith("https://avelaevent.com/gift/"))
        # A trusted frontend gets its guests back; anywhere else is ignored.
        for origin, expect in (("http://localhost:3000", "http://localhost:3000/gift/"),
                               ("https://evil.example", "https://avelaevent.com/gift/")):
            again = self.client.post(f"/api/events/{self.event.slug}/card-gifts/", {
                "amount_kobo": 100_000, "giver_name": "Tobi", "goal_id": str(self.goal.id),
                "return_origin": origin}, format="json")
            self.assertTrue(again.data["checkout_url"].startswith(expect), origin)
        gift = MonetaryGift.objects.get(id=res.data["gift_id"])
        self.assertEqual((gift.method, gift.status, gift.paid_direct), ("gateway", "initiated", True))
        # The guest comes back to their gift page: it checks with Paystack.
        mail.outbox.clear()
        page = self.client.get(f"/api/my-gifts/{token}/").data
        self.assertEqual((page["stage"], page["method"], page["fee_kobo"]), ("received", "card", 25_400))
        gift.refresh_from_db()
        self.assertEqual(gift.status, "paid")
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.raised_kobo, 1_000_000)
        # Straight to the host's bank: the Avela wallet is never credited.
        self.assertFalse(Wallet.objects.filter(host=self.host, pending_kobo__gt=0).exists())
        note = Notification.objects.get(user=self.host, title="New gift")
        self.assertIn("by card", note.body)
        self.assertEqual(mail.outbox[0].to, ["tobi@example.test"])
        # The host sees it beside transfers and can thank the guest.
        self.as_host()
        rows = self.client.get(f"/api/host/events/{self.event.id}/transfers/").data
        self.assertEqual([(r["method"], r["status"]) for r in rows], [("gateway", "paid")])
        self.assertEqual(self.client.post(f"/api/host/events/{self.event.id}/transfers/{gift.id}/thank-you/",
                                          {"message": "Thanks!"}, format="json").status_code, 200)
        # A host can't "confirm" a card gift by hand.
        self.assertEqual(self.client.post(
            f"/api/host/events/{self.event.id}/transfers/{gift.id}/confirm/").status_code, 404)

    def test_no_subaccount_means_no_card_gifts(self):
        from accounts.models import BankAccount
        BankAccount.objects.filter(user=self.host).update(paystack_subaccount="")
        self.assertFalse(self.client.get(f"/api/events/{self.event.slug}/").data["accepts_card"])
        res = self.client.post(f"/api/events/{self.event.slug}/card-gifts/", {
            "amount_kobo": 500_000, "giver_name": "Tobi", "goal_id": str(self.goal.id)}, format="json")
        self.assertEqual(res.data["code"], "no_card_payments")

    def start_gift(self):
        return self.client.post(f"/api/events/{self.event.slug}/card-gifts/", {
            "amount_kobo": 500_000, "giver_name": "Tobi", "goal_id": str(self.goal.id)}, format="json").data

    def test_webhook_with_the_older_signature(self):
        import hashlib, hmac, json
        ref = MonetaryGift.objects.get(id=self.start_gift()["gift_id"]).gateway_reference
        body = json.dumps({"event": "charge.success", "data": {"reference": ref}}).encode()
        sig = hmac.new(b"sk_test_avela", body, hashlib.sha512).hexdigest()
        post = lambda s: self.client.post("/api/webhooks/payment/", body, content_type="application/json",
                                          HTTP_X_PAYSTACK_SIGNATURE=s)
        self.assertEqual(post("0" * 128).status_code, 401)
        self.assertEqual(post(sig).status_code, 200)
        self.assertEqual(MonetaryGift.objects.get(gateway_reference=ref).status, "paid")
        # Other events and unknown references are acknowledged, not retried.
        other = json.dumps({"event": "transfer.success", "data": {"reference": "x"}}).encode()
        self.assertEqual(self.client.post("/api/webhooks/payment/", other, content_type="application/json",
                                          HTTP_X_PAYSTACK_SIGNATURE=hmac.new(b"sk_test_avela", other, hashlib.sha512).hexdigest()).status_code, 200)

    @override_settings(PAYSTACK_WEBHOOK_SECRET="whsec_test")
    def test_webhook_with_the_new_signature(self):
        import hashlib, hmac, json, time
        ref = MonetaryGift.objects.get(id=self.start_gift()["gift_id"]).gateway_reference
        body = json.dumps({"event": "charge.success", "data": {"reference": ref}}).encode()
        def header(t, secret=b"whsec_test"):
            sig = hmac.new(secret, f"{t}.".encode() + body, hashlib.sha512).hexdigest()
            return f"t={t},v1=whk_abc:{sig}"
        post = lambda h: self.client.post("/api/webhooks/payment/", body, content_type="application/json",
                                          HTTP_PAYSTACK_SIGNATURE=h)
        now = int(time.time())
        self.assertEqual(post(header(now - 1000)).status_code, 401)      # stale: replay refused
        self.assertEqual(post(header(now, b"whsec_wrong")).status_code, 401)
        self.assertEqual(post(header(now)).status_code, 200)
        self.assertEqual(MonetaryGift.objects.get(gateway_reference=ref).status, "paid")
