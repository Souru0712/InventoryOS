# Customer Validation Plan

Status: PROPOSED (Phase 3) · Drafted 2026-11-05 · Owner decisions needed are marked **[OWNER]**
Addresses: **TR-31** (no pilots exist; validation must start now, in parallel with engineering)
Sources: `docs/product/PRICING_AND_PACKAGING.md` (§3, §8), `PRODUCT_VISION.md` (§4–6, §9),
`PRODUCT_DECISIONS.md` (A-01..A-14, C-26, PD-33..PD-36), `SAAS_REQUIREMENTS.md` §5.

**Fact base (2026-11-04, owner): no customer validation has been done. No pilots exist.** Every
price, segment profile, workflow, saving and threshold in this plan is a hypothesis or a proposal.
Nothing here is a result.

---

## 1. Purpose and principles

Purpose: replace assumptions (A-xx) with evidence before the money-and-scope decisions they
gate: G-R1 (first paying customers), multi-location (PD-33), Advanced (PD-34), native POS (PD-35).

| # | Principle |
|---|---|
| P-1 | **Assumption vs evidence.** A statement is an assumption until a dated entry in the evidence log (§8) supports it. Docs say "assumed" until then. |
| P-2 | **No fabricated results.** No invented interviews, quotes, survey numbers, willingness-to-pay, market sizes or competitor prices. Competitor facts are researched and sourced before use (Vision §7). |
| P-3 | **The evidence log is the only place results go.** Other docs link to log entries; they do not restate unlogged findings. |
| P-4 | **Behaviour over opinion.** Rank evidence: paid / signed commitment > time or data given (diary, spreadsheet shared, pilot used) > stated intent > compliments. Stated price reactions are directional only. |
| P-5 | **Disconfirm on purpose.** Each hypothesis has a fail threshold; failing is a valid, useful result. |
| P-6 | **Findings never silently change scope.** They become *proposed decisions* for the owner (§8.3). |
| P-7 | **Small and cheap first.** Solo founder, non-developer: conversations and spreadsheets before software. Do not wait for M4/M5 to start. |
| P-8 | **Do not sell what does not exist.** Pre-M4 outreach asks about problems; offers are labelled "planned". No fake demos or fake availability. |
| P-9 | Toast (owner's own store) is out of scope as an example. "Which POS?" is asked of everyone; Toast/Clover/others appear only as adapter-demand data (H-07). |

Sample sizes and thresholds below are **proposed**, chosen for decision usefulness not statistical
power. With small samples, report counts ("6 of 9"), never percentages as findings.

---

## 2. Segments to validate (prioritized)

Priority follows Vision §5.1 (independent single-location owner-operators) and §5.2.

| Rank | Segment (Vision §5.2) | Why first / later | Target interviews (proposed) | POS mix to seek |
|---|---|---|---|---|
| 1 | Independent juice bars (#1) | Highest perishability, essential production/expiry, strongest $99 case (hypothesis) | 8 | 4 Square-or-other POS, 3 none, 1 wants one |
| 2 | Smoothie shops (#2) and bubble tea / specialty (#5) | Modifier complexity, hour-level shelf life (PD-40) | 6 (3 + 3) | Mostly POS |
| 3 | Cafés (#3) and coffee shops (#4) | Larger pool, milk/pastry waste, modifier costing | 6 (3 + 3) | Mostly POS |
| 4 | Small food-service, no-POS (#6): market stalls, caterers, kiosks | Likely Standalone core (Journey B) | 4 | Mostly none |
| 5 | Multi-location, 2–10 stores (#7) | Needed only for PD-33; ask existing-interview owners who have or plan a 2nd site | 4 | Any |
| 6 | Sophisticated operators (#8) | Advanced (PD-34); exploratory only | 2–3 | Any |

Total proposed: ~30 problem interviews over ~8–10 weeks. Stop a segment early when the last 3
interviews add no new information (log "saturation" with the three entry IDs).

### 2.1 Finding prospects (solo founder, NYC; practical, no statistics assumed)

| Channel | How | Notes |
|---|---|---|
| Walk-in, off-peak | Visit independent shops 2–4 pm weekdays; buy something; ask the owner/manager for 30 min at a later time | Best response for owner-operators; never interrupt service |
| Personal network | Ask friends/family/the convenience-store contacts for introductions to any beverage or food-service owner | Warm intros first; record relationship in log (bias) |
| Supplier touchpoints | Produce/dairy/cup-and-packaging suppliers' counters; farmers' markets and market-vendor associations | Reaches no-POS vendors (segment 4) |
| Local communities | NYC small-business groups, food-vendor associations, relevant local online groups | Read each group's rules; ask for conversations, not sales; no spam |
| Trade/chamber/BID events | Neighborhood business improvement districts, chambers, food-business workshops | Mostly free to attend |
| JuJu | The owner's own JuJu operations contacts, if applicable **[OWNER]** | Clearly separate from InventoryOS evidence |
| Referral | End every interview: "Who else runs a shop like yours that I should talk to?" | Cheapest channel once started |
| Later: POS app marketplaces | Square App Marketplace listing (A-10 dependency) and similar, after M5 | Not available pre-launch; do not plan around it |
| Later: landing page traffic | Experiment E-5 | Only with the same community links |

Incentive (proposed, **[OWNER]** to approve budget): a coffee/juice purchase or a small gift card
per interview, disclosed up front, never conditional on answers. Keep a prospect sheet (business
name, segment, POS, status, consent flag) outside the repo (see §10).

---

## 3. Hypotheses register

Thresholds are **PROPOSED**. "n" = qualified participants (owner/manager of a matching business).
Pass = threshold met; Fail = clearly missed; between = inconclusive, extend sample or reframe.

| ID | Hypothesis | Linked | Risk if wrong | How to test | Pass / Fail (proposed) |
|---|---|---|---|---|---|
| H-01 | Independent juice/smoothie/café owners feel waste, stockouts or unknown drink cost as a real, recurring problem (Vision §4) | A-14 | Product solves a non-problem | E-1 problem interviews | Pass: ≥ 60 % of n ≥ 15 name ≥ 2 of pains 1–6 unprompted · Fail: < 30 % |
| H-02 | Owners currently track stock with spreadsheets/paper/nothing, and the effort is a pain | Vision §5.1 | No wedge vs status quo | E-1, E-3 | Pass: ≥ 60 % use manual/none and report effort · Fail: most already use a satisfying tool |
| H-03 | Owners understand "ingredients deducted from sales via recipes" and see value without a long explanation | Vision §6 | Positioning fails | E-1 explanation test; E-4 | Pass: ≥ 60 % restate value correctly after a 60-second explanation |
| H-04 | Measurable waste/variance exists that is large enough to justify $99/location/month | PD-31/C-26, A-14 | Price not justified | E-2 diary; E-1 quantification | Pass: for ≥ 50 % of diary participants, documented avoidable loss ≥ 3× the plan price per month (3× is the proposed rule of thumb) · Fail: < 1× for most |
| H-05 | A meaningful share of target shops use Square | A-01 | Square-first adapter order wrong | "Which POS?" in all interviews, E-4, E-5 | Pass: Square is the most-named POS among POS users · Fail: another POS ≥ 2× Square |
| H-06 | Owners will connect POS data (OAuth) to a new inventory tool | A-10 | Complete onboarding stalls | E-8 Square pilot | Pass: ≥ 70 % of invited pilots complete connection · Fail: < 40 % |
| H-07 | Demand exists for POS adapters beyond Square (Toast, Clover, other) | A-01 | Adapter roadmap misprioritized | POS tally from E-1/E-4/E-5 | Output: ranked count table; adapter proposed after Square only if ≥ 5 qualified prospects name the same POS and say it blocks adoption |
| H-08 | Businesses without a POS exist and prefer to stay without one (Scenario B) | A-01, A-13 | Standalone not a real offering | E-1, E-4 selection answers | Pass: ≥ 20 % of qualified prospects (n ≥ 25) are no-POS and ≥ half of them don't want one · Fail: < 10 % |
| H-09 | No-POS businesses can produce a daily item-level sales figure | A-13 | Standalone theory-vs-actual useless | E-1 show-me; E-7 | Pass: ≥ 70 % of no-POS participants can show it (terminal report, tally, register) |
| H-10 | Standalone is valuable without transaction ingestion (count-based usage, waste, recipes, expiry, receiving) | A-13, PD-36 | Standalone cannot hold $99 | E-3 concierge; E-7 pilot | Pass: ≥ 60 % of Standalone pilots still use it weekly at week 6 and ≥ 50 % say "very disappointed" if removed (Sean Ellis-style question; proposed) |
| H-11 | Daily sales entry/file import is acceptable effort for Standalone users | A-08, A-13 | Standalone data too sparse | E-7 usage logs | Pass: median ≥ 5 of 7 days entered in weeks 2–6 · Fail: < 3 |
| H-12 | $99/location/month is acceptable for Complete | PD-31, PRICING §8 | Revenue model fails | Price questions (§5), E-6, E-8 conversions | Pass: ≥ 60 % of qualified Square pilots convert at $99 (PRICING §8, already proposed there) · Fail: < 30 % |
| H-13 | $99/month is acceptable for Standalone | PRICING §3 | Same | Price questions, E-7 conversion/deposit | Pass: ≥ 50 % of qualified Standalone pilots convert at $99 · Fail: < 25 % |
| H-14 | Reconciliation (ordered/invoiced/received) and expiry/waste are the features that justify price | Vision §4 pains 5–7 | Wrong priorities in R1 | Ranking exercise in E-1; feature value in E-2 | Output: ranked list; top 3 features by participant count feed roadmap proposals |
| H-15 | Invoice photo/paper handling matters and extraction accuracy is tolerable | A-05, PD-25 | RCV effort misallocated | Collect 20–30 real invoices (with consent) in E-1/E-2; manual accuracy check later | Pass: ≥ 70 % of suppliers send paper/PDF (confirms A-05) · extraction accuracy measured separately when built |
| H-16 | Onboarding (connect, items, top-10 recipes, opening count) completes in ≤ 45 minutes assisted | SAAS §5 | Activation fails | E-3 concierge timing; E-7/E-8 onboarding sessions | Pass: ≥ 70 % reach first dashboard with real stock in ≤ 45 min (SAAS §5 target) · Fail: median > 90 min |
| H-17 | Staff will log waste/batches on phones/shared tablet during service | A-09 | Data incomplete | E-2 diary using phone forms; pilots | Pass: ≥ 60 % of logging days have ≥ 1 waste entry by staff, not only owner |
| H-18 | Hour-level shelf life (bubble-tea toppings, prepared items) is common enough to require timestamps | PD-40, A-04 | Expiry design gap | E-1 add-on (segment 5) | Pass/decision: ≥ 3 of 6 bubble-tea/specialty owners say toppings expire in hours and are discarded on a timer |
| H-19 | A meaningful group of no-POS-but-want-one prospects (Scenario C) exists | PD-36, PD-35 | Native POS not viable | "Would you want a POS" in screener; E-5 plan selection | Count qualified Scenario C prospects; no pass threshold at this stage beyond feeding H-21 |
| H-20 | Scenario C prospects and POS users are dissatisfied enough with current POS to consider switching | PD-35 | Switching barrier too high | POS section of interview (§5) | Pass: ≥ 30 % of POS users name a specific recurring complaint and have looked at alternatives in the last 12 months |
| H-21 | ≥ 10 qualified prospects will pre-commit at $149+ for an InventoryOS POS | PD-35, PRICING §8 | Native POS built without demand | E-9 non-binding LOI then refundable deposit | Pass (PD-35): ≥ 10 qualified prospects pre-commit at ≥ $149/month · Fail: < 5 |
| H-22 | Essential checkout/payment capabilities are scoped narrowly enough to build or white-label | PD-35 | Build cost explodes | Capability checklist (§4.3) in Scenario C interviews | Output: ranked must-have list; decision input for build vs white-label vs partner costing |
| H-23 | An integrated POS differentiates enough to justify $149–199 plus processing fees | PD-35 | Price too high vs POS-native stock features | Price questions + competitor research (sourced) | Pass: Scenario C prospects value integration above "Standalone or Square + Complete" at stated prices |
| H-24 | Multi-location operators exist among the target and need centralized management beyond N × Complete | PD-33, A-14 | Wrong expansion | Segment 5 interviews; asks of paying customers | Qualitative: name the location count where Complete-per-location stops being enough; Pass: ≥ 3 of 4 interviewed multi-location operators identify the same top 3 needs |
| H-25 | Multi-location pricing: operators accept N × $99 up to a point, then expect a package | PD-33, E-06 | Wrong pricing model | Anchored price questions to 2–3 location operators | Pass: ≥ 3 of 4 give a consistent structure; decision input, not a fixed number |
| H-26 | Customers will pay for Advanced capabilities (forecasting, automation, API, custom roles, extended history) | PD-34 | Building unneeded features | Feature requests from paying customers; interviews | Pass (PD-34): ≥ 3 paying customers ask for the same capability |
| H-27 | Forecasting/automated replenishment matters more than integrations/API (or vice versa) | PD-34 | Wrong Advanced content | Ranked choice in segment 6 interviews and customer requests | Output: ranked list; no scope without H-26 pass |
| H-28 | Pilots deliver the outcomes in Vision §9 (waste cost down, count variance down, stockouts down, owner hours down, ≥ 90 % items costed) | Vision §9 | Value claim unproven | E-7/E-8 8-week outcome measures | Pass: ≥ 60 % of completing pilots improve on ≥ 2 of the first 3 measures vs first 2 weeks |
| H-29 | Customers retain after trial (month-2 payment) at a level supporting the 21-customer target | C-26 | Churn defeats target | Paying-customer cohort after G-R1 | Pass: ≥ 80 % of first paid cohort pay month 3 (proposed) |
| H-30 | Monthly price sensitivity: a price band exists where ≥ 50 % of qualified prospects consider the plan acceptable | PRICING §3 | Mispricing | Van Westendorp (§5.2) on n ≥ 20 | Output: range of acceptable prices per offering; labelled directional |

Hypothesis ID range: **H-01 to H-30**. Add new IDs; never renumber.

---

## 4. Per-offering validation

### 4.1 InventoryOS Complete, $99 per location per month (Scenario A)

| Question | Hypotheses | Method | Evidence that confirms (proposed) |
|---|---|---|---|
| Do owners understand the value? | H-03 | 60-second explanation, ask them to restate it; show a mock recipe-costing and expiry screenshot (mark as concept) | ≥ 60 % restate correctly |
| Which POS integrations matter? | H-05, H-07, H-06 | "Which POS?" question; "what would stop you using it if yours isn't supported?" | Square first confirmed or reordered; other POS named with counts |
| Which inventory/reconciliation problems justify the price? | H-04, H-14 | Ask owner to rank pains (Vision §4 #1–12) after unprompted recall; diary data | Top 3 pains consistent across ≥ 50 % of POS users |
| Which workflows create measurable savings? | H-04, H-28 | E-2 diary: waste cost, variance, stockouts, hours; compare to plan price | Documented avoidable loss ≥ proposed 3× price for ≥ 50 % |
| Is $99 acceptable? | H-12, H-30 | Price questions; paid conversion after Square pilot | See H-12 |

Watch-outs: POS-native stock features may satisfy some owners (Vision §7 is an assumption);
record exactly what they use and why it is enough or not.

### 4.2 InventoryOS Standalone, $99 per month, one location (Scenario B)

| Question | Hypotheses | Method | Evidence that confirms (proposed) |
|---|---|---|---|
| Do customers without a POS exist and want to stay without one? | H-08 | Screener question "do you use a POS?" and "would you want one?" | See H-08 |
| What are their manual workflows today? | H-02, H-09 | "Walk me through yesterday close"; ask to see the sheet/notebook (with consent) | Workflows documented for ≥ 8 no-POS businesses |
| Is it valuable without transaction ingestion? | H-10, H-11 | E-3 concierge with daily tally; E-7 free pilot | See H-10, H-11 |
| Which reporting/inventory workflows matter? | H-14 | Observe which screens/reports pilots open weekly; ask to rank | Top reports named by ≥ 50 % |
| Is $99 right when sales data is manual? | H-13, H-30 | Price questions; conversion/deposit after pilot | See H-13 |
| Daily entry vs file import | H-09, H-11 | Offer both in pilot; log which they choose | Preference recorded with counts |

### 4.3 Future InventoryOS POS, $149–199 per month starting (Scenario C) — future only

Nothing is promised or built. Payment processing, hardware, card readers, fees and taxes are
**not included** in the stated price; say so in every conversation. Tied to **PD-35** and **C-26**.

| Question | Hypotheses | Method |
|---|---|---|
| How many want an integrated POS? | H-19 | Count Scenario C at screener, E-5 plan selection, pilots (PD-36 "interest recorded"); report as count of qualified prospects |
| Dissatisfaction with current POS | H-20 | Ask what they like/dislike, cost, last time they evaluated alternatives, contract/hardware lock-in |
| Would they switch? | H-20, H-21 | "What would have to be true for you to move?" then E-9 pre-commit |
| Essential checkout/payment capabilities | H-22 | Unprompted list first, then checklist (tabs, tips, discounts, modifiers, split payments, offline mode, receipts, tax, gift cards, online ordering, hardware, reporting); mark must / nice / not needed |
| Does integration justify the cost? | H-23 | Compare their current total POS cost (they state it) vs proposed price plus processing; note processing-rate sensitivity |
| Build vs white-label vs partner | PD-35 | Desk research later, not customer-facing; costing is an engineering/owner task |

PD-35 gate checklist (all required before any build commitment; from PD-35 and C-26):

| # | Criterion | Source | Status |
|---|---|---|---|
| N-1 | Scenario C interest captured at signup | PD-35 | Not started |
| N-2 | ≥ 10 qualified prospects willing to pre-commit at $149+ | PD-35 | Not started |
| N-3 | Build vs white-label vs partner costed | PD-35 | Not started |
| N-4 | $1,000 MRR reached from InventoryOS | C-26 | Not started |
| N-5 | Regulatory, hardware, support burden assessed | PRICING §3 | Not started |

"Qualified" (proposed definition): owner/decision-maker of a matching business, currently without
a POS or actively unhappy with theirs, operating now, who has stated a budget and a time frame.
Pre-commit (proposed ladder): stated intent < signed non-binding LOI < refundable deposit < paid.
Count only LOI or stronger toward N-2 **[OWNER to confirm the definition]**.

### 4.4 Multi-location (~$149–199+ per month) — PD-33

| Question | Hypotheses | Method |
|---|---|---|
| At what location count is extra capability needed? | H-24 | Ask operators with 2+ sites how they handle ordering, transfers, comparison today; record count and pain onset |
| Which features justify a higher price? | H-24 | Rank: cross-store dashboard, transfers, location-scoped roles, consolidated purchasing, store comparison, commissary |
| Per-location vs package | H-25 | Anchored questions: "N × $99?", "one price including N?"; capture reasoning, not just numbers |
| Source of evidence | | Segment 5 interviews now; then paying customers who add a second site (proposed gate: ≥ 3 asking, PD-33 decision) |

### 4.5 Advanced (~$199–299+ per month) — PD-34

| Question | Hypotheses | Method |
|---|---|---|
| Which capabilities would customers pay for? | H-26 | Log every request from paying customers in the evidence log tagged to a capability; count distinct customers |
| Are forecasting/automation important? | H-27 | Ask segment 6 and growing operators to rank; check what they do manually today |
| Which integrations/APIs matter? | H-27, H-07 | Ask which tools (accounting, payroll, delivery apps, suppliers' ordering) they use and what data they move by hand |
| Boundaries | PD-34 | Standard roles and core audit trail stay in base offerings; ask only about custom roles and extended history/exports |
| Gate | H-26 | ≥ 3 paying customers asking for the same capability (PD-34) |

---

## 5. Interview guide (30 minutes)

Rules: ask open, past-behaviour questions ("tell me about the last time"), not hypotheticals. Do
not describe InventoryOS until minute 21. Never ask "would you use...?" or "don't you think...?".
Take notes by hand or typed; record audio only with explicit consent (§10).

### 5.1 Script

| Min | Block | Questions (neutral wording) |
|---|---|---|
| 0–3 | Warm-up and consent | Thanks; purpose is learning, not selling; no obligation; notes taken, business name not shared; ask consent for notes (and audio, optional). "Tell me about your shop: what you make, how many people, how long." |
| 3–10 | Current workflow | "Walk me through a delivery from arrival to shelf." "How do you decide what to order?" "What happened the last time you ran out of something?" "What do you do at close?" "How do you know what a drink costs you?" "Where do you record any of this? Can you show me?" |
| 10–16 | Pain quantification | "Last week, what did you throw away?" "Roughly what did that cost?" (record their estimate and basis) "How many hours a week on ordering, counting, entering invoices?" "Last time a delivery didn't match the invoice, what happened?" "What have you tried to fix this? What happened?" "What does it cost you when it goes wrong?" |
| 16–21 | POS situation | "What do you use to take orders and payments? How long? What does it cost per month, all-in?" "What do you like / not like?" "When did you last look at alternatives?" If none: "Why no register system? Would you want one? What stops you?" "Could you tell me how many of each drink sold yesterday? Where would that come from?" |
| 21–24 | Concept reaction | Read one neutral paragraph describing the planned product (use "planned", no features list beyond ingredients-from-sales, expiry, waste, receiving). "In your own words, what would this do for you?" "What would worry you?" "Which one thing would you want first?" Then rank pains card sort (Vision §4 items 1–12; shuffled). |
| 24–28 | Price reaction | See 5.2 |
| 28–30 | Close | "Who else should I talk to?" "May I follow up in a few weeks?" "Would you try a free early version when it exists?" (log as stated intent only) Thank; incentive. |

### 5.2 Price questions (method labels)

| Method | Use | Wording |
|---|---|---|
| **Van Westendorp price sensitivity (Price Sensitivity Meter)** — standard four questions, per offering | Directional range; n ≥ 20 per offering to read curves, else report individual answers | "At what monthly price would this be so cheap you'd doubt its quality?" · "...a bargain?" · "...getting expensive but you'd still consider it?" · "...too expensive to consider?" Ask **before** revealing any planned price. |
| **Simple anchored reaction** (use only after PSM) | Compare to hypotheses | "The planned price is $X per location per month. How do you feel about it?" then "What would make it clearly worth it?" Rotate anchor ($99 vs an unstated alternative) across interviews to reduce anchoring; note rotation in log. |
| **Cost-of-problem comparison** | Sanity check | Their own weekly waste estimate and hours vs monthly price. |
| **Commitment ladder** (strongest signal) | G-R1 and PD-35 | stated intent → agrees to follow-up → shares data/invoices → signs LOI → refundable deposit → paid. |

Caution: stated willingness to pay overstates real payment (general survey-research caveat; no
figure assumed here). Weight commitments above PSM answers (P-4). Do not use PSM results as
the price; use them to choose which prices to test in E-6/E-9.

### 5.3 Segment add-on questions (insert in minutes 10–16)

| Segment | Add-on |
|---|---|
| Juice bars | "How do you decide how much to press or blend each morning?" "What happens to made-ahead juice or shots at day end?" "How do you handle yield differences between produce batches?" |
| Smoothie shops | "How do you track boosts, milk options and add-ins?" "How often do frozen stock deliveries arrive and how do you count them?" |
| Cafés / coffee | "How do you track milk, syrups and pastries?" "Who orders from the roaster and how?" "How do cups and packaging get reordered?" |
| Bubble tea / specialty | "How long can cooked toppings sit before you discard them? Who tracks the timer?" (H-18) "How do sweetness/ice/topping choices change how much you use?" |
| Small food-service / no-POS | "How do you know what sold at the end of an event or day?" "What do you use to take payments?" |
| Multi-location | "Walk me through ordering for all your sites." "How do stock or prepared items move between stores?" "How do you compare stores?" "Who can see or change what?" "What do you pay for tools per location today?" |
| Sophisticated | "What do you forecast and how?" "Which systems exchange data by hand?" "Who in your team does purchasing?" "What would you automate first?" |
| Scenario C | Checkout capability checklist (§4.3); "What would you need to see to trust a new payment system?" |

### 5.4 Interviewer discipline

| Do | Don't |
|---|---|
| Ask "tell me about the last time" | Ask "would you...?" or "do you wish...?" |
| Sit through silences | Fill in answers or suggest numbers |
| Record exact words in quotes | Paraphrase into what you hoped to hear |
| Log disconfirming comments first | Argue or defend the idea |

---

## 6. Validation experiments

"Earliest" = earliest milestone at which the experiment can run. "Effort" = founder time plus cash,
proposed.

| ID | Experiment | Goal | Method | Sample (proposed) | Duration | Effort / cost | Success criteria (proposed) | Informs | Earliest |
|---|---|---|---|---|---|---|---|---|---|
| E-1 | Problem interviews | Test H-01..H-03, H-05, H-07..H-09, H-14, H-18..H-20, H-24, H-30 | §5 script, 30 min, in person or phone | ~30 across segments (§2) | Weeks 1–10 | ~1.5 h per interview incl. notes; small incentive per person | Saturation per segment; pass thresholds on H-01, H-02, H-03 | G-R1 scope and messaging; segment priority; POS adapter order | Now |
| E-2 | Waste/variance diary study | Quantify avoidable loss for H-04, H-17, H-28 baseline | Participant records 14 days: waste (item, qty, reason, cost est.), stockouts, hours on inventory; simple phone form or paper sheet | 8–10 shops (juice bars first) | 2 weeks + 30 min debrief | 2 h setup + 30 min review each; no software | ≥ 5 shops complete; H-04 pass threshold | Value claim, price confidence, which features lead | Now |
| E-3 | Concierge/manual pilot (spreadsheets) | Test H-10, H-16 without software: run the core workflow by hand | Founder sets up items/recipes/opening count in a spreadsheet, shop does daily tally and weekly count, founder computes usage and variance and sends a weekly report | 3–5 shops (include ≥ 1 no-POS) | 4–6 weeks | ~3 h/shop/week; mild support load; **[OWNER]** to decide time | ≥ 3 shops keep sending data to week 4 and ask for the next report | Standalone viability, report design, onboarding time | Now |
| E-4 | Landing page + plan selection + "which POS?" | Gather demand signal, H-05, H-07, H-08, H-19 | Single page describing planned product (label "planned"; no purchase), choices: Complete / Standalone / "Want a POS too" / Multi-location / Other; free-text "which POS do you use?"; email optional with consent text | Traffic from §2.1 channels only (no ad budget assumed) | 4 weeks live, review weekly | 1–2 days to build with a no-code tool; low cost; **[OWNER]** to approve tool and domain | ≥ 20 responses with valid POS answer; counts per plan; no fabricated conversion benchmarks | Adapter order, Scenario C count (N-1), offer wording | Now (parallel with Stage 0–2) |
| E-5 | Waitlist follow-up calls | Convert E-4 interest into E-1 interviews and pilot candidates | Ask each respondent for a 20-min call | All consenting respondents | Ongoing | 30 min each | ≥ 30 % of consenting respondents agree to a call (proposed) | Pilot recruitment | After E-4 live |
| E-6 | Price validation round | Narrow price tests for H-12, H-13, H-30 | Van Westendorp + anchored question from §5.2 in a second short call or a short form | 20 per offering where possible | 3 weeks | Low | PSM acceptable range includes $99 for ≥ 50 % of Complete and Standalone prospects | Which prices go into G-R1 plan config (E-01: config, not code) | After ~15 E-1 interviews |
| E-7 | Free Standalone pilot | Test H-10, H-11, H-13, H-16, H-17, H-28 on the real product | Free for 8 weeks; onboarding session timed; weekly 15-min check-in; usage logged (with consent); week-8 conversion offer at $99 | 5–8 shops | 8 weeks | ~2 h/pilot/week support | H-10, H-11, H-13 thresholds; ≥ 4 complete 8 weeks | G-R1 readiness; Standalone positioning | **M4** |
| E-8 | Square pilot (Complete) | Test H-06, H-12, H-16, H-28 | Free 8-week pilot; assisted Square connect; mapping help; track onboarding minutes, mapping completion, first-dashboard time; week-8 offer at $99 | 5–8 Square shops | 8 weeks | ~3 h/pilot/week early (mapping) | H-06 ≥ 70 % connect; H-12 ≥ 60 % convert | G-R1 readiness; Complete positioning; A-10 confirmation | **M5** |
| E-9 | Letters of intent / refundable pilot deposits | Strongest pre-launch commitment signals; also PD-35 N-2 | Written non-binding LOI (one page, plain language, states "not built, price indicative, processing fees extra") then optional small refundable deposit or founder-discounted prepaid first month **[OWNER and legal review of wording and any deposit handling]** | Pilots from E-7/E-8 and interviewees; ≥ 10 Scenario C for PD-35 | Rolling | Low cash; compliance risk if deposits mishandled | Counts per rung of the commitment ladder | G-R1 (first paying customers), PD-35 N-2 | LOI: now; deposit: after **M4** |
| E-10 | Paid pilot conversion | Convert free pilots to paid at G-R1 | Offer at week 8 with the plan price from E-6 | All completing pilots | Per pilot | Billing setup | H-12, H-13, H-29 | G-R1 | **G-R1** |
| E-11 | Multi-location interviews | H-24, H-25 | Segment 5 interview script plus pricing structure questions | 4 operators | Weeks 6–12 | Low | Consistent top-3 needs | PD-33 input | Now |
| E-12 | Customer request log | H-26, H-27 | Every feature/integration request from paying customers logged and tagged | All paying customers | Ongoing from G-R1 | Low | ≥ 3 customers same capability | PD-34, Stage 7 | G-R1 |
| E-13 | Scenario C deep dive | H-19..H-23 | 20-min follow-up with Scenario C and unhappy-POS prospects: capabilities checklist, current POS costs, LOI offer | 10–15 | After N-1 data exists | Medium | N-2 ≥ 10 LOI-level | PD-35 | After M5, only when the $1,000 MRR gate (C-26) is within sight; earlier as listening only |
| E-14 | Invoice sample collection | H-15, A-05 | With consent, photograph 20–30 real supplier invoices and note suppliers/format | 5–8 shops | During E-1/E-2 | Low | Confirms/refutes paper-PDF assumption | RCV/extraction scope (PD-25) | Now |

Experiment ethics and integrity: label every pre-launch artifact "planned"; no screenshots of
software that does not exist presented as live; no real card details collected (see §10).

---

## 7. Metrics and stage-gate criteria (all thresholds PROPOSED)

Evidence from the log counts only if confidence is Medium or High (§8.1). No gate may be passed on
fabricated or unlogged results. Gates are **owner decisions**; this table is the evidence package.

| Gate | Evidence required (proposed) | Source |
|---|---|---|
| **Start M4 pilots (free Standalone)** | ≥ 15 problem interviews logged; H-01, H-02 not failed; ≥ 5 pilot-willing no-POS or low-POS prospects recruited by name | E-1, E-3, E-4 |
| **Start M5 pilots (Square)** | ≥ 5 Square (or mapped-POS) shops agree to pilot; A-10 verified; H-05 not failed | E-1, E-4 |
| **G-R1 (first paying customers)** | (a) E-7 + E-8: ≥ 8 pilots completed 8 weeks; (b) H-12 or H-13 passes (conversion ≥ 50 % Standalone / ≥ 60 % Complete); (c) H-16 median onboarding ≤ 60 min; (d) at least 3 customers say they would be "very disappointed" to lose it; (e) waste/variance improvement on ≥ 60 % of completing pilots (H-28); (f) price decision made with E-6 data; (g) pilot data sharing and billing consent text in place (§10) | E-6..E-8, E-10 |
| **After G-R1: M7 / R1.1 priorities** | Top-3 requests by distinct customers; top-3 churn/cancel reasons; support hours per customer | E-12, log |
| **Toward 21 customers (C-26)** | Paying customers count; month-3 retention ≥ 80 % (H-29); pipeline source split (referral vs outreach vs listing) | Billing, log |
| **Stage 6 Multi-location** (gate: paying customers asking + PD-33) | ≥ 3 paying customers with or planning ≥ 2 sites ask for the same centralized capability; E-11 supports pricing structure; PD-33 decided by owner | E-11, E-12 |
| **Stage 7 Advanced** (gate: PD-34) | ≥ 3 paying customers ask for the same capability; H-26/H-27 evidence; boundary check against "never gated" list (PRICING §4) | E-12 |
| **Stage 8 Native POS feasibility** (gate: C-26 plus PD-35) | $1,000 MRR reached (N-4); N-1..N-5 complete (§4.3); ≥ 10 qualified prospects at LOI level or stronger (H-21); build/white-label/partner costed; owner decision | E-9, E-13 |

### 7.1 Ongoing metrics to track from the first interview

| Metric | Definition | Cadence |
|---|---|---|
| Interviews done / saturated segments | Count by segment | Weekly |
| Qualified prospects | Matching §4.3 definition | Weekly |
| POS tally | Count per POS and "none" | Weekly |
| Scenario split (A / B / C) | Count of qualified prospects | Weekly |
| Pilot funnel | Invited → onboarded → active at week 4 → week 8 → converted | Per pilot |
| Time to first dashboard | Minutes from signup to real stock shown | Per pilot |
| Commitment ladder counts | Per rung (§5.2) | Weekly |
| Outcome measures | Vision §9 table | Per pilot week 2 vs week 8 |

---

## 8. Evidence log and governance

### 8.1 Evidence log template

Location (proposed): `docs/roadmap/evidence/EVIDENCE_LOG.md`, created when the first real entry
exists (not created now). Table or one block per entry; no personal data (§10).

| Field | Content |
|---|---|
| ID | EV-001, EV-002, ... (never reused) |
| Date | YYYY-MM-DD |
| Source type | interview / diary / concierge / landing page / pilot / LOI / customer request / desk research |
| Participant ref | Code (e.g. P-014); business name only with written permission |
| Segment | Vision §5.2 number and name |
| POS | Square / Toast / Clover / other (name) / none-by-choice / none-wants-one |
| Quote or observation | Exact words in quotes, or factual observation; no interpretation |
| Hypotheses affected | H-xx list, with direction: supports / contradicts / neutral |
| Evidence strength | Commitment ladder rung (§5.2) |
| Confidence | High / Medium / Low (High = behaviour or commitment; Medium = detailed past behaviour; Low = opinion, hypothetical, friend/family bias) |
| Bias note | Relationship to founder, incentive paid, prompted vs unprompted |
| Follow-up | Next step, if any |

Register view (maintained below the log): H-xx → count supporting / contradicting / status
(Untested, Testing, Supported, Refuted, Inconclusive) → last updated.

### 8.2 Feeding back into product decisions

| Step | Rule |
|---|---|
| 1. Log | Result goes into the evidence log first |
| 2. Update register | Hypothesis status changes only when its threshold is met or missed, with ≥ 3 independent entries |
| 3. Propose | Write a *proposed decision*: what changed, evidence IDs, effect on assumptions, scope, price, or priority |
| 4. Owner decides | Owner accepts, rejects, or asks for more evidence |
| 5. Record | Accepted items update `docs/product/PRODUCT_DECISIONS.md` (new decision row; assumption A-xx marked **confirmed** or **refuted** with date and EV IDs), then PRICING_AND_PACKAGING / PRODUCT_VISION / roadmap |
| 6. Never silent | No finding edits scope, pricing, or the roadmap without steps 3–5 |

### 8.3 Assumption linkage

| Assumption | Hypotheses that confirm/refute |
|---|---|
| A-01 POS mix | H-05, H-07, H-08 |
| A-04 shelf lives | H-18 |
| A-05 paper invoices | H-15 |
| A-08 business-day cutoff | H-11 (late closers noted in diary) |
| A-09 smartphones/shared tablet | H-17 |
| A-10 Square OAuth without marketplace | H-06, E-8 (technical verification, not customer research) |
| A-13 daily item-level sales figure | H-09, H-10 |
| A-14 segment profiles | H-01, H-02, H-24, H-30 |
| A-06, A-07, A-02, A-03, A-11, A-12 | Not customer-validation topics here (A-02 currency/units and A-12 PII may surface in pilots; log if so) |

### 8.4 Roadmap feedback

| Finding type | Roadmap effect (as a proposal, never automatic) |
|---|---|
| A different POS dominates | Reorder adapter after Square; Square stays first by owner choice unless owner decides otherwise |
| Standalone pilots fail H-10 | Propose adding or strengthening sales-import features in M4/M5 or reconsidering Standalone at launch |
| Onboarding > 60 min | Prioritize import/mapping/onboarding work before G-R1 |
| Price fails | Owner decides repricing; plans are configuration (E-01..E-03) |
| Multi-location need emerges early | Propose pulling forward Stage 6 only per its gate |
| Hour-level expiry confirmed (H-18) | Raise PD-40 priority |

---

## 9. Timeline

W1 is the week of 2026-11-09. Engineering stage dates are not given in the sources; milestone-
aligned rows move with the roadmap. Founder time budget (proposed): ~8 h/week on validation
**[OWNER to confirm]**. Weeks 1–10 run in parallel with Stage 0–2 engineering.

| Week | Activities | Output |
|---|---|---|
| W1 | Approve this plan; build prospect sheet; draft screener, consent text (§10), interview notes template; create evidence log file when first entry exists; choose incentive | Ready to talk to people |
| W2 | First 4 walk-in/network interviews (juice bars); test script; adjust wording | EV-001..; script v2 |
| W3 | 5 more interviews; invite 3–4 juice bars to E-2 diary; start E-14 invoice samples | Diary participants recruited |
| W4 | Build E-4 landing page (planned-offer wording, POS capture, consent); 4 interviews (smoothie/bubble tea); E-2 diaries running | Page live (if owner approves) |
| W5 | Share page in communities; 4 interviews (cafés/coffee); recruit E-3 concierge shops (3–5) | First plan/POS counts |
| W6 | E-3 concierge starts; E-2 debriefs; 3 no-POS interviews; E-11 multi-location interviews begin; first register update (H-01..H-03) | First propose-decision memo for the owner |
| W7 | E-3 week 2; E-5 calls to landing respondents; start E-6 price round | PSM data collecting |
| W8 | E-3 weekly reports; E-6 continues; E-9 LOI draft for review **[OWNER/legal]** | LOI wording reviewed |
| W9 | Interim review at ~25 interviews: pass/fail on H-01..H-05, H-08, H-09, H-14; segment ranking updated | Decision memo: what R1 positioning and which offerings to emphasize |
| W10 | E-3 wrap-up; recruit 5–8 Standalone and 5–8 Square pilot candidates by name | Pilot waitlist ready for M4/M5 |
| Milestone **M4** | E-7 free Standalone pilots start (8 weeks); optional E-9 refundable deposits | H-10, H-11, H-13, H-16 evidence |
| Milestone **M5** | E-8 Square pilots start (8 weeks); A-10 verified technically | H-06, H-12 evidence |
| M4/M5 + ~8 weeks | Week-8 conversion offers; outcome measures; gate review for G-R1 | §7 G-R1 evidence package |
| **G-R1** | E-10 paid conversion; start E-12 request log; retention tracking | H-29; first paying cohort |
| **M7 / R1.1** | Re-prioritize from log; continue E-12; keep recruiting toward 21 customers (C-26) | Proposed R1.1 priorities |
| Stage 6 / 7 / 8 | Only per gates in §7; E-11, E-12, E-13 evidence feeds PD-33, PD-34, PD-35 | Gate memos for the owner |

Slip rule: validation weeks do not depend on engineering; if engineering slips, keep interviewing
and extend E-3 concierge so pilots stay warm. If W9 shows H-01 or H-02 failing, escalate to the
owner before further build investment.

---

## 10. Ethics and data

| Topic | Rule |
|---|---|
| Consent for notes | Ask at the start of every conversation; note consent in the log. Audio/video recording only with explicit separate consent; delete audio after transcription or by a stated date |
| Honesty | State purpose (research, early product, may never launch features discussed); no deception about product existence; label planned offers as planned |
| Data minimization | Repo contains no personal data: no names of individuals, phone numbers, email addresses, or photos of people. Use participant codes (P-014). Business names only with written permission; otherwise a code. Contact details live outside the repo in the prospect sheet (access limited to the owner) |
| Pilot data | Before pilots, agree in plain language what is collected, why, retention, and deletion/export on request (consistent with SAAS_REQUIREMENTS data export/deletion). Invoices and sales data belong to the shop; used only for the pilot and aggregated product learning with consent |
| Quotes | Use only with permission and anonymized; no public quotes or logos without written permission |
| Incentives | Disclosed upfront; not conditional on favorable answers |
| Payments | No card or bank details collected in forms, interviews or docs. Deposits and payments only through a real payment processor, only after **[OWNER]** approves wording and handling |
| Communications | Outreach in line with applicable email/SMS rules and each community's policy; respect "no thanks"; no purchased lists (verify applicable rules before any bulk email) |
| Competitor information | Only from public sources or what participants volunteer about their own usage; do not ask them to share competitors' confidential material |
| Vulnerable moments | If a shop is in trouble or the owner is stressed, shorten the call and do not pitch |
| Legal | LOIs, deposits and pilot terms get a lawyer's or qualified reviewer's check before use **[OWNER]** |

---

## 11. Open owner decisions and unknowns

| # | Item |
|---|---|
| O-1 | Weekly founder hours and incentive budget for validation |
| O-2 | Landing page tool, domain, and whether to run it before M4 (E-4) |
| O-3 | Definition of "qualified" and of "pre-commit" for PD-35 (§4.3 proposal) |
| O-4 | Whether deposits/prepayment are allowed pre-launch and who reviews the terms |
| O-5 | Whether JuJu contacts may be used for InventoryOS interviews |
| O-6 | Actual dates for M4, M5, G-R1: not in the source docs; timeline is milestone-aligned |
| O-7 | Whether the concierge pilot (E-3) is acceptable founder time before any software exists |
