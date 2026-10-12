# Avela — Product Requirements Document

> **The celebration layer on top of payments** — a single page to manage an event, receive gifts, collect well-wishes, and keep the memories, for weddings, birthdays, and baby showers.

| | |
|---|---|
| **Document owner** | Product |
| **Status** | Draft for review |
| **Version** | 1.0 |
| **Last updated** | 25 September 2026 |
| **Source material** | Avela User Stories v0.2 (June 2026); implemented core-domain backend (`wishwell_api`) |
| **Target release** | MVP (P0 scope) |

---

## 1. Overview

Avela is a web platform where a **host** creates one shareable page for a celebration — a wedding, a birthday, a baby shower — and **guests** arrive via a link (no account needed) to RSVP, send money gifts, contribute to goals, reserve physical wishlist items, and leave well-wishes.

The phrase *"on top of payments"* is load-bearing. The defensible, monetizable core is **money moving toward a person on an occasion**. RSVP, wishes, and event details are the social wrapper that makes that money flow feel natural and trustworthy. Every scope decision in this document protects that core.

### 1.1 Why now

- Cash and bank-transfer gifting for Nigerian celebrations is fragmented, awkward, and impossible for diaspora guests to participate in cleanly.
- Existing tools are either generic (registries built for other markets) or purely social (event pages with no real money rail).
- A single page that is **warm enough to feel like a gift and sober enough to trust with money** is an unoccupied position.

### 1.2 Vision

Every celebrant has one beautiful link. They share it once; gifts, RSVPs, and good wishes find their way home. After the day, the page becomes a keepsake.

---

## 2. Goals, non-goals & success metrics

### 2.1 Goals (MVP)

1. Let a host publish a celebration page and collect money **safely and legally** within minutes.
2. Let any guest gift, contribute, RSVP, reserve, or wish **without creating an account**.
3. Make the money side **trustworthy**: identity verification, clear fees, a real wallet, and clean withdrawals.
4. Protect the guest experience even at the system's edges (e.g. a host who has hit the receiving cap).

### 2.2 Non-goals (explicitly out of MVP)

- Co-hosts / multiple owners per page (v1 is single-owner).
- Multi-currency / diaspora settlement rails (designed for, not built).
- Premium cosmetic upsell (custom themes, custom URLs, premium covers).
- Native mobile apps (responsive web only).
- The post-event photo gallery ("Memories") ships as a **fast follow**, not MVP.

### 2.3 Success metrics

> Targets are **proposals to be ratified** with the team against a baseline once instrumentation lands. They are directional, not committed.

| Metric | Definition | Why it matters | Proposed target |
|---|---|---|---|
| **Activation** | % of new hosts who publish an event | First value moment | ≥ 60% |
| **Gift conversion** | % of unique event visitors who complete a gift or contribution | The revenue engine | ≥ 8% |
| **Gift volume (GMV)** | Total kobo confirmed-paid per period | Revenue base (fee is a % of this) | Growth MoM |
| **Verification completion** | % of hosts who complete NIN before withdrawal | Unblocks payout & lifts cap | ≥ 90% of withdrawing hosts |
| **Cap-block leakage** | % of gift attempts blocked by an unverified capped host | A3 friction to minimize | < 2% of attempts |
| **Withdrawal success** | % of withdrawal requests that reach "paid" | Trust in the money rail | ≥ 99% |
| **Time to publish** | Median minutes from signup to first published event | Onboarding friction | < 10 min |

**Guardrail metrics:** chargeback rate, payment-failure rate, support tickets per 100 events, page P75 load time on 3G.

---

## 3. Personas

| Persona | Role & needs |
|---|---|
| **Host** | The account owner who creates and runs the page and **owns the funds**. May be the celebrant or someone acting for them (e.g. a parent for a baby shower). v1 is single-owner: no co-hosts; a couple nominates one person. Needs: fast setup, gifts that never stop mid-event, clear money, easy withdrawal. |
| **Guest** | Arrives via a shared link, usually **without an account**. RSVPs, gifts, contributes, reserves, leaves wishes, and later uploads photos. Needs: instant understanding of the page, a frictionless gift, and privacy control. |
| **Platform** | Handles collection and payout, identity verification, and earns the **withdrawal fee**. Needs: to never hold pooled funds it shouldn't, to stay KYC-compliant, and to minimize chargeback exposure. |

---

## 4. Scope & prioritization

| Priority | Meaning |
|---|---|
| **P0** | MVP cannot ship without it. |
| **P1** | Fast follow. |
| **P2** | Designed for, not built yet. |

| Priority | Scope |
|---|---|
| **v1 (P0)** | Auth + KYC cap; event create/edit; event page + RSVP; physical wishlist with reserve-lock; direct monetary gifts and goal contributions with two-axis privacy; wishes with moderation; wallet and withdrawal; dashboard; settings; free unlimited events. |
| **Fast follow (P1)** | Post-event memory gallery; table assignments. |
| **Later (P2)** | Premium cosmetic upsell; co-hosts and roles; multi-currency / diaspora. |

**MVP screen inventory (~18 core screens):**

- **Public:** Landing, How fees work, Login, Sign up
- **Host:** Dashboard, Create/Edit Event, Event Detail (RSVP + modules), Wishlist, Contributions, Wishes, Wallet, Settings
- **Guest:** Event Page, Gift Reservation, Direct Gift checkout, Contribution checkout, Wish submission
- **Shared:** Identity verification flow

---

## 5. Functional requirements

Requirements are grouped by epic. Each story is *"As a [user], I want [capability] so that [benefit]"* with acceptance criteria. IDs (A1, E1…) are stable references for design, engineering, and QA.

### Epic A — Account & Identity

**A1 [P0] — Host signup.** *As a host, I want to sign up with email and phone so I can create an event.*
- Email and phone are captured and verified by OTP **before** an event can collect money.
- Standard login, logout, and forgot-password flows.

**A2 [P0] — Verify before the cap.** *As a host, I want to verify my identity before my page outgrows the receiving cap, so gifts never stop mid-event.*
- Cumulative amount received is tracked per host (see §6).
- At cumulative **₦100,000**, new incoming gifts are blocked until NIN verification completes; **the single gift that crosses the line is allowed to finish.**
- NIN verification is instant and automated; on success the cap lifts permanently.
- Verification is nudged at signup and again near **₦70,000**, and can be done voluntarily at any time.
- The lock is **money-only** — RSVP, wishes, and wishlist claims keep working while funds are paused.

**A3 [P0] — Graceful capped state for guests.** *As a guest, when a host has hit their cap, I want a clear, non-broken message so I don't think the page failed.*
- The guest sees a friendly "gift box is full for now" state, can **still leave a wish**, and the host is notified that gifts are waiting.
- The friction is the **host's** to clear, never the guest's.

### Epic B — Event Creation & Management (Host)

**B1 [P0] — Create an event.** *As a host, I want to create an event with all its details so guests have one place to celebrate me.*
- Fields: title, description, cover image, date, time, venue (optional), dress code (optional), occasion type.
- Host chooses which modules the page shows: wishlist, monetary gifts, contribution goals, wishes.
- The event gets a shareable link on publish.

**B2 [P0] — Edit or unpublish.** *As a host, I want to edit or unpublish an event so I can fix details or take it down.*
- Any field is editable after publish; changes reflect on the live page.
- Unpublishing hides the page **without destroying** received gifts or data.

**B3 [P1] — Table assignments.** *As a host, I want to assign tables so attending guests know where to sit.*
- Optional table list; assigned guests see their table after they RSVP "attending".

### Epic C — The Event Page & RSVP (Guest)

**C1 [P0] — Land and understand instantly.** *As a guest, I want to land directly on the event page and understand it instantly.*
- The page shows the cover, the details, and whichever modules the host enabled.
- **No guest account required.**
- The host controls visibility: public link vs invite-only.

**C2 [P0] — RSVP.** *As a guest, I want to RSVP so the host knows I'm coming.*
- Options: attending, maybe, not attending.
- If attending and a table is assigned, the table number is shown.

**C3 [P0] — RSVP overview (host).** *As a host, I want to see RSVPs grouped by status with a running headcount.*

### Epic D — Gifting: Physical Wishlist

**D1 [P0] — Build a wishlist.** *As a host, I want to build a wishlist so guests know what I'd love.*
- Item fields: name, image, description, optional external link.
- Item status: available or reserved.

**D2 [P0] — Reserve an item.** *As a guest, I want to reserve a wishlist item so two of us don't buy the same thing.*
- Reserving **locks** the item to "reserved" for everyone else (must be race-safe — see §10).
- The guest chooses whether their name shows next to the reservation.
- The guest can release a reservation if plans change.

### Epic E — Gifting: Monetary

**E1 [P0] — Send a money gift.** *As a guest, I want to send a money gift directly to the celebrant, with or without a goal.*
- Enter amount, optional message, and pay.
- **Two independent controls:** show my name / stay anonymous, and show the amount / hide it.
- The card fee is added **on top** at checkout and paid by the giver, so the celebrant receives the clean amount (mandatory for international cards, which have no fee cap).

**E2 [P0] — Contribution goals.** *As a host, I want to create contribution goals so guests can rally toward something specific.*
- Goal fields: name, target amount, progress.
- The progress bar is **motivational** — funds reach the host regardless of whether the goal is met.

**E3 [P0] — Contribute to a goal.** *As a guest, I want to contribute to a goal with a message, the same way I'd send a direct gift.*
- Select goal, enter amount, add an optional message, apply the same privacy controls, and pay.

### Epic F — Well-Wishes

**F1 [P0] — Leave a wish.** *As a guest, I want to leave a wish so the celebrant hears from me.*
- Message and name; submitted with **no account**.

**F2 [P0] — Reply & moderate.** *As a host, I want to reply to and moderate wishes.*
- Reply inline; hide or delete an abusive wish.

### Epic G — Wallet & Withdrawal

**G1 [P0] — Wallet.** *As a host, I want a wallet showing what I've received and what I can take out.*
- **Available** balance (cleared), **pending** balance (in the settlement and chargeback window), and withdrawal history.

**G2 [P0] — Withdraw.** *As a host, I want to withdraw to my bank.*
- Withdraw to a verified bank account.
- The platform withdrawal fee is applied (model under Open Decision D1, §7).
- A **minimum withdrawal (₦2,000)** prevents fee-stacking on tiny pulls; the interface nudges a single post-event withdrawal.
- Withdrawal is blocked until identity is verified where required.

### Epic H — Dashboard

**H1 [P0] — Activity dashboard.** *As a host, I want a dashboard summarising my activity.*
- Totals: events, gifts (count and value), contributions, and wishes.
- Quick links into each event.

### Epic I — Settings, Notifications & Privacy

- **I1 [P0]** Profile settings (name, photo, bank details).
- **I2 [P0]** Notification settings — new gift, new wish, contribution milestone, approaching receiving cap, and withdrawal status.
- **I3 [P0]** Privacy controls — page visibility, default amount visibility, and who can see contributor names.

### Epic J — Memories (Fast Follow)

- **J1 [P1]** Guest uploads photos after the event so the day lives in one place.
- **J2 [P1]** Host moderates the gallery (remove a photo).
- *Rationale:* it's what stops the page going dead the day after — but the core money and wish loop ships without it.

### Epic K — Monetization & Plans

- **K1 [P0]** Event creation is free and unlimited; revenue is the **withdrawal fee only**. This maximises gift volume, which is the revenue base.
- **K2 [P2]** Premium cosmetic upsell — custom themes, custom URL, premium covers — once there is volume to upsell into.

---

## 6. KYC & verification tiers

| Tier | Trigger | Requirement | Effect |
|---|---|---|---|
| **0** | Account creation | Phone + email verification | Can create pages and receive up to a cumulative **₦100,000** |
| **1** | Cumulative received reaches ₦100,000 | NIN verification (instant, automated) | Incoming funds unblocked; withdrawal enabled above the threshold |

The threshold is measured on **cumulative received, not per gift** — so ₦500,000 arriving as ten ₦50,000 gifts still triggers it. The transaction that crosses the line completes; the lock applies afterward.

**Key constants** (implemented in `config/constants.py`): cap `₦100,000`; verification nudge `₦70,000`; minimum withdrawal `₦2,000`.

---

## 7. Monetization & fees

Creating a page and collecting gifts is free and unlimited. Money is touched in exactly two places, both disclosed up front:

1. **Card fee — paid by the giver.** Added on top at checkout so the celebrant receives the clean amount.
2. **Withdrawal fee — the platform's only revenue.** Model is **Open Decision D1**; the system is built **fee-agnostic** so it can be switched with a one-line change (`wallet/fees.py`, `ACTIVE_FEE`).

| Model | Fee on ₦50,000 | Fee on ₦500,000 | Trade-off |
|---|---|---|---|
| **Flat (₦1k / ₦2k)** | ₦1,000 | ₦2,000 | Predictable and host-friendly; caps platform upside. |
| **Percentage (1%)** | ₦500 | ₦5,000 | Scales revenue with gift volume; takes more from large celebrants. |

---

## 8. Money-handling principles

- **All money is stored as integer kobo** (1 naira = 100 kobo). No floats, ever. Display formatting is a presentation concern only.
- A gift becomes "real" **only** on gateway-confirmed payment (webhook), at which point: the host's cumulative-received increments, the wallet's *pending* balance is credited, and any linked goal's raised total bumps. This is the single place money is recognized.
- Funds sit in **pending** through the settlement/chargeback window, then move to **available** on settlement confirmation — protecting against early withdrawal of reversible money.
- **Custody (Open Decision D2):** the wallet represents the host's *view* of their balance. The intended production design routes settlement through the gateway (sub-accounts / dedicated virtual accounts) so the platform never legally holds pooled funds.

---

## 9. Architecture & technical requirements

- **Backend:** Django + DRF, API-first. External services (payments, identity) sit behind **interfaces** (`integrations/gateways.py`) and ship as stubs so the system runs and is testable without live keys. Real providers drop in without touching domain code.
- **Frontend:** Next.js (App Router) consuming the DRF API. Guest/public pages are **server-rendered** so a shared link produces a real link preview (Open Graph) for WhatsApp/Instagram/iMessage — the sharing loop is the primary acquisition channel and depends on this. Host dashboard is a client app.
- **Payments:** Paystack (replace `StubPaymentGateway` → `PaystackGateway`). Webhook must **verify the signature** before confirming a gift (see §10).
- **Identity/NIN:** a provider such as Dojah (replace `StubIdentityVerifier` → `DojahVerifier`).
- **Idempotency:** payment confirmation must be idempotent (webhooks retry); confirming an already-paid gift is a no-op.

---

## 10. Non-functional requirements

**Security & integrity**
- Payment webhooks **must** verify the gateway signature before acting. *(Known gap: currently a TODO in `integrations/views.py`.)*
- Wishlist reservation must be **race-safe** — concurrent reserves on the same item must not both succeed. *(Known gap: `ReserveView` check-then-set has no row lock.)*
- No secrets in the client bundle; bank details and PII encrypted at rest.
- Rate-limit guest actions (gift init, RSVP, wish) to deter abuse.

**Privacy**
- Two-axis gift privacy (name, amount) honored on every public surface and in every payload the API returns.
- Host privacy controls (page visibility, default amount visibility, contributor-name visibility) enforced server-side, not just hidden in UI.

**Performance & reliability**
- Guest event page P75 load < 2.5s on a 3G-class mobile connection.
- Money operations are transactional; a failed step never leaves a wallet or ledger half-updated.
- Double-entry ledger for every balance change (audit trail).

**Accessibility**
- WCAG 2.1 AA: color contrast, focus states, keyboard paths, and touch-target sizing across guest and host flows.

**Compliance**
- KYC tiers enforced as in §6; withdrawal gated on verification; auditable record of verification events.

---

## 11. Analytics & instrumentation

Minimum events to land with MVP (names indicative):

`host_signed_up`, `otp_verified`, `event_published`, `event_page_viewed`, `rsvp_submitted`, `gift_initiated`, `gift_paid`, `gift_blocked_capped`, `goal_contribution_paid`, `wishlist_item_reserved`, `wish_left`, `nudge_verification_shown`, `nin_verification_started`, `nin_verification_succeeded`, `withdrawal_requested`, `withdrawal_paid`.

Each money event carries `amount_kobo`, `event_id`, and privacy flags where relevant. These directly feed the §2.3 metrics.

---

## 12. Dependencies & integrations

| Dependency | Purpose | Status |
|---|---|---|
| Paystack | Card collection, settlement, payout | Interface stubbed; live integration pending keys |
| NIN provider (e.g. Dojah) | Identity verification | Interface stubbed; live integration pending |
| SMS/Email OTP provider | Phone + email verification (A1) | To be selected |
| Object storage | Cover images, wishlist images, memory photos (P1) | To be selected |

---

## 13. Open decisions

| ID | Decision | Status | Recommendation |
|---|---|---|---|
| **D1** | Fee model: flat (₦1k/₦2k) vs 1%. Blocks the wallet build's final numbers. | Open | Ship **flat** for launch (predictable, host-friendly); revisit once GMV data exists. System is fee-agnostic, so switching is one line. |
| **D2** | Custody: where the pooled balance legally sits before withdrawal. | Open | Route settlement through the gateway (sub-accounts) so the platform never holds pooled funds. |
| **D3** | Chargebacks: who absorbs a reversed gift after the host has withdrawn. | Open | Needs a policy before enabling withdrawal of recently-settled funds; the pending window mitigates but does not eliminate. |
| **D4** | Anchor occasion for launch and marketing (build is occasion-agnostic). | Open | Pick one occasion to concentrate early demand; recommend weddings (highest gift sizes). |
| **D5** | Plan gating: free unlimited events vs a 1-event free cap. | Open | **Free unlimited** (recommended) — maximises gift volume, which is the revenue base. |

---

## 14. Risks & mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Guest sees a "broken" page when a host is capped | Lost gifts, brand damage | A3 graceful state; host notified; wish still works |
| Webhook spoofing / unverified confirmations | Fraudulent "paid" gifts | Mandatory signature verification before confirm (§10) |
| Double-reservation of a wishlist item | Two guests buy the same gift | Row-level lock on reserve (§10) |
| Chargeback after withdrawal | Platform absorbs loss | Pending window + D3 policy |
| Verification friction stalls withdrawals | Hosts can't get paid; churn | Instant automated NIN; nudges at ₦70k and signup |
| Low gift conversion | Weak revenue base | Fast, low-friction checkout; social proof; occasion focus (D4) |

---

## 15. Release plan (indicative)

1. **M1 — Money spine:** auth + OTP, event create/publish, guest event page, direct gift with confirm/webhook, wallet pending→available, KYC cap + NIN, withdrawal. *(This is the demonstrable end-to-end loop.)*
2. **M2 — Full guest surface:** goals + contributions, wishlist + reserve-lock, wishes + moderation, RSVP overview, dashboard, settings, notifications, privacy controls.
3. **M3 — Harden & launch:** signature verification, race-safety, rate limits, accessibility pass, analytics, live Paystack + NIN keys.
4. **Fast follow (P1):** memory gallery, table assignments.

---

## Appendix — Glossary

- **Kobo** — smallest naira unit (1 naira = 100 kobo); the storage unit for all money.
- **Cumulative received** — running total a host has ever received; drives the KYC cap.
- **Crossing gift** — the single gift that takes a host from under the cap to over it; allowed to complete.
- **Pending / available** — money in the settlement window vs cleared money withdrawable now.
- **Motivational goal** — a target with a progress bar; funds reach the host whether or not it's met.
- **Reserve-lock** — the mechanism that marks a wishlist item claimed so no one buys it twice.
- **The seal** — the product's signature trust/celebration mark (design-system element).

---

*Derived from Avela User Stories v0.2 and the implemented core-domain backend. Targets and open decisions marked as such are proposals for team ratification, not commitments.*
