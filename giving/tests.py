from datetime import timedelta

from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from accounts.models import BankAccount, Notification, User
from events.models import ContributionGoal, Event, WishlistItem
from wallet.models import Wallet
from .models import MonetaryGift


class BankTransferGiftTests(APITestCase):
    """Guest pays the host's own bank account; the host confirms it arrived."""

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
        self.assertEqual(public, ["data:image/jpeg;base64,BBB", "data:image/jpeg;base64,AAA"])
        self.assertEqual(self.client.delete(f"/api/host/photos/{a['id']}/").status_code, 204)
        self.assertEqual(self.client.get(f"/api/events/{self.event.slug}/").data["gallery"],
                         ["data:image/jpeg;base64,BBB"])

    def test_another_host_cannot_touch_photos(self):
        photo = self.client.post(f"/api/host/events/{self.event.id}/photos/",
                                 {"image": "data:image/jpeg;base64,AAA"}, format="json").data
        other = User.objects.create_user("intruder@example.test", "pw-123456")
        token, _ = Token.objects.get_or_create(user=other)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        self.assertEqual(self.client.patch(f"/api/host/photos/{photo['id']}/", {"order": 9}, format="json").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/host/photos/{photo['id']}/").status_code, 404)
