from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from rental_manager.database import SessionLocal, init_db
from rental_manager.models import (
    Apartment,
    Expense,
    Lease,
    Meter,
    MeterReading,
    PaymentReceipt,
    RentalObject,
    RentCharge,
    Tariff,
    Tenant,
    UtilityBill,
    UtilityService,
    utc_now,
)
from rental_manager.services.billing import calculate_utility_bill, generate_rent_charges, update_rent_charge_status, update_utility_line_status
from rental_manager.services.seed import seed_if_empty


DEMO_MARK = "DEMO_DATA"
DEMO_TODAY = date(2026, 4, 23)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill the local database with fictional demo data.")
    parser.add_argument("--reset-demo", action="store_true", help="Remove previous demo data before seeding.")
    args = parser.parse_args()

    init_db()
    with SessionLocal() as session:
        seed_if_empty(session)
        if has_demo_data(session):
            if not args.reset_demo:
                print("Demo data already exists. Run with --reset-demo to rebuild it.")
                return
            clear_demo_data(session)

        seed_demo_data(session)
        session.commit()
        print_summary(session)


def has_demo_data(session: Session) -> bool:
    return bool(session.scalar(select(Tenant.id).where(Tenant.notes.like(f"%{DEMO_MARK}%")).limit(1)))


def clear_demo_data(session: Session) -> None:
    demo_tenants = session.scalars(select(Tenant).where(Tenant.notes.like(f"%{DEMO_MARK}%"))).all()
    demo_leases = [lease for tenant in demo_tenants for lease in tenant.leases]
    demo_lease_ids = [lease.id for lease in demo_leases if lease.id is not None]

    receipt_filters = [PaymentReceipt.notes.like(f"%{DEMO_MARK}%")]
    if demo_lease_ids:
        receipt_filters.append(PaymentReceipt.lease_id.in_(demo_lease_ids))
    for receipt in session.scalars(select(PaymentReceipt).where(or_(*receipt_filters))).all():
        session.delete(receipt)

    for expense in session.scalars(select(Expense).where(Expense.notes.like(f"%{DEMO_MARK}%"))).all():
        session.delete(expense)

    for bill in session.scalars(select(UtilityBill).where(UtilityBill.notes.like(f"%{DEMO_MARK}%"))).all():
        session.delete(bill)

    if demo_lease_ids:
        for charge in session.scalars(select(RentCharge).where(RentCharge.lease_id.in_(demo_lease_ids))).all():
            session.delete(charge)

    for reading in session.scalars(select(MeterReading).where(MeterReading.note.like(f"%{DEMO_MARK}%"))).all():
        session.delete(reading)

    for lease in demo_leases:
        session.delete(lease)
    for tenant in demo_tenants:
        session.delete(tenant)

    session.flush()


def seed_demo_data(session: Session) -> None:
    objects = session.scalars(select(RentalObject).order_by(RentalObject.id)).all()
    if len(objects) < 3:
        raise RuntimeError("Base objects were not seeded.")

    for rental_object in objects:
        shares = [20, 25, 25, 30]
        apartments = apartments_for_object(session, rental_object)
        for apartment, share in zip(apartments, shares):
            apartment.odn_share_percent = share
    ensure_historical_tariffs(session)

    leases = seed_leases(session, objects)
    generate_rent_charges(session, until=date(2026, 6, 15))
    apply_rent_scenarios(session, leases)
    seed_meter_readings(session, objects)
    session.flush()
    seed_utility_bills(session, objects)
    seed_expenses(session, objects)
    seed_suspicious_receipt(session, leases)


def apartments_for_object(session: Session, rental_object: RentalObject) -> list[Apartment]:
    return session.scalars(
        select(Apartment)
        .where(Apartment.object_id == rental_object.id)
        .order_by(Apartment.sort_order, Apartment.id)
    ).all()


def service_for(session: Session, rental_object: RentalObject, kind: str) -> UtilityService:
    service = session.scalar(
        select(UtilityService)
        .where(UtilityService.object_id == rental_object.id, UtilityService.kind == kind)
        .limit(1)
    )
    if not service:
        raise RuntimeError(f"Missing service {kind} for object {rental_object.name}")
    return service


def ensure_historical_tariffs(session: Session) -> None:
    for service in session.scalars(select(UtilityService)).all():
        existing = session.scalar(
            select(Tariff)
            .where(Tariff.service_id == service.id, Tariff.starts_on <= date(2026, 1, 1))
            .limit(1)
        )
        if existing:
            continue
        tiers = [{"limit": None, "price": 1.0}]
        if service.kind == "electricity":
            tiers = [
                {"limit": 1000, "price": 4.2},
                {"limit": 4000, "price": 4.5},
                {"limit": None, "price": 7.0},
            ]
        elif service.kind == "water":
            tiers = [{"limit": None, "price": 38.5}]
        elif service.kind == "gas":
            tiers = [{"limit": None, "price": 8.4}]
        session.add(
            Tariff(
                service_id=service.id,
                starts_on=date(2026, 1, 1),
                name="Демо исторический тариф",
                tiers_json=json.dumps(tiers, ensure_ascii=False),
            )
        )
    session.flush()


def seed_leases(session: Session, objects: list[RentalObject]) -> dict[str, Lease]:
    white = apartments_for_object(session, objects[0])
    black = apartments_for_object(session, objects[1])
    bath = apartments_for_object(session, objects[2])

    specs = [
        ("white_paid", white[0], "Демо Иванова Мария Сергеевна", "+79010000001", "@demo_white_1", date(2025, 11, 14), 14, 20000, 5000, 15000, "личный сейф", "Вернуть после проверки квартиры."),
        ("white_partial", white[1], "Демо Петров Алексей Игоревич", "+79010000002", "@demo_white_2", date(2026, 1, 20), 20, 18000, 7000, 0, "", ""),
        ("white_overdue", white[2], "Демо Сидорова Анна Павловна", "+79010000003", "@demo_white_3", date(2026, 2, 21), 21, 20000, 10000, 10000, "наличные", "Особое условие: можно удержать клининг."),
        ("white_today", white[3], "Демо Кузнецов Павел Олегович", "+79010000004", "@demo_white_4", date(2026, 3, 23), 23, 17000, 3000, 0, "", ""),
        ("black_ahead", black[0], "Демо Орлова Елена Викторовна", "+79020000001", "@demo_black_1", date(2025, 12, 1), 1, 16000, 4000, 8000, "банк", "Залог не трогать без созвона."),
        ("black_deferred", black[1], "Демо Никитин Роман Андреевич", "+79020000002", "@demo_black_2", date(2026, 1, 15), 15, 15000, 5000, 0, "", ""),
        ("black_future", black[3], "Демо Смирнова Ольга Денисовна", "+79020000004", "@demo_black_4", date(2026, 2, 28), 30, 14000, 6000, 5000, "карта", "Зачесть только по письменной договорённости."),
        ("bath_paid", bath[0], "Демо Фролов Денис Максимович", "+79030000001", "@demo_bath_1", date(2026, 1, 10), 10, 12000, 8000, 0, "", ""),
        ("bath_31_partial", bath[1], "Демо Морозова Наталья Романовна", "+79030000002", "@demo_bath_2", date(2026, 1, 31), 31, 13000, 7000, 12000, "наличные", "31-е число, чтобы проверить переносы дат."),
        ("bath_moved_out", bath[2], "Демо Егоров Кирилл Артёмович", "+79030000003", "@demo_bath_3", date(2026, 2, 5), 5, 11000, 4000, 9000, "банк", "При выезде показать условия залога."),
    ]

    leases: dict[str, Lease] = {}
    for key, apartment, full_name, phone, telegram, start_date, payment_day, ip_amount, personal_amount, deposit, deposit_location, deposit_terms in specs:
        tenant = Tenant(
            full_name=full_name,
            phone=phone,
            telegram=telegram,
            whatsapp=phone,
            notes=f"{DEMO_MARK}: fictional tenant",
        )
        session.add(tenant)
        session.flush()
        lease = Lease(
            apartment_id=apartment.id,
            tenant_id=tenant.id,
            start_date=start_date,
            payment_day=payment_day,
            ip_amount=ip_amount,
            personal_amount=personal_amount,
            deposit_amount=deposit,
            deposit_location=deposit_location,
            deposit_terms=deposit_terms,
            notes=f"{DEMO_MARK}: scenario {key}",
        )
        if key == "bath_moved_out":
            lease.end_date = date(2026, 4, 20)
        session.add(lease)
        leases[key] = lease

    session.flush()
    return leases


def apply_rent_scenarios(session: Session, leases: dict[str, Lease]) -> None:
    scenarios = {
        "white_paid": "paid",
        "white_partial": "ip_only",
        "white_overdue": "unpaid",
        "white_today": "unpaid",
        "black_ahead": "paid_ahead",
        "black_deferred": "deferred",
        "black_future": "paid",
        "bath_paid": "paid",
        "bath_31_partial": "personal_only",
        "bath_moved_out": "ip_only",
    }

    for key, lease in leases.items():
        charges = sorted(lease.rent_charges, key=lambda charge: charge.due_date)
        current_charge = max((charge for charge in charges if charge.due_date <= DEMO_TODAY), key=lambda charge: charge.due_date)
        for charge in charges:
            if charge.due_date < current_charge.due_date:
                pay_rent_charge(session, charge, "ip", charge.ip_due)
                pay_rent_charge(session, charge, "personal", charge.personal_due)

        scenario = scenarios[key]
        if scenario == "paid":
            pay_rent_charge(session, current_charge, "ip", current_charge.ip_due)
            pay_rent_charge(session, current_charge, "personal", current_charge.personal_due)
        elif scenario == "paid_ahead":
            pay_rent_charge(session, current_charge, "ip", current_charge.ip_due)
            pay_rent_charge(session, current_charge, "personal", current_charge.personal_due + 1000)
        elif scenario == "ip_only":
            pay_rent_charge(session, current_charge, "ip", current_charge.ip_due)
        elif scenario == "personal_only":
            pay_rent_charge(session, current_charge, "personal", current_charge.personal_due)
        elif scenario == "deferred":
            current_charge.deferral_until = date(2026, 4, 25)
            current_charge.deferral_note = "Демо-отсрочка: обещал оплатить после зарплаты."

        for charge in charges:
            update_rent_charge_status(charge, today=DEMO_TODAY)

    leases["bath_moved_out"].active = False
    leases["bath_moved_out"].tenant.active = False


def pay_rent_charge(session: Session, charge: RentCharge, channel: str, amount: float) -> None:
    if channel == "ip":
        charge.ip_paid = round((charge.ip_paid or 0) + amount, 2)
    else:
        charge.personal_paid = round((charge.personal_paid or 0) + amount, 2)

    session.add(
        PaymentReceipt(
            lease_id=charge.lease_id,
            rent_charge_id=charge.id,
            apartment_id=charge.lease.apartment_id,
            amount=amount,
            channel=channel,
            paid_at=datetime.combine(charge.due_date, datetime.min.time()),
            source="demo",
            status="accepted",
            recipient_name="Демо ИП" if channel == "ip" else "Демо личный перевод",
            recipient_details="Фиктивные реквизиты для проверки",
            notes=DEMO_MARK,
        )
    )


def seed_meter_readings(session: Session, objects: list[RentalObject]) -> None:
    white, black, bath = objects

    seed_electricity(
        session,
        service_for(session, white, "electricity"),
        object_values=[(date(2026, 3, 1), 10000), (date(2026, 4, 1), 11500), (date(2026, 4, 23), 12600)],
        apartment_values=[
            [1000, 1280, 1450],
            [2000, 2350, 2500],
            [3000, 3270, 3400],
            [4000, 4210, 4310],
        ],
    )
    seed_electricity(
        session,
        service_for(session, black, "electricity"),
        object_values=[(date(2026, 3, 1), 8000), (date(2026, 4, 1), 9100), (date(2026, 4, 23), 9900)],
        apartment_values=[
            [1000, 1250, 1420],
            [2000, 2300, 2480],
            [3000, 3000, 3000],
            [4000, 4300, 4520],
        ],
    )
    seed_electricity(
        session,
        service_for(session, bath, "electricity"),
        object_values=[(date(2026, 3, 1), 12000), (date(2026, 4, 1), 13900), (date(2026, 4, 23), 15300)],
        apartment_values=[
            [1000, 1500, 1780],
            [2000, 2400, 2650],
            [3000, 3300, 3420],
            [4000, 4000, 4000],
        ],
    )

    add_object_readings(session, service_for(session, bath, "water"), [(date(2026, 3, 1), 500), (date(2026, 4, 1), 620), (date(2026, 4, 23), 700)])
    add_object_readings(session, service_for(session, bath, "gas"), [(date(2026, 2, 1), 600), (date(2026, 3, 1), 700)])


def seed_electricity(
    session: Session,
    service: UtilityService,
    object_values: list[tuple[date, float]],
    apartment_values: list[list[float]],
) -> None:
    add_object_readings(session, service, object_values)
    apartment_meters = session.scalars(
        select(Meter)
        .where(Meter.service_id == service.id, Meter.scope == "apartment")
        .order_by(Meter.apartment_id)
    ).all()
    reading_dates = [item[0] for item in object_values]
    for meter, values in zip(apartment_meters, apartment_values):
        for reading_date, value in zip(reading_dates, values):
            add_reading(session, meter, reading_date, value)


def add_object_readings(session: Session, service: UtilityService, values: list[tuple[date, float]]) -> None:
    meter = session.scalar(
        select(Meter)
        .where(Meter.service_id == service.id, Meter.scope == "object")
        .limit(1)
    )
    if not meter:
        raise RuntimeError(f"Missing object meter for {service.name}")
    for reading_date, value in values:
        add_reading(session, meter, reading_date, value)


def add_reading(session: Session, meter: Meter, reading_date: date, value: float) -> None:
    existing = session.scalar(select(MeterReading).where(MeterReading.meter_id == meter.id, MeterReading.reading_date == reading_date))
    if existing:
        existing.value = value
        existing.note = DEMO_MARK
        return
    session.add(MeterReading(meter_id=meter.id, reading_date=reading_date, value=value, note=DEMO_MARK))


def seed_utility_bills(session: Session, objects: list[RentalObject]) -> None:
    white, black, bath = objects

    white_bill = create_bill(session, service_for(session, white, "electricity"), date(2026, 3, 1), date(2026, 4, 1), due_date=date(2026, 4, 10))
    apply_utility_payments(session, white_bill, ["paid", "partial", "unpaid", "paid"])

    black_forecast = create_bill(
        session,
        service_for(session, black, "electricity"),
        date(2026, 4, 1),
        date(2026, 5, 1),
        due_date=None,
        allow_estimate=True,
        issue=False,
    )
    black_forecast.notes = f"{DEMO_MARK}\nПрогнозный счёт по средним показаниям.\n{black_forecast.notes}".strip()

    bath_electric = create_bill(session, service_for(session, bath, "electricity"), date(2026, 3, 1), date(2026, 4, 1), due_date=date(2026, 4, 12))
    apply_utility_payments(session, bath_electric, ["paid", "unpaid", "partial"])

    bath_water = create_bill(session, service_for(session, bath, "water"), date(2026, 3, 1), date(2026, 4, 1), due_date=date(2026, 4, 15))
    apply_utility_payments(session, bath_water, ["paid", "paid", "paid"])
    bath_water.provider_paid = True
    bath_water.provider_paid_at = utc_now()

    bath_gas = create_bill(session, service_for(session, bath, "gas"), date(2026, 2, 1), date(2026, 3, 1), due_date=date(2026, 3, 10))
    apply_utility_payments(session, bath_gas, ["unpaid", "partial", "unpaid"])


def create_bill(
    session: Session,
    service: UtilityService,
    period_start: date,
    period_end: date,
    due_date: date | None,
    allow_estimate: bool = False,
    issue: bool = True,
) -> UtilityBill:
    bill, warnings = calculate_utility_bill(session, service.id, period_start, period_end, allow_estimate=allow_estimate)
    bill.notes = f"{DEMO_MARK}\n" + "\n".join(warnings)
    session.add(bill)
    session.flush()

    if issue:
        bill.status = "issued"
        bill.due_date = due_date
        for line in bill.lines:
            line.status = "issued"
            line.issued_at = utc_now()
            line.due_date = due_date
            update_utility_line_status(line, today=DEMO_TODAY)
    return bill


def apply_utility_payments(session: Session, bill: UtilityBill, statuses: list[str]) -> None:
    for line, status in zip(sorted(bill.lines, key=lambda item: item.apartment_id), statuses):
        if status == "paid":
            amount = line.total_amount
        elif status == "partial":
            amount = round(line.total_amount * 0.45, 2)
        else:
            amount = 0

        if amount > 0:
            line.paid_amount = round((line.paid_amount or 0) + amount, 2)
            session.add(
                PaymentReceipt(
                    lease_id=line.lease_id,
                    utility_line_id=line.id,
                    apartment_id=line.apartment_id,
                    amount=amount,
                    channel="utilities",
                    paid_at=datetime.combine(line.due_date or DEMO_TODAY, datetime.min.time()),
                    source="demo",
                    status="accepted",
                    recipient_name="Демо личный перевод",
                    recipient_details="Фиктивный номер телефона",
                    notes=DEMO_MARK,
                )
            )
        update_utility_line_status(line, today=DEMO_TODAY)


def seed_expenses(session: Session, objects: list[RentalObject]) -> None:
    white, black, bath = objects
    white_apartments = apartments_for_object(session, white)
    black_apartments = apartments_for_object(session, black)
    bath_apartments = apartments_for_object(session, bath)

    expenses = [
        Expense(
            expense_date=date(2026, 4, 5),
            object_id=white.id,
            apartment_id=white_apartments[1].id,
            category="ремонт",
            amount=4200,
            source_funds="personal",
            payment_method="карта",
            description="Замена смесителя в ванной",
            compensation_status="pending",
            notes=DEMO_MARK,
        ),
        Expense(
            expense_date=date(2026, 4, 7),
            object_id=black.id,
            apartment_id=black_apartments[3].id,
            category="реклама",
            amount=1800,
            source_funds="rental_budget",
            payment_method="онлайн",
            description="Продвижение объявления на неделю",
            compensation_status="not_required",
            notes=DEMO_MARK,
        ),
        Expense(
            expense_date=date(2026, 4, 11),
            object_id=bath.id,
            apartment_id=bath_apartments[0].id,
            category="клининг",
            amount=3000,
            source_funds="personal",
            payment_method="наличные",
            description="Уборка после короткой аренды",
            compensation_status="compensated",
            compensated_at=utc_now(),
            notes=DEMO_MARK,
        ),
        Expense(
            expense_date=date(2026, 4, 19),
            object_id=None,
            apartment_id=None,
            category="канцелярия",
            amount=650,
            source_funds="personal",
            payment_method="СБП",
            description="Папки под договоры и чеки",
            compensation_status="pending",
            notes=DEMO_MARK,
        ),
    ]
    session.add_all(expenses)


def seed_suspicious_receipt(session: Session, leases: dict[str, Lease]) -> None:
    lease = leases["white_overdue"]
    session.add(
        PaymentReceipt(
            lease_id=lease.id,
            apartment_id=lease.apartment_id,
            amount=20000,
            channel="ip",
            paid_at=datetime(2026, 4, 22, 18, 30),
            source="demo",
            status="suspicious",
            recipient_name="ООО Неизвестный Получатель",
            recipient_details="Получатель не совпадает с ожидаемыми реквизитами",
            file_path="data/uploads/demo_suspicious_receipt.txt",
            notes=f"{DEMO_MARK}: suspicious recipient",
        )
    )


def print_summary(session: Session) -> None:
    print("Demo data seeded:")
    for model in [Tenant, Lease, RentCharge, MeterReading, UtilityBill, Expense, PaymentReceipt]:
        count = session.scalar(select(model).count()) if False else len(session.scalars(select(model)).all())
        print(f"- {model.__name__}: {count}")
    print("Open http://127.0.0.1:8000 and refresh the page.")


if __name__ == "__main__":
    main()
