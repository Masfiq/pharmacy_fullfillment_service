"""Map raw FDA NDC Directory values onto the service's simpler domain values."""

from __future__ import annotations

from collections.abc import Iterable

from app.domain.enums import DosageForm

# Checked in order: the first keyword found wins. Injectables come first so that
# "INJECTION, SOLUTION" is not classified as an oral liquid.
_FORM_KEYWORDS: list[tuple[tuple[str, ...], DosageForm]] = [
    (("INJECTION", "INJECTABLE"), DosageForm.INJECTABLE),
    (("AEROSOL", "INHAL", "SPRAY", "POWDER, METERED"), DosageForm.INHALER),
    (("CREAM", "OINTMENT", "GEL", "LOTION", "PATCH", "SHAMPOO"), DosageForm.TOPICAL),
    (("TABLET",), DosageForm.TABLET),
    (("CAPSULE",), DosageForm.CAPSULE),
    (("SOLUTION", "SUSPENSION", "SYRUP", "ELIXIR", "LIQUID", "DROPS"), DosageForm.LIQUID),
]


def normalize_dosage_form(fda_form: str) -> DosageForm:
    upper = fda_form.upper()
    for keywords, form in _FORM_KEYWORDS:
        if any(keyword in upper for keyword in keywords):
            return form
    return DosageForm.OTHER


def normalize_strength(strengths: Iterable[str]) -> str:
    """Turn openFDA strengths like ``["25 mg/1", "5 mg/1"]`` into ``"25 mg / 5 mg"``.

    openFDA writes per unit strengths with a ``/1`` suffix; that suffix adds
    nothing for display so it is dropped. Other denominators (``mg/mL``) stay.
    """
    cleaned = [s.strip().removesuffix("/1").strip() for s in strengths if s and s.strip()]
    return " / ".join(cleaned)
