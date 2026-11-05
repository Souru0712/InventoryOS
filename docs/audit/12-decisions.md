# 12 — Decisions requiring founder approval

Each has a recommendation; none is started until you answer. **Status 2026-11-04:** D3 → C-35 and
D6 → C-37 in [PRODUCT_DECISIONS](../product/PRODUCT_DECISIONS.md); the rest are mapped in its §7.

| ID | Decision | Conflict found | Recommendation | Blocks |
|---|---|---|---|---|
| D1 | **Pricing: one plan or two?** | Brief: one plan, $49/mo, all features, one location. Code (`app/entitlements.py:49`), pricing page and `NEEDS-YOU.md` §6: Starter $29 / Pro $59, 30-day Pro trial, $149 setup | One plan at $49, 30-day trial, keep $149 done-for-you setup as an optional one-off | B22, Stripe products |
| D2 | **First segment** | Brief: juice bars and cafés first. Earlier notes: cafés, restaurants, bakeries, retail, specialty food, multi-location | Juice bars + cafés. Makes recipes and daily produce receiving (C2) the top product priority | Copy, onboarding, B13 |
| D3 | **Adopt the append-only ledger** (major architectural change) | — | Yes, as designed in [04](04-database-and-ledger.md#recommended-ledger-design-r1-needs-approval-d3); additive migration, nothing dropped | B09–B12, B15 |
| D4 | **Order-level sales ingestion** replacing daily aggregates as the consumption source | — | Yes; keep `sales_daily` for reports | B12 |
| D5 | **Roles in MVP** | Brief lists Owner / Manager / Employee | Owner + Staff only; Manager later | B17 |
| D6 | **Snack-box module** (private, ~2,700 lines, widens Square OAuth scopes for every customer) | — | Move to its own repo and Square app before the first customer connects | B20, Square app review |
| D7 | **Multi-location under one plan** | One plan = one location; Square merchants often have several | One location included; others visible but not tracked; add-on later. Fix C3 anyway inside B12 | B22 |
| D8 | **Host** | — | Render (web + scheduler + managed Postgres with PITR) — simplest for one person. Fly.io is the alternative | B26 |
| D9 | **Email provider** | Brief: Resend. `NEEDS-YOU.md`: Postmark | Resend (in your list, free tier covers 21 customers); revisit only if deliverability suffers | B24 |
| D10 | **Data retention after cancellation** | None defined | Read-only 90 days, then deletion; export available throughout; deletion on request within 30 days | B19, privacy page |
| D11 | **Refunds and stock** | — | MVP: refunds never change stock; record them for sales accuracy post-launch; operator decides restock vs waste per item | B12, post-launch |
| D12 | **AI spend controls** | No cap exists | Extraction only after email verification; 50 invoice/date reads per tenant per day; monthly spend alert on the Anthropic key | B25 |
| D13 | **Credentials / accounts you must create** | — | GitHub (private), Stripe test mode, email provider + DNS, host, R2, Sentry, Square sandbox redirect + webhook (`NEEDS-YOU.md` §1–6) | Phases 0, 3, 4 |
