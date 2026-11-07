"""Barcode scanning: the phone reads the code, the server decides what it means.

The page talks JSON to these routes so a scan never reloads the camera.
"""

from __future__ import annotations

import logging
from datetime import UTC, date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.access.permissions import VIEW_COSTS, without_costs
from app.config import get_settings
from app.db import get_db
from app.models import ProductIdentifier, StockItem, Tenant, TrackMode, User
from app.orchestration.scanning import (
    CONFIDENT_ENOUGH,
    CodeInUse,
    find_by_code,
    link_code,
    read_expiry_photo,
    receive_scanned,
    record_event,
    suspicious,
)
from app.orchestration.views import stock_views, tenant_currency, velocity_map
from app.security import (
    require_permission,
    require_tenant,
    require_user,
    require_writable,
)
from app.transform.barcodes import canonical
from app.transform.dates import prefer_for_currency
from app.transform.inventory import days_of_cover
from app.units import UnitError, format_amount, format_qty, from_base, to_base
from app.web.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/app/scan", response_class=HTMLResponse)
def scan_page(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("inventory.view")),
):
    return templates.TemplateResponse(
        request,
        "app/scan.html",
        {
            "title": "Scan",
            "tenant": tenant,
            # JSON-safe: the page needs only enough to offer a list to link a new code to.
            "item_options": [
                {"id": view.id, "name": view.name, "stock_unit": view.stock_unit}
                for view in stock_views(db, org)
            ],
        },
    )


def _item_payload(db: Session, org: OrgContext, item: StockItem, code: str, kind: str) -> dict:
    velocity = velocity_map(db, org).get(item.id, Decimal("0"))
    cover = days_of_cover(Decimal(item.qty_base or 0), velocity)
    from app.orchestration.scanning import location_name

    pack = Decimal(item.pack_size_base) if item.pack_size_base else None
    payload = {
        "found": True,
        "code": code,
        "identifier_type": kind,
        "item": {
            "id": item.id,
            "name": item.name,
            "unit": item.stock_unit,
            "dimension": item.dimension.value,
            "track_mode": item.track_mode.value,
            "qty": str(from_base(item.qty_base or 0, item.stock_unit)),
            "qty_label": format_qty(item.qty_base or 0, item.stock_unit),
            "reorder_label": format_qty(item.reorder_point_base or 0, item.stock_unit),
            "cover_days": str(cover) if cover is not None else None,
            "cost_cents": int(Decimal(item.cost_per_base_cents or 0)),
            "location": location_name(db, org, item),
            "pack_size": str(from_base(pack, item.stock_unit)) if pack else None,
            "receive_unit": item.receive_unit or "each",
        },
    }
    # Roles without cost access never receive cost fields (USER_ROLES_AND_PERMISSIONS §5).
    return payload if org.can(VIEW_COSTS) else without_costs(payload)


@router.get("/app/scan/lookup")
def lookup(
    code: str,
    symbology: str | None = None,
    refresh: bool = True,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("inventory.view")),
):
    """Resolve a scanned code against this tenant's own identifier table first."""
    value, kind = canonical(code, symbology)
    if not value:
        raise HTTPException(status_code=400, detail="no code was scanned")

    identifier = find_by_code(db, org, code, symbology)
    if identifier is None:
        return JSONResponse(
            {
                "found": False,
                "code": value,
                "identifier_type": kind,
                "misread": suspicious(code, symbology),
            }
        )

    item = get_scoped(db, org, StockItem, identifier.stock_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="stock item not found")

    # Square owns the quantity, so ask it for this one variation before showing a number.
    square_unreachable = False
    if refresh:
        from app.orchestration.sync import (
            NotConnected,
            record_square_failure,
            refresh_item_from_square,
        )

        try:
            refresh_item_from_square(db, org, item)
            db.commit()
        except NotConnected:
            pass
        except Exception as exc:
            db.rollback()
            log.exception("could not refresh item %s from square for tenant %s", item.id, tenant.id)
            record_square_failure(db, org, "item", exc)
            db.commit()
            square_unreachable = True

    payload = _item_payload(db, org, item, value, identifier.identifier_type.value)
    payload["square_unreachable"] = square_unreachable
    return JSONResponse(payload)


@router.post("/app/scan/link")
def link(
    code: str = Form(...),
    stock_item_id: int = Form(...),
    symbology: str | None = Form(None),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("item.edit")),
):
    """Teach the scanner a code it did not know."""
    item = get_scoped(db, org, StockItem, stock_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="stock item not found")
    try:
        identifier = link_code(db, org, item.id, code, symbology, source="scan")
    except CodeInUse as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return JSONResponse(
        _item_payload(db, org, item, identifier.value, identifier.identifier_type.value)
    )


def _quantity(raw: str, item: StockItem, as_packs: bool) -> Decimal:
    try:
        entered = Decimal((raw or "").strip())
    except InvalidOperation as exc:
        raise HTTPException(status_code=400, detail="enter a number") from exc
    if entered <= 0:
        raise HTTPException(status_code=400, detail="enter a quantity above zero")

    if as_packs:
        if not item.pack_size_base:
            raise HTTPException(status_code=400, detail=f"{item.name} has no pack size set")
        return (entered * Decimal(item.pack_size_base)).quantize(Decimal("0.0001"))
    try:
        return to_base(entered, item.stock_unit)
    except (UnitError, ArithmeticError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/app/scan/receive")
def receive(
    stock_item_id: int = Form(...),
    quantity: str = Form(...),
    as_packs: bool = Form(False),
    expires_on: str = Form(""),
    date_type: str = Form("unknown"),
    date_confidence: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    user: User = Depends(require_user),
    org: OrgContext = Depends(require_permission("receiving.post")),
):
    """Scan, then receive: stock rises, the lot is dated, Square gets the delta."""
    item = get_scoped(db, org, StockItem, stock_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="stock item not found")

    qty_base = _quantity(quantity, item, as_packs)
    expiry: date | None = None
    if expires_on.strip():
        try:
            expiry = date.fromisoformat(expires_on.strip())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="that is not a valid date") from exc

    try:
        confidence = Decimal(date_confidence) if date_confidence.strip() else None
    except InvalidOperation:
        confidence = None
    result = receive_scanned(
        db,
        item,
        qty_base,
        user_id=user.id,
        expires_on=expiry,
        date_type=date_type if expiry else "unknown",
        date_confidence=confidence if expiry else None,
    )
    db.commit()
    return JSONResponse(
        {
            "ok": True,
            "received": format_qty(qty_base, item.stock_unit),
            "received_text": format_amount(qty_base, item.stock_unit),
            "qty_label": format_qty(result["qty_after_base"], item.stock_unit),
            "qty_text": format_amount(result["qty_after_base"], item.stock_unit),
            "pos_tracked": item.track_mode is TrackMode.PROVIDER,
            "dated": result["dated"],
        }
    )


@router.post("/app/scan/count")
def count(
    stock_item_id: int = Form(...),
    quantity: str = Form(...),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    user: User = Depends(require_user),
    org: OrgContext = Depends(require_permission("count.record")),
):
    """Correct the count from the shelf. Nothing is sent to Square (C-34)."""
    from app.models import InventoryCount

    item = get_scoped(db, org, StockItem, stock_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="stock item not found")

    qty_base = _quantity(quantity, item, as_packs=False)
    before = Decimal(item.qty_base or 0)
    item.qty_base = qty_base
    from datetime import datetime

    now = datetime.now(UTC)
    item.last_counted_at = now
    db.add(
        InventoryCount(
            tenant_id=tenant.id,
            stock_item_id=item.id,
            qty_base=qty_base,
            source="scan",
            note="counted by barcode scan",
            counted_at=now,
        )
    )
    db.flush()

    record_event(
        db,
        item,
        action="counted",
        source="barcode_scan",
        qty_after_base=qty_base,
        delta_base=qty_base - before,
        user_id=user.id,
    )
    db.commit()
    return JSONResponse(
        {
            "ok": True,
            "qty_label": format_qty(qty_base, item.stock_unit),
            "qty_text": format_amount(qty_base, item.stock_unit),
            "pos_tracked": item.track_mode is TrackMode.PROVIDER,
        }
    )


@router.get("/app/scan/codes/{stock_item_id}")
def codes_for_item(
    stock_item_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("inventory.view")),
):
    item = get_scoped(db, org, StockItem, stock_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="stock item not found")
    rows = db.scalars(
        select_scoped(org, ProductIdentifier).where(
            ProductIdentifier.stock_item_id == stock_item_id,
        )
    )
    return JSONResponse(
        {
            "codes": [
                {"value": r.value, "type": r.identifier_type.value, "source": r.source}
                for r in rows
            ]
        }
    )


def _photo_limit() -> int:
    """The same limit as an invoice photo: the image is shrunk before it is sent anywhere."""
    return get_settings().max_upload_mb * 1024 * 1024


@router.post("/app/scan/expiry")
def scan_expiry(
    photo: UploadFile = File(...),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("count.record")),
):
    """Read the date off a photo of the packaging. The product is already known, so this
    only has to answer 'which date', which is a far smaller question than 'which product'."""
    image = photo.file.read()
    if not image:
        raise HTTPException(status_code=400, detail="the photo was empty")
    if len(image) > _photo_limit():
        raise HTTPException(
            status_code=400,
            detail=f"that photo is larger than the {get_settings().max_upload_mb} MB limit",
        )

    reading = read_expiry_photo(
        image,
        photo.content_type or "image/jpeg",
        prefer=prefer_for_currency(tenant_currency(db, org)),
    )
    return JSONResponse(
        {
            "found": reading.value is not None,
            "date": reading.value.isoformat() if reading.value else None,
            "date_type": reading.date_type,
            "is_expiry": reading.is_expiry,
            "confidence": float(reading.confidence),
            "threshold": float(CONFIDENT_ENOUGH),
            "needs_confirmation": reading.needs_confirmation,
            "raw_text": reading.raw_text,
            "note": reading.note,
        }
    )
