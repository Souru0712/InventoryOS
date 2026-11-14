"""What a cost change touches (T2.03; RCP-FR-13, AC-RCP-07): /app/catalog/{id}/recipes.

For one item at one location: its latest cost change, and every recipe that uses it, directly or
through a prepared item, with the recipe's cost before and after and its estimated margin. Only a
role with cost access sees it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.access.permissions import VIEW_COSTS
from app.costing import costing_location, impact
from app.db import get_db
from app.models import Location, Tenant
from app.recipes import owner_name
from app.security import location_filter, require_permission, require_tenant
from app.web.templating import templates

router = APIRouter()


@router.get("/app/catalog/{inventory_item_id}/recipes", response_class=HTMLResponse)
def recipes_using_item(
    request: Request,
    inventory_item_id: int,
    location: int | None = Depends(location_filter),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission(VIEW_COSTS)),
):
    place = costing_location(db, org, location)
    if place is None:
        raise HTTPException(status_code=404, detail="no location")
    try:
        found = impact(db, org, inventory_item_id, place)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="item not found") from exc
    places = list(
        db.scalars(
            select_scoped(org, Location).where(Location.is_active.is_(True)).order_by(Location.name)
        )
    )
    return templates.TemplateResponse(
        request,
        "app/item_recipes.html",
        {
            "title": f"Recipes using {found.item.name}",
            "tenant": tenant,
            "impact": found,
            "names": {row.recipe.id: owner_name(db, org, row.owner) for row in found.rows},
            "places": places,
            "currency": place.currency,
        },
    )
