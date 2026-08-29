from __future__ import annotations

from typing import Any

from rental_manager.models import Apartment, PaymentProfile


PAYMENT_PROFILE_FIELDS = (
    "ip_recipient_name",
    "ip_recipient_inn",
    "ip_recipient_ogrnip",
    "ip_recipient_account",
    "ip_recipient_bank",
    "ip_recipient_bik",
    "ip_recipient_correspondent_account",
    "ip_recipient_bank_inn",
    "ip_recipient_bank_kpp",
    "personal_recipient_name",
    "personal_recipient_phone",
    "personal_recipient_bank",
)


def payment_profile_values(profile: PaymentProfile) -> dict[str, str]:
    return {field: str(getattr(profile, field) or "") for field in PAYMENT_PROFILE_FIELDS}


def apply_payment_profile_payload(profile: PaymentProfile, payload: dict[str, Any]) -> None:
    if "name" in payload:
        profile.name = str(payload.get("name") or "").strip()
    for field in PAYMENT_PROFILE_FIELDS:
        if field in payload:
            setattr(profile, field, str(payload.get(field) or "").strip())
    if "notes" in payload:
        profile.notes = str(payload.get("notes") or "").strip()
    if "active" in payload:
        profile.active = payload.get("active") not in {False, "false", "0", 0, None}


def effective_payment_profile(apartment: Apartment) -> tuple[PaymentProfile | None, str]:
    if apartment.payment_profile is not None:
        return apartment.payment_profile, "apartment"
    if apartment.object is not None and apartment.object.payment_profile is not None:
        return apartment.object.payment_profile, "object"
    return None, "global"


def effective_payment_settings(apartment: Apartment, global_settings: dict[str, Any]) -> dict[str, Any]:
    profile, _source = effective_payment_profile(apartment)
    if profile is None:
        return {field: global_settings.get(field, "") for field in PAYMENT_PROFILE_FIELDS}
    return payment_profile_values(profile)


def effective_payment_profile_summary(apartment: Apartment) -> dict[str, Any]:
    profile, source = effective_payment_profile(apartment)
    if profile is None:
        return {
            "id": None,
            "name": "Глобальные реквизиты",
            "source": source,
            "active": True,
        }
    return {
        "id": profile.id,
        "name": profile.name,
        "source": source,
        "active": profile.active,
    }


def serialize_payment_profile(profile: PaymentProfile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "name": profile.name,
        **payment_profile_values(profile),
        "notes": profile.notes,
        "active": profile.active,
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        "object_ids": sorted(item.id for item in profile.objects),
        "apartment_ids": sorted(item.id for item in profile.apartments),
    }
