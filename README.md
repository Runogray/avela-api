# Avela API

Django + DRF backend for the celebration gifting platform. This drop is the
**core domain + business logic**; external gateways (Paystack, NIN) sit behind
interfaces in `integrations/gateways.py` and ship as stubs so the system runs
and tests without live keys.

## Apps
- **accounts** — custom `User` (host). Carries KYC state and the cumulative
  receiving total. `User.can_receive()` is the cap gate.
- **events** — `Event`, `RSVP`, `WishlistItem` (reservation lock),
  `ContributionGoal` (motivational progress).
- **giving** — `MonetaryGift` (direct or goal-linked, two privacy axes),
  `Wish`. `services.py` holds the create/confirm flow.
- **wallet** — `Wallet` (available/pending), `LedgerEntry`,
  `WithdrawalRequest`. `fees.py` is the swappable fee strategy.
- **integrations** — gateway + identity interfaces, stubs, payment webhook,
  NIN verification endpoint.

## Money
Stored as integer **kobo** everywhere (no floats). See `config/constants.py`.

## Run (Docker — recommended)
Django 6 needs Python 3.12+, so the API runs in a container (Postgres included).
```bash
cp .env.example .env              # optional; sane defaults work out of the box
docker compose up --build         # API on http://localhost:8000, Postgres on 5432
```
The `web` service waits for Postgres, applies migrations, then starts the dev
server with live reload (code is mounted). Common tasks:
```bash
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py migrate
docker compose exec web python smoke.py
docker compose down               # stop (add -v to also drop the DB volume)
```
Production-style run (gunicorn, DEBUG off): see the note at the bottom of
`docker-compose.yml`. Set a strong `DJANGO_SECRET_KEY` and real
`DJANGO_ALLOWED_HOSTS` first.

## Run (directly on the host)
Needs Python 3.12+ installed locally.
```bash
pip install -r requirements.txt
python manage.py migrate          # sqlite by default; set DATABASE_URL_HOST for Postgres
python manage.py runserver
python smoke.py                   # end-to-end logic demo (cap, wallet, fee)
```

## Configuration (env vars)
`DJANGO_SECRET_KEY`, `DJANGO_DEBUG` (1/0), `DJANGO_ALLOWED_HOSTS`,
`CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS`, `DATABASE_URL_HOST` +
`DB_NAME`/`DB_USER`/`DB_PASSWORD`/`DB_PORT`, `PAYSTACK_SECRET_KEY`,
`KYC_PROVIDER_KEY`. All have dev defaults; see `.env.example`.

## Key endpoints
- `POST /api/auth/register/`                 create account (emails an OTP) -> {token, user}
- `POST /api/auth/login/`                    -> {token, user}
- `POST /api/auth/logout/`                   invalidate token
- `GET/PATCH /api/auth/me/`                  current user
- `POST /api/auth/send-email-otp/`           (re)send the email verification code
- `POST /api/auth/verify-email/`             verify the emailed 6-digit OTP
- `GET  /api/events/{slug}/`                 public event page
- `POST /api/events/{slug}/rsvp/`            RSVP
- `POST /api/events/{slug}/wishes/`          leave a wish
- `POST /api/events/{slug}/gifts/`           send money (runs the cap gate)
- `POST /api/wishlist-items/{id}/reserve/`   reserve a gift (locks it)
- `GET  /api/wallet/`                        balances + verification nudge
- `GET  /api/wallet/quote/?amount_kobo=`     fee preview
- `GET  /api/wallet/transactions/`           ledger history (Transactions screen)
- `GET  /api/wallet/withdrawals/`            withdrawal requests
- `POST /api/wallet/withdraw/`               withdraw (NIN + floor enforced)
- `POST /api/verify-nin/`                    lift the receiving cap
- `POST /api/webhooks/payment/`              gateway confirmation -> confirm_gift

### Host-side CRUD (authenticated — manages the owner's own data)
- `GET/POST   /api/host/events/`                      list + create my events
- `GET/PATCH/DELETE /api/host/events/{id}/`           retrieve / update / delete
- `POST       /api/host/events/{id}/publish/`         publish (…/unpublish/ to reverse)
- `GET/POST   /api/host/events/{id}/wishlist-items/`  list + add wishlist items
- `GET/POST   /api/host/events/{id}/goals/`           list + add contribution goals
- `GET        /api/host/events/{id}/rsvps/`           RSVPs (host view)
- `GET        /api/host/events/{id}/wishes/`          wishes (host view)
- `PATCH/DELETE /api/host/wishlist-items/{id}/`       edit / remove an item
- `PATCH/DELETE /api/host/goals/{id}/`                edit / remove a goal
- `PATCH/DELETE /api/host/wishes/{id}/`               reply / hide / remove a wish
- `PATCH      /api/host/rsvps/{id}/`                  assign a table number

## Open decisions wired as seams
- **D1 fee model** — `wallet/fees.py`. `ACTIVE_FEE = FlatFee()` today; flip to
  `PercentFee(0.01)` to switch the platform. Nothing else changes.
- **D2 custody** — wallet represents the host's *view*; route real settlement
  through the gateway (sub-accounts) so the platform never holds pooled funds.
- **Paystack / NIN** — replace the two stubs in `integrations/gateways.py`.
