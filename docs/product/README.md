# InventoryOS — Product Blueprint (Phase 1)

The internal source of truth for **InventoryOS**, a commercial, multi-tenant inventory and
operations management platform for independent food-service businesses. Written 2026-11-04 for
humans and AI development agents. Phase 1 is product definition only: no application code, schema
or existing documentation was changed.

> **Location.** InventoryOS is the new name for StockPulse (C-24) and is built on this repository's
> codebase (C-25); this folder is the permanent home of its product docs (C-28). The repository
> folder is now `InventoryOS` (formerly `POS-Inventory-Automation`); the code and its branding still
> say StockPulse. Building on this code does **not** mean its current features already meet this
> blueprint — the Phase 2 audit measures the gaps.

## 1. Product overview

**What it is.** An inventory and operations platform that connects every movement of
ingredients, products, supplies and materials to the activity that caused it — purchasing,
receiving, production, sales, waste — so owners know what they have, what they should have, what it
cost and where money is lost. Not a stock-counting app, and not a POS at launch.

**Three separate entities**

| Entity | What it is | Role |
|---|---|---|
| **InventoryOS** | The product defined here — the new name for StockPulse (C-24) | Built on the InventoryOS codebase (C-25); no InventoryOS release yet |
| **InventoryOS codebase (formerly StockPulse)** | `C:\Users\oscar\.vscode\InventoryOS` (renamed from `POS-Inventory-Automation`) — Python inventory app with Square integration; still branded StockPulse in code | Chosen foundation (C-25); current state documented in the [technical audit](../audit/00-INDEX.md); gaps measured in Phase 2 |
| **JuJu Juice** | `C:\Users\oscar\.vscode\JuJu` — separate juice business with its own customer site and staff/kitchen platform | Real-world reference and demonstration environment; keeps its own brand, experience and requirements |

**Three customer scenarios and operating modes** — one engine; only the source of sales data
differs ([PRODUCT_VISION §5.3](PRODUCT_VISION.md#53-customer-types-by-pos-situation-confirmed)).

| Scenario | Offering | Operating mode | Pricing hypothesis | Status |
|---|---|---|---|---|
| A — has a POS, keeps it | InventoryOS Complete | Connected (Square as first adapter, not the foundation) | $99 / location / month | Launch |
| B — no POS, doesn't want one | InventoryOS Standalone | Standalone (daily sales entry, imports, operational records) | $99 / month, one location | Launch |
| C — no POS, wants one | InventoryOS POS | Native POS | $149–199 / month starting + pass-through costs | Future — validation required |
| Expansion | Multi-location | Any mode | ~$149–199+ / month | Expansion |
| Expansion | Advanced | Any mode | ~$199–299+ / month | Expansion |

Prices are **hypotheses, not published prices**, held in billing configuration, never in domain
logic. No customer validation has been done yet.

## 2. Documents

| Document | Purpose |
|---|---|
| [PRODUCT_VISION](PRODUCT_VISION.md) | Vision, positioning, value proposition, problems, segments and journeys, differentiation, outcomes, boundaries |
| [PRICING_AND_PACKAGING](PRICING_AND_PACKAGING.md) | Scenarios A–C, pricing dimensions, packaging matrix, entitlement principles (E-01..10), validation questions |
| [PRODUCT_SPECIFICATION](PRODUCT_SPECIFICATION.md) | Operating modes; 15 modules with requirements (`<MOD>-FR-nn`) and release tags; UX and application structure; metric definitions; release definition, roadmap boundaries, prioritization matrix |
| [BUSINESS_WORKFLOWS](BUSINESS_WORKFLOWS.md) | 24 end-to-end workflows + 4 supporting (`WF-01..28`): trigger, actor, steps, system behaviour, ledger effects, records, notifications, exceptions, audit, outcome |
| [DOMAIN_MODEL](DOMAIN_MODEL.md) | Entities, relationships, ownership, lifecycle, stock quantity definitions, exceptions, **integrity rules DR-01..36** |
| [USER_ROLES_AND_PERMISSIONS](USER_ROLES_AND_PERMISSIONS.md) | Eight roles, permission matrix, location-specific access, approvals |
| [SAAS_REQUIREMENTS](SAAS_REQUIREMENTS.md) | Multi-tenancy, authentication, account lifecycle, billing and entitlements, onboarding, export/deletion, tenant administration |
| [JUJU_REFERENCE_AND_PARITY](JUJU_REFERENCE_AND_PARITY.md) | JuJu's actual workflows, capability matrices, shared vs JuJu-specific, relationship options, repository boundaries |
| [INTEGRATION_REQUIREMENTS](INTEGRATION_REQUIREMENTS.md) | Provider abstraction, Square as first candidate, sync and idempotency, Standalone data-entry alternatives, native POS boundary |
| [NON_FUNCTIONAL_REQUIREMENTS](NON_FUNCTIONAL_REQUIREMENTS.md) | Security, reliability, performance, scalability, accessibility, maintainability, observability, risks, production readiness standard |
| [PRODUCT_DECISIONS](PRODUCT_DECISIONS.md) | Decisions with rationale and alternatives (C-01..23), adopted recommendations, **open decisions PD-01..42**, assumptions A-01..14, contradictions |
| [PRODUCT_GLOSSARY](PRODUCT_GLOSSARY.md) | Terminology for UI, code and docs |
| [PRODUCT_ACCEPTANCE_CRITERIA](PRODUCT_ACCEPTANCE_CRITERIA.md) | Testable criteria (`AC-<MOD>-nn`), workflow-level success conditions, business-level requirements |

## 3. Reading order

1. This README → 2. [PRODUCT_DECISIONS](PRODUCT_DECISIONS.md) (what is settled and open) →
3. [PRODUCT_VISION](PRODUCT_VISION.md) → 4. [PRICING_AND_PACKAGING](PRICING_AND_PACKAGING.md) →
5. [PRODUCT_SPECIFICATION](PRODUCT_SPECIFICATION.md) → 6. [BUSINESS_WORKFLOWS](BUSINESS_WORKFLOWS.md) →
7. [DOMAIN_MODEL](DOMAIN_MODEL.md) → 8. the remaining documents as needed →
9. [PRODUCT_ACCEPTANCE_CRITERIA](PRODUCT_ACCEPTANCE_CRITERIA.md) before calling anything done.

## 4. Document relationships

```
                    PRODUCT_DECISIONS (overrides all until updated)
                               │
        PRODUCT_VISION ──► PRICING_AND_PACKAGING
               │                    │ entitlements
               ▼                    ▼
        PRODUCT_SPECIFICATION ◄── SAAS_REQUIREMENTS
         │      │        │
         │      │        └──► USER_ROLES_AND_PERMISSIONS
         │      ▼
         │  BUSINESS_WORKFLOWS ──► DOMAIN_MODEL (integrity rules)
         │                              ▲
         ├──► INTEGRATION_REQUIREMENTS ─┘
         ├──► NON_FUNCTIONAL_REQUIREMENTS
         └──► PRODUCT_ACCEPTANCE_CRITERIA
   JUJU_REFERENCE_AND_PARITY informs SPECIFICATION and DOMAIN_MODEL (reference only)
   PRODUCT_GLOSSARY defines every term used above
```

| Question | Authoritative document |
|---|---|
| Decided / assumed / open; which wins in a conflict | PRODUCT_DECISIONS |
| Who the product is for; positioning | PRODUCT_VISION |
| Offerings, prices, entitlements | PRICING_AND_PACKAGING |
| What a module must do; what ships when | PRODUCT_SPECIFICATION |
| How a process flows and what it does to stock | BUSINESS_WORKFLOWS |
| Entity meaning, invariants | DOMAIN_MODEL |
| Who may do what, where | USER_ROLES_AND_PERMISSIONS |
| Tenancy, auth, billing lifecycle, onboarding | SAAS_REQUIREMENTS |
| JuJu facts and relationship | JUJU_REFERENCE_AND_PARITY (JuJu's repo is the source of truth for JuJu code) |
| External systems and data entry without a POS | INTEGRATION_REQUIREMENTS |
| Quality bars, risks, launch gate | NON_FUNCTIONAL_REQUIREMENTS |
| Terms | PRODUCT_GLOSSARY |
| Done-ness | PRODUCT_ACCEPTANCE_CRITERIA |
| Facts about the existing app's code | [../audit/](../audit/00-INDEX.md) — evidence, not requirements |

Precedence when documents disagree: **PRODUCT_DECISIONS → DOMAIN_MODEL (integrity rules) →
PRICING_AND_PACKAGING → PRODUCT_SPECIFICATION → BUSINESS_WORKFLOWS → others → PRODUCT_VISION**.
Fix the lower document in the same change.

## 5. Conventions

- Tags: **[CONFIRMED]** owner · **[PROPOSED]** · **[RECOMMENDED]** · **[ASSUMED]** · **[OPEN]** ·
  **[FUTURE]**. Hypotheses are never presented as facts.
- Release tags: `R1` launch · `R1.1` first increment · `R2` later core · `Expansion` · `Advanced` ·
  `Future`.
- Requirement sources: `B` brief · `R` recommendation · `J` JuJu precedent · `S` existing-app
  precedent · `O` prior owner decision.
- IDs are stable; deprecate with a note rather than renumbering.

## 6. Key assumptions and open questions

Full lists: [PRODUCT_DECISIONS §3–5](PRODUCT_DECISIONS.md#3-open-decisions).

| Most important open decisions | ID |
|---|---|
| Native POS validation criteria | PD-35 |
| Multi-location pricing vs Complete's per-location price | PD-33 |

Decided 2026-11-04: InventoryOS is the new name for StockPulse (C-24); it is built on this codebase
(C-25); the target is 21 paying customers with $1,000 MRR gating new products (C-26); the build
order is engine → Standalone → Square (C-27); the release plan is approved (C-29); one configurable
product serves every target segment with each business's data separated (C-30); login must include
verification, reset and throttling (C-31), built by finishing the in-app login (C-32). After the Phase 2 audit: scoped data access for tenant
isolation (C-33); the ledger owns all stock (C-34) and replaces the old engine (C-35); InventoryOS owns
the product catalog (C-36); the snack-box module leaves (C-37); items are per organization (C-38);
migration history kept (C-39); no pilots on the old engine (C-40); Starter/Pro retired (C-41); JuJu
parity by specification until R1 (C-42).

| Key assumptions | ID |
|---|---|
| A meaningful share of customers use Square; another share has no POS | A-01 |
| Standalone customers can produce a daily item-level sales figure | A-13 |
| Segment profiles reflect real businesses (no interviews yet) | A-14 |
| Square production OAuth works for other sellers without marketplace listing | A-10 |

## 7. Phase status

| Phase | Scope | Status |
|---|---|---|
| 1 | Product blueprint (this folder) | Complete — awaiting owner review |
| 2 | Audit of the chosen codebase against this blueprint: all three operating modes without unnecessary Square dependency, ledger, tenancy and location scope, entitlements, Square live readiness; sizes the in-app login work (C-32); led to C-33..C-42 | Complete 2026-11-04 — [audit](../audit/README.md); decisions C-33..C-42 |
| 3 | Gap analysis and roadmap: Complete and Standalone first; native POS evaluated separately; then multi-location and Advanced | Not started |

## 8. Rules for AI agents

1. Read this README, then PRODUCT_DECISIONS, then the relevant module in PRODUCT_SPECIFICATION.
2. This codebase is InventoryOS's foundation (C-25), but never assume its current features meet
   this blueprint — check the Phase 2 audit. JuJu is never InventoryOS.
3. Do not implement anything marked OPEN, and do not hard-code prices.
4. Every inventory change goes through the ledger rules in [DOMAIN_MODEL §7](DOMAIN_MODEL.md#7-data-integrity-rules).
5. Square is the first adapter: no POS-specific logic outside adapters; every workflow must work in
   Standalone mode with no POS configured.
6. The native POS is never a launch dependency.
7. A feature is complete only when its criteria in PRODUCT_ACCEPTANCE_CRITERIA pass.
