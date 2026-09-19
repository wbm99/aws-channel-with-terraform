#!/usr/bin/env python3
"""Print on-demand prices from the AWS Price List API for one service in us-east-1.

Usage: scripts/price_lookup.py SERVICE_CODE REGEX
Example: scripts/price_lookup.py AWSElementalMediaLive "Single Pipeline HD AVC"
"""

import json
import re
import subprocess
import sys


def get_products(service_code: str):
    """Yield every price-list entry; the AWS CLI follows the pagination tokens itself."""
    command = [
        "aws", "pricing", "get-products", "--region", "us-east-1",
        "--service-code", service_code,
        "--filters", "Type=TERM_MATCH,Field=regionCode,Value=us-east-1",
        "--output", "json",
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    yield from json.loads(result.stdout).get("PriceList", [])


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    service_code, pattern = sys.argv[1], re.compile(sys.argv[2], re.IGNORECASE)
    rows = set()
    for item in get_products(service_code):
        product = json.loads(item) if isinstance(item, str) else item
        attributes = product["product"]["attributes"]
        for term in product["terms"].get("OnDemand", {}).values():
            for dimension in term["priceDimensions"].values():
                text = f"{dimension['description']} {attributes.get('usagetype', '')}"
                if pattern.search(text):
                    rows.add((attributes.get("usagetype"), attributes.get("operation"),
                              dimension["description"][:120], dimension["pricePerUnit"].get("USD")))
    for row in sorted(rows, key=lambda r: (str(r[0]), str(r[2]))):
        print(row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
