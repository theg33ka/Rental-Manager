# Скрипт для удаления старых записей арендной платы с неправильной датой
from datetime import date, timedelta
from sqlalchemy import select, delete, extract, update

from rental_manager.database import SessionLocal, init_db
from rental_manager.models import Lease, RentCharge, Apartment, PaymentReceipt
from rental_manager.services.billing import generate_rent_charges

init_db()
session = SessionLocal()

old_day = 29
new_day = 28

try:
    # Находим все аренды с payment_day = old_day
    old_leases = session.scalars(
        select(Lease).join(Apartment).where(Lease.payment_day == old_day)
    ).all()
    
    print(f"Найдено аренд с payment_day={old_day}: {len(old_leases)}")
    
    for lease in old_leases:
        print(f"\nАренда ID={lease.id}, квартира: {lease.apartment.name}")
        print(f"  payment_day: {lease.payment_day} -> {new_day}")
        
        # Сначала очищаем ссылки в payment_receipts
        old_charges = session.scalars(
            select(RentCharge).where(
                RentCharge.lease_id == lease.id,
                extract('day', RentCharge.due_date) == old_day
            )
        ).all()
        
        charge_ids = [c.id for c in old_charges]
        if charge_ids:
            print(f"  Очищаем {len(charge_ids)} ссылок в payment_receipts...")
            session.execute(
                update(PaymentReceipt)
                .where(PaymentReceipt.rent_charge_id.in_(charge_ids))
                .values(rent_charge_id=None)
            )
            session.commit()
        
        # Удаляем старые записи RentCharge
        print(f"  Удалено записей RentCharge: {len(old_charges)}")
        for charge in old_charges:
            print(f"    - {charge.due_date}: {charge.ip_due} (ИП), {charge.personal_due} (перс.)")
            session.delete(charge)
        
        # Коммитим удаления сразу
        session.commit()
        
        # Теперь меняем payment_day в отдельной транзакции
        lease.payment_day = new_day
        session.commit()
        print(f"  payment_day обновлён на {new_day}")
    
    print("\n=== Старые записи удалены, payment_day обновлён ===")
    
    # Теперь пересоздадим записи с правильной датой
    until = date.today() + timedelta(days=90)
    created = generate_rent_charges(session, until=until)
    
    print(f"\n=== Создано новых записей RentCharge: {created} ===")
    
except Exception as e:
    session.rollback()
    print(f"Ошибка: {e}")
    import traceback
    traceback.print_exc()
finally:
    session.close()