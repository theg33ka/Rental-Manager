from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from rental_manager.models import Apartment, Meter, RentalObject, Tariff, Tenant, UtilityService


def seed_if_empty(session: Session) -> None:
    has_objects = session.scalar(select(RentalObject).limit(1))
    if has_objects:
        return

    objects = [
        ("Белый дом", "БД", ["БД1", "БД2", "БД3", "БД4"], ["electricity"]),
        ("Чёрный дом", "ЧД", ["ЧД1", "ЧД2", "ЧД3", "ЧД4"], ["electricity"]),
        ("Баня", "БН", ["Баня 1", "Баня 2", "Баня 3", "Баня 4"], ["electricity", "water", "gas"]),
    ]

    service_names = {
        "electricity": "Электричество",
        "water": "Холодная вода",
        "gas": "Газ",
    }

    for object_name, short_code, apartment_names, services in objects:
        rental_object = RentalObject(name=object_name, short_code=short_code)
        session.add(rental_object)
        session.flush()

        apartments = []
        for index, apartment_name in enumerate(apartment_names, start=1):
            apartment = Apartment(
                object_id=rental_object.id,
                name=apartment_name,
                sort_order=index,
                odn_share_percent=25,
            )
            session.add(apartment)
            apartments.append(apartment)
        session.flush()

        for kind in services:
            service = UtilityService(
                object_id=rental_object.id,
                kind=kind,
                name=service_names[kind],
                provider_reading_due_day=20,
                provider_due_day=24,
                resident_due_days=7,
            )
            session.add(service)
            session.flush()
            session.add(
                Meter(
                    service_id=service.id,
                    object_id=rental_object.id,
                    scope="object",
                    name=f"{object_name}: общий {service_names[kind].lower()}",
                )
            )

            if kind == "electricity":
                for apartment in apartments:
                    session.add(
                        Meter(
                            service_id=service.id,
                            object_id=rental_object.id,
                            apartment_id=apartment.id,
                            scope="apartment",
                            name=f"{apartment.name}: {service_names[kind].lower()}",
                        )
                    )

            tiers = [{"limit": None, "price": 1.0}]
            if kind == "electricity":
                if rental_object.name == "Баня":
                    tiers = [{"limit": None, "price": 7.4}]
                else:
                    tiers = [
                        {"limit": 3900, "price": 4.18},
                        {"limit": 6000, "price": 6.01},
                        {"limit": None, "price": 7.48},
                    ]
            session.add(
                Tariff(
                    service_id=service.id,
                    starts_on=date(2026, 1, 1),
                    name="Стартовый тариф",
                    tiers_json=json.dumps(tiers, ensure_ascii=False),
                )
            )

    session.commit()


def seed_release_baseline_if_empty(session: Session) -> bool:
    has_tenants = session.scalar(select(Tenant.id).limit(1))
    if has_tenants:
        return False

    seed_if_empty(session)

    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    import seed_from_screenshots as legacy_seed  # type: ignore

    legacy_seed.clear_demo_data(session)
    legacy_seed.clear_screenshot_data(session)
    legacy_seed.seed_tariffs(session)
    leases = legacy_seed.seed_leases(session)
    legacy_seed.generate_rent_charges(session, until=date(2026, 5, 31))
    legacy_seed.mark_old_rent_paid(session)
    legacy_seed.seed_rent_payments(session, leases)
    legacy_seed.seed_utility_payments(session, leases)
    legacy_seed.seed_meter_readings(session)
    legacy_seed.seed_expenses(session)
    return True
