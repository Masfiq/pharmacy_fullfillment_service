"""Download a real drug catalog snapshot from the FDA NDC Directory (openFDA).

The FDA NDC Directory is public data about drug products: NDC codes, names,
strengths, and dosage forms. It contains no patient information.

This script queries openFDA for a list of commonly dispensed maintenance
medications, normalizes each product into the fields our Drug model needs, and
writes ``data/drugs_openfda.csv``. The CSV is committed to the repo so that
seeding works offline and is reproducible. Re-run this script to refresh it.

Usage:
    python scripts/fetch_openfda_drugs.py
"""

from __future__ import annotations

import csv
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.domain.enums import DosageForm  # noqa: E402
from app.domain.normalize import normalize_dosage_form, normalize_strength  # noqa: E402

OPENFDA_URL = "https://api.fda.gov/drug/ndc.json"
OUTPUT = ROOT / "data" / "drugs_openfda.csv"
PRODUCTS_PER_GENERIC = 2
MAX_ATTEMPTS = 4

# Commonly dispensed outpatient medications, the kind of mail order and
# central fill volume a high throughput pharmacy handles. Controlled
# substances are intentionally left out.
GENERIC_NAMES: list[str] = [
    "atorvastatin",
    "lisinopril",
    "levothyroxine",
    "metformin",
    "amlodipine",
    "metoprolol",
    "omeprazole",
    "simvastatin",
    "losartan",
    "albuterol",
    "gabapentin",
    "hydrochlorothiazide",
    "sertraline",
    "montelukast",
    "fluticasone",
    "amoxicillin",
    "furosemide",
    "pantoprazole",
    "escitalopram",
    "prednisone",
    "bupropion",
    "tamsulosin",
    "rosuvastatin",
    "meloxicam",
    "clopidogrel",
    "citalopram",
    "carvedilol",
    "trazodone",
    "pravastatin",
    "warfarin",
    "fluoxetine",
    "atenolol",
    "allopurinol",
    "glipizide",
    "spironolactone",
    "venlafaxine",
    "ondansetron",
    "cephalexin",
    "azithromycin",
    "valsartan",
    "finasteride",
    "doxycycline",
    "lamotrigine",
    "topiramate",
    "duloxetine",
    "famotidine",
    "insulin glargine",
    "enoxaparin",
    "semaglutide",
    "triamcinolone",
    "mupirocin",
    "clotrimazole",
    "budesonide",
    "latanoprost",
    "hydroxyzine",
    "methotrexate",
    "sumatriptan",
    "nitrofurantoin",
    "sildenafil",
    "cetirizine",
    "ipratropium",
    "lidocaine",
    "ketoconazole",
    "timolol",
    "dulaglutide",
]

FIELDS = [
    "ndc",
    "name",
    "strength",
    "dosage_form",
    "unit_type",
    "fda_dosage_form",
    "brand_name",
    "labeler",
    "product_ndc",
]


def query(generic: str) -> list[dict[str, object]]:
    search = (
        f'generic_name:"{generic}" AND finished:true AND product_type:"HUMAN PRESCRIPTION DRUG"'
    )
    url = f"{OPENFDA_URL}?{urllib.parse.urlencode({'search': search, 'limit': 25})}"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                payload = json.load(response)
            results: list[dict[str, object]] = payload.get("results", [])
            return results
        except urllib.error.HTTPError as exc:
            if exc.code == 404:  # openFDA returns 404 when nothing matches
                return []
            if attempt == MAX_ATTEMPTS:
                raise
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            if attempt == MAX_ATTEMPTS:
                raise
        time.sleep(2**attempt)  # back off: 2s, 4s, 8s
    return []


def to_row(product: dict[str, object]) -> dict[str, str] | None:
    form = normalize_dosage_form(str(product.get("dosage_form", "")))
    if form is DosageForm.OTHER:
        return None
    packaging = product.get("packaging") or []
    ingredients = product.get("active_ingredients") or []
    if not isinstance(packaging, list) or not packaging or not isinstance(ingredients, list):
        return None
    package = next((p for p in packaging if not p.get("sample")), None)
    if package is None:
        return None
    strengths = [str(i.get("strength", "")) for i in ingredients]
    return {
        "ndc": str(package["package_ndc"]),
        "name": str(product.get("generic_name", "")).strip().title(),
        "strength": normalize_strength(strengths),
        "dosage_form": form.value,
        "unit_type": form.unit_type.value,
        "fda_dosage_form": str(product.get("dosage_form", "")),
        "brand_name": str(product.get("brand_name", "")),
        "labeler": str(product.get("labeler_name", "")),
        "product_ndc": str(product.get("product_ndc", "")),
    }


def main() -> None:
    rows: list[dict[str, str]] = []
    seen_ndcs: set[str] = set()
    for generic in GENERIC_NAMES:
        picked: list[dict[str, str]] = []
        seen_strengths: set[tuple[str, str]] = set()
        for product in query(generic):
            row = to_row(product)
            if row is None or row["ndc"] in seen_ndcs:
                continue
            key = (row["strength"], row["dosage_form"])
            if key in seen_strengths:
                continue
            seen_strengths.add(key)
            seen_ndcs.add(row["ndc"])
            picked.append(row)
            if len(picked) == PRODUCTS_PER_GENERIC:
                break
        print(f"{generic:20s} {len(picked)} product(s)")
        rows.extend(picked)
        time.sleep(0.3)  # stay well under the anonymous openFDA rate limit

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: (r["name"], r["ndc"])))
    print(f"Wrote {len(rows)} drugs to {OUTPUT}")


if __name__ == "__main__":
    main()
