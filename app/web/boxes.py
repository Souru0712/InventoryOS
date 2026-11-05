"""The store's side of snack boxes: what to pack, the shelf check, refunds, stock reconciliation,
snack profiles, settings and the referral ledger."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.db import get_db
from app.entitlements import owns_snack_box
from app.models import BoxLine, BoxLineStatus, BoxOrder, BoxOrderStatus, Customer, Location, Tenant
from app.orchestration import boxes as box_orders
from app.orchestration import customers, referrals
from app.orchestration.views import tenant_currency
from app.security import require_tenant
from app.transform.boxes import FLAVORS
from app.web.flash import flash, pop_flash
from app.web.inputs import posted_form
from app.web.templating import templates

router = APIRouter()
DOLLAR_FIELDS = (
    "price_sweet_cents",
    "price_chaos_cents",
    "price_world_cents",
    "target_cogs_cents",
    "packaging_cents",
    "payment_fee_fixed_cents",
    "min_contribution_cents",
    "referral_reward_cents",
    "referral_discount_cents",
)
COUNT_FIELDS = (
    "min_items",
    "max_items",
    "min_days_after_pickup",
    "pickup_after_days",
    "payment_window_minutes",
)


def require_snack_shop(tenant: Tenant = Depends(require_tenant)) -> Tenant:
    """The snack shop belongs to the operator, not to the product: for anyone else it does not exist."""
    if not owns_snack_box(tenant):
        raise HTTPException(status_code=404, detail="not found")
    return tenant


def _cents(text: str) -> int:
    try:
        return int((Decimal(text.strip() or "0") * 100).quantize(Decimal("1")))
    except InvalidOperation as exc:
        raise ValueError(f"{text!r} is not an amount") from exc


def _render(request: Request, db: Session, tenant: Tenant, template: str, **context):
    return templates.TemplateResponse(
        request,
        template,
        {
            "tenant": tenant,
            "currency": tenant_currency(db, tenant.id),
            "flash": pop_flash(request),
            **context,
        },
    )


def _order_or_404(db: Session, tenant: Tenant, order_id: int) -> BoxOrder:
    order = db.get(BoxOrder, order_id)
    if order is None or order.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="box order not found")
    return order


def _done(request: Request, order: BoxOrder, problems: list[str], ok: str) -> RedirectResponse:
    flash(request, " ".join(problems) or ok, "error" if problems else "ok")
    return RedirectResponse(f"/app/boxes/{order.id}", status_code=303)


@router.get("/app/boxes", response_class=HTMLResponse)
def boxes_page(
    request: Request, db: Session = Depends(get_db), tenant: Tenant = Depends(require_snack_shop)
):
    settings = box_orders.box_settings(db, tenant.id)
    db.commit()
    rows = db.execute(
        select(BoxOrder, Customer)
        .join(Customer, Customer.id == BoxOrder.customer_id)
        .where(BoxOrder.tenant_id == tenant.id)
        .order_by(BoxOrder.created_at.desc())
    ).all()
    groups = {
        "attention": [
            r for r in rows if r[0].attention and r[0].status is not BoxOrderStatus.CANCELLED
        ],
        "paid": [r for r in rows if r[0].status is BoxOrderStatus.PAID],
        "packed": [r for r in rows if r[0].status is BoxOrderStatus.PACKED],
        "pending_payment": [r for r in rows if r[0].status is BoxOrderStatus.PENDING_PAYMENT],
        "done": [
            r
            for r in rows
            if r[0].status
            in (BoxOrderStatus.PICKED_UP, BoxOrderStatus.REFUNDED, BoxOrderStatus.CANCELLED)
        ][:25],
    }
    report = [row for row in box_orders.stock_report(db, tenant.id) if row.issues]
    return _render(
        request,
        db,
        tenant,
        "app/boxes/index.html",
        title="Snack boxes",
        box=settings,
        groups=groups,
        report=report,
    )


@router.get("/app/boxes/stock", response_class=HTMLResponse)
def stock_page(
    request: Request, db: Session = Depends(get_db), tenant: Tenant = Depends(require_snack_shop)
):
    return _render(
        request,
        db,
        tenant,
        "app/boxes/stock.html",
        title="Stock check",
        rows=box_orders.stock_report(db, tenant.id),
    )


@router.post("/app/boxes/reconcile")
def reconcile_now(
    request: Request, db: Session = Depends(get_db), tenant: Tenant = Depends(require_snack_shop)
):
    rows = box_orders.reconcile(db, tenant.id)
    db.commit()
    open_issues = sum(1 for row in rows if row.issues)
    flash(
        request,
        f"Reconciled {len(rows)} item(s); {open_issues} still need a look.",
        "error" if open_issues else "ok",
    )
    return RedirectResponse("/app/boxes/stock", status_code=303)


@router.get("/app/boxes/{order_id:int}", response_class=HTMLResponse)
def order_page(
    request: Request,
    order_id: int,
    swap: int | None = None,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_snack_shop),
):
    order = _order_or_404(db, tenant, order_id)
    settings = box_orders.box_settings(db, tenant.id)
    lines = customers.order_lines(db, order)
    offer_for = [
        line
        for line, _ in lines
        if line.status is BoxLineStatus.SHORT
        or (line.id == swap and line.status is BoxLineStatus.ALLOCATED)
    ]
    options = {line.id: box_orders.replacement_options(db, order, line) for line in offer_for}
    response = _render(
        request,
        db,
        tenant,
        "app/boxes/order.html",
        title=f"Box #{order.id}",
        order=order,
        box=settings,
        customer=db.get(Customer, order.customer_id),
        lines=lines,
        options=options,
        contribution=box_orders.order_contribution_cents(db, settings, order),
        referral=referrals.referral_for_order(db, order),
        has_packed=any(line.status is BoxLineStatus.PACKED for line, _ in lines),
    )
    db.commit()
    return response


@router.post("/app/boxes/{order_id:int}/paid")
def paid_at_counter(
    request: Request,
    order_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_snack_shop),
):
    order = _order_or_404(db, tenant, order_id)
    if order.status is not BoxOrderStatus.PENDING_PAYMENT:
        return _done(request, order, ["Only an unpaid order can be marked paid."], "")
    box_orders.confirm_payment(db, order, "counter")
    db.commit()
    return _done(request, order, [], "Paid. The box is built and its snacks are set aside.")


@router.post("/app/boxes/{order_id:int}/pack")
def pack(
    request: Request,
    order_id: int,
    form: FormData = Depends(posted_form),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_snack_shop),
):
    order = _order_or_404(db, tenant, order_id)
    ticked = {int(v) for v in form.getlist("line") if str(v).isdigit()}
    problems = box_orders.pack(db, order, ticked)
    # Commit either way: a pack that stopped may still have found and settled a shortage.
    db.commit()
    return _done(request, order, problems, "Packed and ready for pickup.")


@router.post("/app/boxes/{order_id:int}/picked-up")
def picked_up(
    request: Request,
    order_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_snack_shop),
):
    order = _order_or_404(db, tenant, order_id)
    problems = box_orders.pick_up(db, order)
    db.commit()
    return _done(request, order, problems, "Picked up.")


@router.post("/app/boxes/{order_id:int}/cancel")
def cancel(
    request: Request,
    order_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_snack_shop),
):
    order = _order_or_404(db, tenant, order_id)
    problems = box_orders.cancel(db, order)
    db.commit()
    return _done(request, order, problems, "Cancelled.")


@router.post("/app/boxes/{order_id:int}/refund")
def refund(
    request: Request,
    order_id: int,
    restock: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_snack_shop),
):
    order = _order_or_404(db, tenant, order_id)
    problems = box_orders.refund(db, order, restock == "on")
    db.commit()
    return _done(
        request,
        order,
        problems,
        "Refund recorded. Give the money back in Square if you have not already.",
    )


@router.post("/app/boxes/{order_id:int}/restock")
def restock(
    request: Request,
    order_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_snack_shop),
):
    order = _order_or_404(db, tenant, order_id)
    problems = box_orders.restock(db, order)
    db.commit()
    return _done(request, order, problems, "Back on the shelf and in the till's count.")


@router.post("/app/boxes/{order_id:int}/lines/{line_id:int}/replace")
def replace(
    request: Request,
    order_id: int,
    line_id: int,
    stock_item_id: int = Form(...),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_snack_shop),
):
    order = _order_or_404(db, tenant, order_id)
    line = db.get(BoxLine, line_id)
    if line is None or line.box_order_id != order.id:
        raise HTTPException(status_code=404, detail="line not found")
    problems = box_orders.replace_line(db, order, line, stock_item_id)
    db.commit()
    return _done(request, order, problems, "Swapped.")


@router.get("/app/boxes/snacks", response_class=HTMLResponse)
def snacks_page(
    request: Request, db: Session = Depends(get_db), tenant: Tenant = Depends(require_snack_shop)
):
    return _render(
        request,
        db,
        tenant,
        "app/boxes/snacks.html",
        title="Snacks",
        rows=box_orders.snack_rows(db, tenant.id),
        flavors=FLAVORS,
        allergens=customers.ALLERGENS,
    )


@router.post("/app/boxes/snacks/{stock_item_id:int}")
def save_snack(
    request: Request,
    stock_item_id: int,
    form: FormData = Depends(posted_form),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_snack_shop),
):
    label = str(form.get("allergens_state") or "unknown")
    allergens = (
        None
        if label == "unknown"
        else set()
        if label == "none"
        else {a for a in customers.ALLERGENS if form.get(f"allergen_{a}") == "on"}
    )
    if label == "listed" and not allergens:
        problems = ["Tick the allergens on the label, or choose none."]
    else:
        problems = box_orders.save_snack_profile(
            db,
            tenant.id,
            stock_item_id,
            form.get("boxable") == "on",
            {f for f in FLAVORS if form.get(f"flavor_{f}") == "on"},
            str(form.get("origin") or ""),
            allergens,
        )
    db.commit()
    flash(request, " ".join(problems) or "Saved.", "error" if problems else "ok")
    return RedirectResponse("/app/boxes/snacks", status_code=303)


@router.get("/app/boxes/settings", response_class=HTMLResponse)
def settings_page(
    request: Request, db: Session = Depends(get_db), tenant: Tenant = Depends(require_snack_shop)
):
    settings = box_orders.box_settings(db, tenant.id)
    locations = list(db.scalars(select(Location).where(Location.tenant_id == tenant.id)))
    response = _render(
        request,
        db,
        tenant,
        "app/boxes/settings.html",
        title="Box settings",
        box=settings,
        locations=locations,
    )
    db.commit()
    return response


@router.post("/app/boxes/settings")
def save_settings(
    request: Request,
    form: FormData = Depends(posted_form),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_snack_shop),
):
    settings = box_orders.box_settings(db, tenant.id)
    try:
        values = {field: _cents(str(form.get(field) or "0")) for field in DOLLAR_FIELDS}
        values.update({field: int(str(form.get(field) or "0")) for field in COUNT_FIELDS})
        values["payment_fee_bps"] = int(
            (Decimal(str(form.get("payment_fee_pct") or "0")) * 100).quantize(Decimal("1"))
        )
    except (ValueError, InvalidOperation):
        flash(request, "Every field needs a number.", "error")
        return RedirectResponse("/app/boxes/settings", status_code=303)
    location = str(form.get("location_id") or "")
    problems = box_orders.update_settings(
        db,
        settings,
        values,
        form.get("is_open") == "on",
        int(location) if location.isdigit() else None,
    )
    db.commit()
    flash(
        request,
        " ".join(problems) or "Saved. New boxes use these terms.",
        "error" if problems else "ok",
    )
    return RedirectResponse("/app/boxes/settings", status_code=303)


@router.get("/app/boxes/referrals", response_class=HTMLResponse)
def referrals_page(
    request: Request, db: Session = Depends(get_db), tenant: Tenant = Depends(require_snack_shop)
):
    people = list(
        db.scalars(select(Customer).where(Customer.tenant_id == tenant.id).order_by(Customer.email))
    )
    return _render(
        request,
        db,
        tenant,
        "app/boxes/referrals.html",
        title="Referrals",
        ledger=referrals.ledger(db, tenant.id),
        credits=referrals.credit_rows(db, tenant.id),
        people=people,
        balances={c.id: referrals.balance_cents(db, c.id) for c in people},
    )


@router.post("/app/boxes/customers/{customer_id:int}/credit")
def adjust_credit(
    request: Request,
    customer_id: int,
    amount: str = Form(...),
    note: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_snack_shop),
):
    customer = db.get(Customer, customer_id)
    if customer is None or customer.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="customer not found")
    try:
        referrals.adjust(db, customer, _cents(amount), note)
    except ValueError as exc:
        flash(request, str(exc), "error")
    else:
        db.commit()
        flash(request, "Credit adjusted.")
    return RedirectResponse("/app/boxes/referrals", status_code=303)
