"""Send a signed Square webhook at the running app, the way Square would.

Square's own "Send test event" button proves the URL is reachable. This proves the rest: that the
signature is verified, that a forged one is refused, and that a real inventory event moves stock.

    python scripts/send_test_webhook.py                  # signature check, both directions
    python scripts/send_test_webhook.py --count 19       # a sale: set a variation to 19 on hand
    python scripts/send_test_webhook.py --variation ABC  # which variation to move

Development tooling: nothing in app/ imports it.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.providers.square.webhooks import signature_for  # noqa: E402

PATH = "/webhooks/square"


def env_value(key: str) -> str:
    path = Path(__file__).resolve().parent.parent / ".env"
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def inventory_event(merchant: str, variation: str, location: str, quantity: str) -> dict:
    """The shape Square sends when a till changes a count."""
    return {
        "merchant_id": merchant,
        "type": "inventory.count.updated",
        "event_id": f"test-{datetime.now(UTC).timestamp()}",
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "data": {
            "type": "inventory.count",
            "object": {
                "inventory_counts": [
                    {
                        "catalog_object_id": variation,
                        "catalog_object_type": "ITEM_VARIATION",
                        "location_id": location,
                        "state": "IN_STOCK",
                        "quantity": quantity,
                        "calculated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    }
                ]
            },
        },
    }


def post(url: str, body: bytes, signature: str) -> httpx.Response:
    return httpx.post(
        url,
        content=body,
        timeout=30.0,
        headers={
            "Content-Type": "application/json",
            "x-square-hmacsha256-signature": signature,
            # ngrok's browser warning page otherwise intercepts plain requests.
            "ngrok-skip-browser-warning": "1",
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--variation", default="TEST-VARIATION", help="catalog variation id to move"
    )
    parser.add_argument("--location", default="TEST-LOCATION", help="Square location id")
    parser.add_argument("--merchant", default="", help="merchant id; defaults to the connected one")
    parser.add_argument("--count", default="19", help="new quantity on hand")
    args = parser.parse_args()

    settings = get_settings()
    key = settings.square_webhook_signature_key or env_value("SQUARE_WEBHOOK_SIGNATURE_KEY")
    if not key:
        raise SystemExit(
            "SQUARE_WEBHOOK_SIGNATURE_KEY is not set; the endpoint would refuse everything"
        )

    url = settings.app_base_url.rstrip("/") + PATH
    print(f"posting to {url}\n")

    event = inventory_event(
        args.merchant or "TEST-MERCHANT", args.variation, args.location, args.count
    )
    body = json.dumps(event).encode()

    # 1. A forged signature must be refused before anything is read.
    forged = post(url, body, "not-a-real-signature")
    print(f"  forged signature  -> {forged.status_code} {forged.text[:120]}")

    # 2. A real one, signed the way Square signs: the notification URL plus the raw body.
    genuine = post(url, body, signature_for(url, body, key))
    print(f"  genuine signature -> {genuine.status_code} {genuine.text[:200]}")

    print()
    if forged.status_code == 400 and genuine.status_code == 200:
        print("Signature verification works in both directions.")
        if '"ignored"' in genuine.text:
            print("The event was dropped because no connected seller matches it, which is correct")
            print(
                "until a sandbox seller is connected. Rerun with --merchant/--variation/--location"
            )
            print("from a real connection to move actual stock.")
        return 0
    if genuine.status_code == 503:
        print("503: the app has no signature key loaded. Restart it after editing .env.")
    elif genuine.status_code == 400:
        print("400 on a genuine signature: APP_BASE_URL does not match the URL Square signs.")
        print(f"It must be exactly the notification URL, no trailing slash. Currently: {url}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
