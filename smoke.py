import django, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.utils import timezone
from accounts.models import User
from events.models import Event
from giving.services import create_gift, confirm_gift, GiftBlocked
from giving.models import MonetaryGift
from wallet.models import Wallet, WithdrawalRequest, WithdrawalError
from config.constants import naira

host = User.objects.create_user("amara@example.com", "pw", first_name="Amara", last_name="O")
evt = Event.objects.create(host=host, slug="amara-tobi", title="Amara & Tobi",
                           occasion="wedding", starts_at=timezone.now(), is_published=True)

def gift(amount):
    r = create_gift(event=evt, amount_kobo=amount, giver_name="Guest")
    g = r["gift"]; confirm_gift(g); host.refresh_from_db(); return g

print("CAP TEST (cap = ₦100,000, host unverified)")
g1 = gift(60_000_00); print(f"  +₦60k  -> cumulative {naira(host.cumulative_received_kobo)}  crossing={g1.is_crossing}")
g2 = gift(50_000_00); print(f"  +₦50k  -> cumulative {naira(host.cumulative_received_kobo)}  crossing={g2.is_crossing}  (this one crossed the line)")
try:
    create_gift(event=evt, amount_kobo=10_000_00, giver_name="Late guest")
except GiftBlocked as e:
    print(f"  next gift BLOCKED -> \"{e}\"")

print("\nVERIFY NIN -> cap lifts")
host.mark_nin_verified()
g3 = gift(10_000_00); print(f"  +₦10k accepted -> cumulative {naira(host.cumulative_received_kobo)}")

w = Wallet.objects.get(host=host)
print(f"\nWALLET  available={naira(w.available_kobo)}  pending={naira(w.pending_kobo)}")
w.settle(w.pending_kobo, memo="settlement cleared")
print(f"  after settlement -> available={naira(w.available_kobo)}  pending={naira(w.pending_kobo)}")

print("\nWITHDRAWAL (active fee = FlatFee)")
wr = WithdrawalRequest.open(w, 120_000_00)
print(f"  withdraw {naira(wr.amount_kobo)}  fee {naira(wr.fee_kobo)}  net {naira(wr.net_kobo)}")
try:
    WithdrawalRequest.open(w, 1_000_00)
except WithdrawalError as e:
    print(f"  ₦1,000 withdrawal rejected -> \"{e}\"")
