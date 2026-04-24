$ErrorActionPreference = "Stop"

.\.venv\Scripts\python.exe -m compileall rental_manager scripts tests
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

Remove-Item -LiteralPath data\test_validation.db -Force -ErrorAction SilentlyContinue

@'
import os
import sys
from datetime import date

os.environ["RENTAL_MANAGER_DATABASE_URL"] = "sqlite:///data/test_validation.db"

from rental_manager.database import engine, init_db, SessionLocal
from rental_manager.models import Apartment, Lease, Meter, MeterReading, RentalObject, Tenant, UtilityService
from rental_manager.services.billing import calculate_utility_bill, generate_rent_charges
from rental_manager.services.seed import seed_if_empty
from sqlalchemy import select

init_db()
with SessionLocal() as session:
    seed_if_empty(session)
    obj = session.get(RentalObject, 1)
    apartment = session.scalar(select(Apartment).where(Apartment.object_id == obj.id).order_by(Apartment.sort_order).limit(1))
    tenant = Tenant(full_name="Test Tenant", phone="+70000000000")
    session.add(tenant)
    session.flush()
    lease = Lease(
        apartment_id=apartment.id,
        tenant_id=tenant.id,
        start_date=date(2026, 4, 14),
        payment_day=14,
        ip_amount=20000,
        personal_amount=5000,
    )
    session.add(lease)
    session.flush()
    created = generate_rent_charges(session, until=date(2026, 6, 1))
    assert created == 2, created

    service = session.scalar(select(UtilityService).where(UtilityService.object_id == obj.id, UtilityService.kind == "electricity"))
    meters = session.scalars(select(Meter).where(Meter.service_id == service.id)).all()
    for meter in meters:
        start_value = 1000 if meter.scope == "object" else 10
        end_value = 2200 if meter.scope == "object" else 110
        session.add(MeterReading(meter_id=meter.id, reading_date=date(2026, 4, 1), value=start_value))
        session.add(MeterReading(meter_id=meter.id, reading_date=date(2026, 5, 1), value=end_value))
    session.flush()
    bill, warnings = calculate_utility_bill(session, service.id, date(2026, 4, 1), date(2026, 5, 1), allow_estimate=False)
    assert bill.total_cost == 5016.0, bill.total_cost
    assert len(bill.lines) == 1, len(bill.lines)
    assert any("14.04.2026" in warning for warning in warnings), warnings

engine.dispose()
print("validation ok", file=sys.stderr)
'@ | .\.venv\Scripts\python.exe -

Remove-Item -LiteralPath data\test_validation.db -Force -ErrorAction SilentlyContinue
