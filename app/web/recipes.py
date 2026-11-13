"""Recipe screens (T2.02-b; RCP-FR-02, -05..09, -14, AC-RCP-01, -06, -09): the staff recipe
card and the editor, at /app/recipes/{kind}/{owner_id} for a size ("variation"), an add modifier
("modifier") or a prepared item ("item").

Anyone who can read recipes sees the card, which carries no cost at all (AC-RCP-09); saving takes
recipe.edit, and production_recipe.edit for a prepared item, under the write rule. A save is the
next version (app/recipes.py); the past keeps the version it used.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.access import OrgContext, get_scoped, select_scoped
from app.access.scoped import members
from app.db import get_db
from app.models import (
    InventoryItem,
    ItemPackage,
    Location,
    ProductVariation,
    Recipe,
    RecipeVersion,
    Tenant,
)
from app.recipes import (
    FROM_THE_START,
    NewLine,
    NotFound,
    Owner,
    effective_version,
    owner_name,
    recipe_of,
    save_recipe,
    start_from,
    versions,
)
from app.security import require_permission, require_tenant, require_writable
from app.throttle import client_ip
from app.transform.recipes import OWNER_KINDS, Line, RecipeRuleError, what_changed
from app.units import UNITS
from app.web.flash import flash, pop_flash
from app.web.inputs import posted_form
from app.web.templating import templates

router = APIRouter()

BASE = "/app/recipes"
BLANK_ROWS = 3  # empty lines under the current ones; save and edit again for more
KIND_LABELS = {"variation": "Recipe", "modifier": "Add-in recipe", "item": "Production recipe"}


def _owner(kind: str, owner_id: int) -> Owner:
    if kind not in OWNER_KINDS:
        raise HTTPException(status_code=404, detail="not found")
    return Owner(kind, owner_id)


def _zone(db: Session, org: OrgContext) -> ZoneInfo:
    """The business's own clock, for showing and entering when a version starts."""
    place = db.scalars(
        select_scoped(org, Location).where(Location.is_active.is_(True)).order_by(Location.id)
    ).first()
    return ZoneInfo(place.timezone if place else "UTC")


def _back(db: Session, org: OrgContext, owner: Owner) -> tuple[str, str]:
    if owner.kind == "variation":
        variation = get_scoped(db, org, ProductVariation, owner.id)
        if variation is not None:
            return f"/app/products/{variation.product_id}", variation.product.name
    if owner.kind == "modifier":
        return "/app/products#modifiers", "Modifiers"
    return f"/app/catalog/{owner.id}", "Item"


def _lines(version: RecipeVersion) -> list[Line]:
    return [
        Line(
            ln.inventory_item_id,
            Decimal(ln.quantity),
            ln.unit,
            Decimal(ln.qty_base),
            ln.is_packaging,
        )
        for ln in version.lines
    ]


def _yield_text(version: RecipeVersion) -> str:
    amount = f"{Decimal(version.yield_quantity).normalize():f}"
    if version.yield_unit == "serving":
        return f"{amount} serving{'' if amount == '1' else 's'}"
    return f"{amount} {version.yield_unit}"


def _history(db: Session, org: OrgContext, recipe: Recipe) -> list[dict]:
    rows = list(reversed(versions(db, org, recipe)))  # oldest first, to compare each with before
    names = {
        i.id: i.name
        for i in db.scalars(select_scoped(org, InventoryItem))
        if any(ln.inventory_item_id == i.id for v in rows for ln in v.lines)
    }
    people = {u.id: u.email for u in members(db, org)}
    entries = []
    for i, version in enumerate(rows):
        before = rows[i - 1] if i else None
        entries.append(
            {
                "version": version,
                "who": people.get(version.created_by) if version.created_by else None,
                "changes": what_changed(
                    _lines(before) if before else None,
                    _lines(version),
                    names,
                    (_yield_text(before), _yield_text(version)) if before else ("", ""),
                    bool(before and (before.instructions or "") != (version.instructions or "")),
                ),
            }
        )
    return list(reversed(entries))


@router.get(BASE + "/{kind}/{owner_id}", response_class=HTMLResponse)
def recipe_card(
    request: Request,
    kind: str,
    owner_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("recipe.view")),
):
    owner = _owner(kind, owner_id)
    try:
        name = owner_name(db, org, owner)
    except (NotFound, RecipeRuleError) as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    recipe = recipe_of(db, org, owner)
    now = datetime.now(UTC)
    current = effective_version(db, org, recipe, now) if recipe else None
    upcoming = (
        sorted(
            (v for v in versions(db, org, recipe) if v.effective_from > now),
            key=lambda v: v.effective_from,
        )
        if recipe
        else []
    )
    back, back_label = _back(db, org, owner)
    return templates.TemplateResponse(
        request,
        "app/recipe.html",
        {
            "title": f"{KIND_LABELS[kind]}: {name}",
            "tenant": tenant,
            "message": pop_flash(request),
            "kind": kind,
            "kind_label": KIND_LABELS[kind],
            "owner": owner,
            "name": name,
            "current": current,
            "upcoming": upcoming,
            "history": _history(db, org, recipe) if recipe else [],
            "yield_text": _yield_text,
            "zone": _zone(db, org),
            "from_the_start": FROM_THE_START,
            "back": back,
            "back_label": back_label,
            "can_edit": org.can("recipe.edit")
            and (kind != "item" or org.can("production_recipe.edit")),
        },
    )


def _sizes_to_start_from(db: Session, org: OrgContext, owner: Owner) -> list[ProductVariation]:
    if owner.kind != "variation":
        return []
    variation = get_scoped(db, org, ProductVariation, owner.id)
    if variation is None:
        return []
    return [
        other
        for other in variation.product.variations
        if other.id != variation.id and recipe_of(db, org, Owner("variation", other.id))
    ]


def _editor(
    request: Request,
    db: Session,
    org: OrgContext,
    tenant: Tenant,
    owner: Owner,
    *,
    rows: list[dict],
    yield_quantity: str,
    yield_unit: str,
    instructions: str,
    starts_at: str = "",
    error: str | None = None,
    started_from: str | None = None,
):
    name = owner_name(db, org, owner)
    items = sorted(
        db.scalars(select_scoped(org, InventoryItem).where(InventoryItem.is_active.is_(True))),
        key=lambda i: i.name.casefold(),
    )
    packages = sorted({p.name for p in db.scalars(select_scoped(org, ItemPackage))})
    back, back_label = _back(db, org, owner)
    response = templates.TemplateResponse(
        request,
        "app/recipe_edit.html",
        {
            "title": f"Edit {KIND_LABELS[owner.kind].lower()}: {name}",
            "tenant": tenant,
            "kind": owner.kind,
            "kind_label": KIND_LABELS[owner.kind],
            "owner": owner,
            "name": name,
            "items": items,
            "unit_choices": [*UNITS, *packages],
            "rows": rows + [{} for _ in range(BLANK_ROWS)],
            "yield_quantity": yield_quantity,
            "yield_unit": yield_unit,
            "instructions": instructions,
            "starts_at": starts_at,
            "error": error,
            "started_from": started_from,
            "start_from": _sizes_to_start_from(db, org, owner),
            "back": back,
            "back_label": back_label,
        },
    )
    if error:
        response.status_code = 400
    return response


def _require_edit(org: OrgContext, owner: Owner) -> None:
    if owner.kind == "item" and not org.can("production_recipe.edit"):
        raise HTTPException(status_code=403, detail="not allowed")


@router.get(BASE + "/{kind}/{owner_id}/edit", response_class=HTMLResponse)
def edit_recipe(
    request: Request,
    kind: str,
    owner_id: int,
    start: int | None = None,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("recipe.edit")),
):
    owner = _owner(kind, owner_id)
    _require_edit(org, owner)
    try:
        owner_name(db, org, owner)
    except (NotFound, RecipeRuleError) as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    recipe = recipe_of(db, org, owner)
    current = effective_version(db, org, recipe) if recipe else None
    rows = [
        {
            "item": ln.inventory_item_id,
            "quantity": f"{Decimal(ln.quantity).normalize():f}",
            "unit": ln.unit,
            "packaging": ln.is_packaging,
        }
        for ln in (current.lines if current else [])
    ]
    started_from = None
    instructions = (current.instructions or "") if current else ""
    if start is not None and kind == "variation":
        try:
            filled = start_from(db, org, start, owner_id)
        except (NotFound, RecipeRuleError) as exc:
            raise HTTPException(status_code=404, detail="not found") from exc
        rows = [
            {
                "item": n.inventory_item_id,
                "quantity": n.quantity,
                "unit": n.unit,
                "packaging": bool(n.packaging),
            }
            for n in filled
        ]
        source = get_scoped(db, org, ProductVariation, start)
        started_from = source.name if source else None
        source_recipe = recipe_of(db, org, Owner("variation", start))
        source_version = effective_version(db, org, source_recipe) if source_recipe else None
        instructions = (source_version.instructions or "") if source_version else instructions
    return _editor(
        request,
        db,
        org,
        tenant,
        owner,
        rows=rows,
        yield_quantity=f"{Decimal(current.yield_quantity).normalize():f}" if current else "1",
        yield_unit=current.yield_unit if current and kind == "item" else "",
        instructions=instructions,
        started_from=started_from,
    )


def _starts(raw: str, zone: ZoneInfo) -> datetime | None:
    if not raw.strip():
        return None
    try:
        local = datetime.fromisoformat(raw.strip())
    except ValueError as exc:
        raise RecipeRuleError("A start is a date and time.") from exc
    return local.replace(tzinfo=zone).astimezone(UTC)


@router.post(BASE + "/{kind}/{owner_id}")
def save(
    request: Request,
    kind: str,
    owner_id: int,
    form: FormData = Depends(posted_form),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("recipe.edit")),
):
    owner = _owner(kind, owner_id)
    _require_edit(org, owner)
    item_ids = [str(v) for v in form.getlist("item")]
    quantities = [str(v) for v in form.getlist("quantity")]
    units = [str(v) for v in form.getlist("unit")]
    packaging = {str(v) for v in form.getlist("packaging")}
    rows: list[dict] = []
    lines: list[NewLine] = []
    for index, raw_item in enumerate(item_ids):
        quantity = quantities[index] if index < len(quantities) else ""
        unit = units[index] if index < len(units) else ""
        if not raw_item.strip() and not quantity.strip():
            continue  # an empty row
        packed = str(index) in packaging
        rows.append(
            {
                "item": int(raw_item) if raw_item.isdigit() else None,
                "quantity": quantity,
                "unit": unit,
                "packaging": packed,
            }
        )
        if not raw_item.isdigit():
            return _rerender(request, db, org, tenant, owner, form, rows, "Choose an item.")
        lines.append(NewLine(int(raw_item), quantity, unit, True if packed else None))
    try:
        starts = _starts(str(form.get("starts_at", "")), _zone(db, org))
        version = save_recipe(
            db,
            org,
            owner,
            lines,
            yield_quantity=str(form.get("yield_quantity", "1")),
            yield_unit=str(form.get("yield_unit", "")),
            instructions=str(form.get("instructions", "")),
            effective_from=starts,
            ip=client_ip(request),
        )
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    except RecipeRuleError as exc:
        db.rollback()
        return _rerender(request, db, org, tenant, owner, form, rows, str(exc))
    db.commit()
    if version is None:
        flash(request, "Nothing changed, so no new version.")
    elif starts is None:
        flash(request, f"Saved version {version.version_no}. Earlier sales keep their version.")
    else:
        flash(request, f"Saved version {version.version_no}; it starts at the time you chose.")
    return RedirectResponse(f"{BASE}/{kind}/{owner_id}", status_code=303)


def _rerender(
    request: Request,
    db: Session,
    org: OrgContext,
    tenant: Tenant,
    owner: Owner,
    form: FormData,
    rows: list[dict],
    error: str,
):
    return _editor(
        request,
        db,
        org,
        tenant,
        owner,
        rows=rows,
        yield_quantity=str(form.get("yield_quantity", "1")),
        yield_unit=str(form.get("yield_unit", "")),
        instructions=str(form.get("instructions", "")),
        starts_at=str(form.get("starts_at", "")),
        error=error,
    )
