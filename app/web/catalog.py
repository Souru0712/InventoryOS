"""Item and category screens (T1.14): /app/catalog. Replaces the old /app/items/new form.

Anyone who can see inventory can browse and search items; changing them takes item.edit. Every
change waits on the subscription's write rule like the rest of inventory.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.db import get_db
from app.items import (
    NewItem,
    NotFound,
    categories,
    category_counts,
    codes,
    create_category,
    create_item,
    has_history,
    item_row,
    list_items,
    merge_category,
    rename_category,
    set_active,
    set_category_archived,
    stock_at,
    update_item,
    update_levels,
)
from app.models import Location, Tenant
from app.security import location_filter, require_permission, require_tenant, require_writable
from app.throttle import client_ip
from app.transform.items import ITEM_TYPE_LABELS, ItemRuleError
from app.units import UNITS, UNITS_BY_DIMENSION, Dimension
from app.web.flash import flash, pop_flash
from app.web.templating import templates

router = APIRouter()

BASE = "/app/catalog"


def _not_found(exc: NotFound) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


def _locations(db: Session, org: OrgContext) -> list[Location]:
    return sorted(
        (loc for loc in db.scalars(select_scoped(org, Location)) if loc.archived_at is None),
        key=lambda loc: loc.name,
    )


def _units() -> dict[str, list[tuple[str, str]]]:
    return {d.value: [(u.code, u.label) for u in UNITS_BY_DIMENSION[d]] for d in Dimension}


def _dimension_of(unit: str) -> str:
    """The unit someone counts in decides how the item is measured."""
    return UNITS[unit].dimension.value if unit in UNITS else ""


# --- the list -----------------------------------------------------------------------------------


@router.get(BASE, response_class=HTMLResponse)
def items_page(
    request: Request,
    q: str = "",
    category: int | None = None,
    supplier: str = "",
    status: str = "active",
    low: str = "",
    expiring: str = "",
    location: int | None = Depends(location_filter),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("inventory.view")),
):
    try:
        rows = list_items(
            db,
            org,
            query=q,
            category_id=category,
            supplier=supplier,
            status=status,
            low=bool(low),
            expiring=bool(expiring),
            location_id=location,
        )
    except NotFound as exc:
        raise _not_found(exc) from exc
    return templates.TemplateResponse(
        request,
        "app/catalog.html",
        {
            "title": "Items",
            "tenant": tenant,
            "rows": rows,
            "categories": categories(db, org),
            "locations": _locations(db, org),
            "filters": {
                "q": q,
                "category": category,
                "supplier": supplier,
                "status": status,
                "low": bool(low),
                "expiring": bool(expiring),
                "location": location,
            },
            "type_labels": ITEM_TYPE_LABELS,
            "message": pop_flash(request),
        },
    )


# --- categories (before /{inventory_item_id}, so the word is not read as an id) -----------------


@router.get(f"{BASE}/categories", response_class=HTMLResponse)
def categories_page(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("item.edit")),
):
    return templates.TemplateResponse(
        request,
        "app/categories.html",
        {
            "title": "Categories",
            "tenant": tenant,
            "categories": categories(db, org, archived=True),
            "counts": category_counts(db, org),
            "message": pop_flash(request),
        },
    )


def _category_action(request: Request, db: Session, action, done: str) -> RedirectResponse:
    try:
        action()
    except NotFound as exc:
        raise _not_found(exc) from exc
    except ItemRuleError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(f"{BASE}/categories", status_code=303)
    db.commit()
    flash(request, done)
    return RedirectResponse(f"{BASE}/categories", status_code=303)


@router.post(f"{BASE}/categories")
def add_category(
    request: Request,
    name: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("item.edit")),
):
    ip = client_ip(request)
    return _category_action(
        request, db, lambda: create_category(db, org, name, ip=ip), "Category added."
    )


@router.post(BASE + "/categories/{category_id}/rename")
def rename(
    request: Request,
    category_id: int,
    name: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("item.edit")),
):
    ip = client_ip(request)
    return _category_action(
        request, db, lambda: rename_category(db, org, category_id, name, ip=ip), "Renamed."
    )


@router.post(BASE + "/categories/{category_id}/merge")
def merge(
    request: Request,
    category_id: int,
    into_id: int = Form(...),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("item.edit")),
):
    ip = client_ip(request)
    return _category_action(
        request,
        db,
        lambda: merge_category(db, org, category_id, into_id, ip=ip),
        "Merged. Its items moved to the other category.",
    )


@router.post(BASE + "/categories/{category_id}/archive")
def archive(
    request: Request,
    category_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("item.edit")),
):
    ip = client_ip(request)
    return _category_action(
        request,
        db,
        lambda: set_category_archived(db, org, category_id, True, ip=ip),
        "Archived. Its items keep it; new items cannot pick it.",
    )


@router.post(BASE + "/categories/{category_id}/restore")
def restore(
    request: Request,
    category_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("item.edit")),
):
    ip = client_ip(request)
    return _category_action(
        request,
        db,
        lambda: set_category_archived(db, org, category_id, False, ip=ip),
        "Restored.",
    )


# --- a new item ---------------------------------------------------------------------------------


def _new_form(request, db, org, tenant, values: dict, error: str | None = None, status: int = 200):
    return templates.TemplateResponse(
        request,
        "app/item_form.html",
        {
            "title": "Add an item",
            "tenant": tenant,
            "locations": _locations(db, org),
            "categories": categories(db, org),
            "type_labels": ITEM_TYPE_LABELS,
            "units": _units(),
            "values": values,
            "error": error,
        },
        status_code=status,
    )


@router.get(f"{BASE}/new", response_class=HTMLResponse)
def new_item_page(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("item.edit")),
):
    return _new_form(request, db, org, tenant, {"item_type": "raw", "dimension": "count"})


@router.post(f"{BASE}/new")
def add_item(
    request: Request,
    name: str = Form(""),
    location_ids: list[int] = Form([]),
    display_unit: str = Form("each"),
    item_type: str = Form("raw"),
    category: str = Form(""),
    notes: str = Form(""),
    quantity: str = Form("0"),
    reorder_point: str = Form("0"),
    par_level: str = Form(""),
    cost_per_unit: str = Form("0"),
    retail_price: str = Form(""),
    supplier: str = Form(""),
    receive_unit: str = Form(""),
    pack_size: str = Form(""),
    lead_time_days: str = Form("2"),
    sku: str = Form(""),
    upc: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("item.edit")),
):
    new = NewItem(
        name=name,
        location_ids=location_ids,
        dimension=_dimension_of(display_unit),
        display_unit=display_unit,
        item_type=item_type,
        category=category,
        notes=notes,
        quantity=quantity,
        reorder_point=reorder_point,
        par_level=par_level,
        cost_per_unit=cost_per_unit,
        retail_price=retail_price,
        supplier=supplier,
        receive_unit=receive_unit,
        pack_size=pack_size,
        lead_time_days=lead_time_days,
        codes=(upc, sku),
    )
    try:
        item = create_item(db, org, new, ip=client_ip(request))
    except NotFound as exc:
        raise _not_found(exc) from exc
    except ItemRuleError as exc:
        db.rollback()
        return _new_form(request, db, org, tenant, vars(new), str(exc), 400)
    db.commit()
    flash(request, f"{item.name} added.")
    return RedirectResponse(f"{BASE}/{item.id}", status_code=303)


# --- one item -----------------------------------------------------------------------------------


def _item_page(request, db, org, tenant, inventory_item_id: int, error=None, status=200):
    try:
        row = item_row(db, org, inventory_item_id)
    except NotFound as exc:
        raise _not_found(exc) from exc
    stocked = {place.location.id for place in row.places}
    return templates.TemplateResponse(
        request,
        "app/catalog_item.html",
        {
            "title": row.item.name,
            "tenant": tenant,
            "row": row,
            "item": row.item,
            "unit_label": UNITS[row.item.display_unit].label
            if row.item.display_unit in UNITS
            else row.item.display_unit,
            "codes": codes(db, org, row),
            "locked_dimension": has_history(db, org, row),
            "categories": categories(db, org),
            "type_labels": ITEM_TYPE_LABELS,
            "units": _units(),
            "elsewhere": [loc for loc in _locations(db, org) if loc.id not in stocked],
            "message": pop_flash(request),
            "error": error,
        },
        status_code=status,
    )


@router.get(BASE + "/{inventory_item_id}", response_class=HTMLResponse)
def item_page(
    request: Request,
    inventory_item_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("inventory.view")),
):
    return _item_page(request, db, org, tenant, inventory_item_id)


@router.post(BASE + "/{inventory_item_id}")
def save_item(
    request: Request,
    inventory_item_id: int,
    name: str = Form(""),
    item_type: str = Form("raw"),
    category: str = Form(""),
    display_unit: str = Form(""),
    notes: str = Form(""),
    shelf_life_days: str = Form(""),
    tracks_expiry: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("item.edit")),
):
    try:
        update_item(
            db,
            org,
            inventory_item_id,
            name=name,
            item_type=item_type,
            category=category,
            dimension=_dimension_of(display_unit),
            display_unit=display_unit,
            notes=notes,
            shelf_life_days=shelf_life_days,
            tracks_expiry={"yes": True, "no": False}.get(tracks_expiry),
            ip=client_ip(request),
        )
    except NotFound as exc:
        raise _not_found(exc) from exc
    except ItemRuleError as exc:
        db.rollback()
        return _item_page(request, db, org, tenant, inventory_item_id, str(exc), 400)
    db.commit()
    flash(request, "Saved.")
    return RedirectResponse(f"{BASE}/{inventory_item_id}", status_code=303)


@router.post(BASE + "/{inventory_item_id}/settings/{item_location_id}")
def save_levels(
    request: Request,
    inventory_item_id: int,
    item_location_id: int,
    reorder_point: str = Form("0"),
    par_level: str = Form(""),
    min_level: str = Form(""),
    storage_area: str = Form(""),
    stocked: str = Form("yes"),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("item.edit")),
):
    try:
        update_levels(
            db,
            org,
            inventory_item_id,
            item_location_id,
            reorder_point=reorder_point,
            par_level=par_level,
            min_level=min_level,
            storage_area=storage_area,
            active=stocked != "no",
            ip=client_ip(request),
        )
    except NotFound as exc:
        raise _not_found(exc) from exc
    except ItemRuleError as exc:
        db.rollback()
        return _item_page(request, db, org, tenant, inventory_item_id, str(exc), 400)
    db.commit()
    flash(request, "Levels saved.")
    return RedirectResponse(f"{BASE}/{inventory_item_id}", status_code=303)


def _activation(request, db, org, inventory_item_id: int, active: bool) -> RedirectResponse:
    try:
        item = set_active(db, org, inventory_item_id, active, ip=client_ip(request))
    except NotFound as exc:
        raise _not_found(exc) from exc
    db.commit()
    flash(
        request,
        f"{item.name} is back in use."
        if active
        else f"{item.name} is deactivated: its history stays, and it leaves pick lists.",
    )
    return RedirectResponse(f"{BASE}/{inventory_item_id}", status_code=303)


@router.post(BASE + "/{inventory_item_id}/deactivate")
def deactivate(
    request: Request,
    inventory_item_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("item.edit")),
):
    return _activation(request, db, org, inventory_item_id, False)


@router.post(BASE + "/{inventory_item_id}/activate")
def activate(
    request: Request,
    inventory_item_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("item.edit")),
):
    return _activation(request, db, org, inventory_item_id, True)


@router.post(BASE + "/{inventory_item_id}/stock-at")
def stock_elsewhere(
    request: Request,
    inventory_item_id: int,
    location_id: int = Form(...),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("item.edit")),
):
    try:
        stock_at(db, org, inventory_item_id, location_id, ip=client_ip(request))
    except NotFound as exc:
        raise _not_found(exc) from exc
    db.commit()
    flash(request, "Now stocked there, with none on hand: count it to set the quantity.")
    return RedirectResponse(f"{BASE}/{inventory_item_id}", status_code=303)
