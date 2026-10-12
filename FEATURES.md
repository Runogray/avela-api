# Avela — Feature Inventory & Build Plan

The exhaustive list of Avela features, each with **backend** status, **frontend**
status, and what's left to build. Avela is a web app (no landing page); screens
are the complete set from Figma — this is about making them real against the
Django API, not adding screens.

**Legend**
- Backend: ✅ done · ⚠️ partial · ❌ not built
- Frontend: ✅ wired to API · 🟡 UI done but on localStorage mock · — n/a
- Money is integer **kobo** everywhere.

---

## A. Authentication & Account
| # | Feature | Backend | Frontend | To build |
|---|---------|---------|----------|----------|
| A1 | Sign up (create account) | ✅ `/auth/register` (emails OTP) | 🟡 AuthScreen (register + mock fallback) | confirm real wiring, surface server errors |
| A2 | Log in | ✅ `/auth/login` | 🟡 AuthScreen | verify against API |
| A3 | Log out | ✅ `/auth/logout` | ✅ Profile.signOut | — |
| A4 | Email OTP verify | ✅ `/auth/verify-email` | 🟡 VerifyEmail (verify wired; resend is mock timer) | wire "Resend" → `/auth/send-email-otp` |
| A5 | Current user (me) | ✅ `/auth/me` GET | 🟡 Profile reads localStorage | load from `fetchMe` |
| A6 | Edit name / phone | ⚠️ `/auth/me` PATCH (name, phone) | 🟡 Profile edits localStorage | wire name/phone → `updateMe` |
| A7 | Change email (w/ OTP) | ❌ no email-change endpoint | 🟡 Profile change-email + OTP (mock) | **BE:** email-change + re-verify endpoint |
| A8 | Change password | ❌ no endpoint | 🟡 Profile (mock) | **BE:** `/auth/change-password`; wire FE |
| A9 | Gender | ❌ no field on User | 🟡 Profile (localStorage) | **BE:** add `gender` field (+migration) or keep FE-only |
| A10 | Profile avatar (8 presets) | ❌ no field | ✅ picker + onboarding (localStorage) | **BE:** add `avatar` field; persist via `updateMe` |
| A11 | Delete account | ❌ no endpoint | 🟡 Profile (clears localStorage) | **BE:** delete endpoint; wire FE |
| A12 | KYC / NIN verify | ✅ `/verify-nin` | 🟡 Wallet/Profile (mock) | wire |

## B. Events — Host / Dashboard
| # | Feature | Backend | Frontend | To build |
|---|---------|---------|----------|----------|
| B1 | Dashboard: list my events | ✅ `/host/events` GET | 🟡 EventsHome (`dote.events`) | wire list |
| B2 | Create event (occasion→details→upload→wishlist→preview→publish) | ✅ `/host/events` POST + nested wishlist/goals + `publish` | 🟡 CreateEventFlow (localStorage) | wire multi-step create |
| B3 | Edit event | ✅ PATCH `/host/events/{id}` | 🟡 edit flow | wire |
| B4 | Delete event | ✅ DELETE | 🟡 | wire |
| B5 | Publish / unpublish | ✅ `/host/events/{id}/publish` · `/unpublish` | 🟡 | wire |
| B6 | Manage event (overview, progress, gifts, rsvps, wishes) | ✅ detail + `rsvps`/`wishes` actions | 🟡 ManageEvent | wire |
| B7 | Wishlist items add/edit/remove | ✅ `/host/events/{id}/wishlist-items` + `/host/wishlist-items/{id}` | 🟡 | wire |
| B8 | Contribution goals add/edit/remove | ✅ `/host/.../goals` + `/host/goals/{id}` | 🟡 | wire |
| B9 | Event gallery (photos) | ❌ only `cover_image` URL | 🟡 ManageGallery | **BE:** `EventPhoto` model + endpoints (or keep cover-only) |
| B10 | Host reply / hide a wish | ✅ PATCH `/host/wishes/{id}` | 🟡 | wire |
| B11 | RSVP table assignment | ✅ PATCH `/host/rsvps/{id}` | 🟡 | wire (if surfaced) |

## C. Public / Guest (no account)
| # | Feature | Backend | Frontend | To build |
|---|---------|---------|----------|----------|
| C1 | Public event page by slug | ✅ `/events/{slug}` (enriched payload) | 🟡 EventView/EventOverview | wire guest page |
| C2 | RSVP | ✅ `/events/{slug}/rsvp` | 🟡 RsvpFlow | wire |
| C3 | Leave a wish | ✅ `/events/{slug}/wishes` | 🟡 WishForm | wire |
| C4 | Send money gift (direct/goal, 2 privacy axes, cap gate) | ✅ `/events/{slug}/gifts` → checkout_url | 🟡 GiftFlow | wire + checkout redirect |
| C5 | Reserve wishlist item (lock) | ✅ `/wishlist-items/{id}/reserve` | 🟡 ReserveFlow | wire |
| C6 | Recent gifts / wishes / RSVP count | ✅ in public payload | 🟡 | wire |
| C7 | "Gift box full" (A3 capped state) | ✅ `gifts_paused` | 🟡 | wire |
| C8 | Payment confirmation → status screen | ⚠️ webhook confirms server-side; **no gift-status GET for FE** | 🟡 TransactionStatus | **BE:** `GET /gifts/{id}/status`; FE poll/return handling |

## D. Wallet
| # | Feature | Backend | Frontend | To build |
|---|---------|---------|----------|----------|
| D1 | Balances + verification nudge | ✅ `/wallet` | 🟡 Wallet (`dote.wallettx`) | wire |
| D2 | Fee quote | ✅ `/wallet/quote` | 🟡 WalletWithdraw | wire |
| D3 | Withdraw (NIN + floor) | ✅ `/wallet/withdraw` | 🟡 | wire |
| D4 | Transactions list | ✅ `/wallet/transactions` | 🟡 WalletTransactions | wire |
| D5 | Withdrawals list | ✅ `/wallet/withdrawals` | 🟡 | wire |
| D6 | First-time withdraw KYC (BVN + bank account + PIN) | ❌ no bank-account / PIN model | 🟡 WalletWithdraw (mock) | **BE:** bank account + withdrawal PIN model/endpoints |
| D7 | Top up wallet | ❌ no top-up concept (wallet funds via gifts) | 🟡 WalletTopUp | **decide:** drop, or model self-funding |

## E. Invites received
| # | Feature | Backend | Frontend | To build |
|---|---------|---------|----------|----------|
| E1 | Invites list | ❌ no Invite model | 🟡 Invites (`dote.invites`) | **BE:** Invite model + endpoints |
| E2 | Open invite link → auto-register | ❌ | ✅ FE (localStorage) | **BE:** invite accept endpoint |

## F. Notifications
| # | Feature | Backend | Frontend | To build |
|---|---------|---------|----------|----------|
| F1 | Notifications list + read state | ❌ no Notification model | 🟡 Notifications (`dote.notifReadAt`) | **BE:** Notification model + feed (gift received, withdrawal, funding, nudge) |

## G. Settings / Support / Cross-cutting
| # | Feature | Backend | Frontend | To build |
|---|---------|---------|----------|----------|
| G1 | Help & support (email + socials + FAQ) | — | ✅ static (inside Settings) | — |
| G2 | Client-side nav, active states, avatar→Settings | — | ✅ | — |
| G3 | Root `/` redirect (no landing) | — | ✅ | — |
| G4 | Avatar first-run onboarding on dashboard | — | ✅ (localStorage) | tie to A10 once BE avatar exists |

---

## Build order (phases)
Each phase ends **green in the browser against the live API** before the next.

0. **Run locally** — Postgres+Django (Docker) + Next.js dev; confirm `GET /api/...` reachable from the app. *(gate for everything below)*
1. **Auth** — A1–A5 real end-to-end (register→OTP→me→login→logout); A4 resend.
2. **Host events** — B1–B8 (dashboard list/create/edit/delete/publish + wishlist/goals).
3. **Public guest** — C1–C7 (event page, RSVP, wish, gift, reserve, social proof, capped state).
4. **Wallet** — D1–D5.
5. **Profile depth** — A6, A10–A12 + small BE additions (avatar/gender field, password, delete).
6. **New BE features** — F1 notifications, E1–E2 invites (models + endpoints + FE wiring).
7. **Edges** — C8 gift status, D6 bank/PIN, B9 gallery, D7 top-up decision.

## Backend additions still required (net-new, need migrations)
- A7 email change, A8 password change, A9 gender, A10 avatar, A11 delete account
- C8 gift-status endpoint
- D6 bank account + withdrawal PIN
- E1/E2 Invite model, F1 Notification model
- B9 EventPhoto (gallery) — optional

## How to run locally
- **Backend:** `cd /Users/grey/Downloads/wishwell/wishwell_api && docker compose up --build` → API on `:8000` (migrations auto-apply; OTP codes print in the `web` logs).
- **Frontend:** `cd frontend && npm run dev` → `:3000` (or the in-app preview). Talks to `http://localhost:8000/api` by default; falls back to localStorage mock if the API is unreachable.
