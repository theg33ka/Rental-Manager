from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from rental_manager.database import Base
from rental_manager.main import (
    apply_receipt_match,
    build_message_context,
    create_apartment,
    create_object,
    create_payment_profile,
    delete_object,
    delete_payment_profile,
    onboard_tenant,
    update_apartment,
    update_object,
    update_payment_profile,
)
from rental_manager.models import Apartment, AppSetting, Lease, PaymentProfile, RentalObject
from rental_manager.services.payment_profiles import effective_payment_profile, effective_payment_settings


class PaymentProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "payment-profiles.db"
        self.engine = create_engine(f"sqlite:///{database_path.as_posix()}", future=True)
        Base.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_object_default_and_apartment_override_are_reused_by_rental_flows(self) -> None:
        with Session(self.engine) as session:
            session.add_all(
                [
                    AppSetting(key="ip_recipient_name", value="Глобальный ИП"),
                    AppSetting(key="ip_recipient_account", value="GLOBAL-ACCOUNT"),
                    AppSetting(key="personal_recipient_phone", value="+7 900 000-00-00"),
                    AppSetting(key="personal_recipient_bank", value="Глобальный банк"),
                ]
            )
            session.commit()

            object_profile = create_payment_profile(
                {
                    "name": "Домовой набор",
                    "ip_recipient_name": "ИП Домовой",
                    "ip_recipient_account": "OBJECT-ACCOUNT",
                    "personal_recipient_phone": "+7 911 111-11-11",
                    "personal_recipient_bank": "Банк объекта",
                },
                session,
            )
            apartment_profile = create_payment_profile(
                {
                    "name": "Отдельный набор",
                    "ip_recipient_name": "ИП Квартирный",
                    "ip_recipient_account": "APARTMENT-ACCOUNT",
                    "personal_recipient_phone": "+7 922 222-22-22",
                    "personal_recipient_bank": "Банк квартиры",
                },
                session,
            )
            obj = create_object(
                {"name": "Новый двор", "short_code": "НД", "payment_profile_id": object_profile["id"]},
                session,
            )
            inherited = create_apartment(
                {"object_id": obj["id"], "name": "НД-1", "odn_share_percent": 50},
                session,
            )
            overridden = create_apartment(
                {
                    "object_id": obj["id"],
                    "name": "НД-2",
                    "odn_share_percent": 50,
                    "payment_profile_id": apartment_profile["id"],
                },
                session,
            )

            inherited_model = session.get(Apartment, inherited["id"])
            overridden_model = session.get(Apartment, overridden["id"])
            self.assertEqual(effective_payment_profile(inherited_model)[0].id, object_profile["id"])
            self.assertEqual(effective_payment_profile(overridden_model)[0].id, apartment_profile["id"])

            lease_payload = onboard_tenant(
                {
                    "apartment_id": inherited["id"],
                    "full_name": "Алексей Воронцов",
                    "start_date": date.today().isoformat(),
                    "payment_day": date.today().day,
                    "ip_amount": 25000,
                    "personal_amount": 5000,
                },
                session,
            )
            lease = session.get(Lease, lease_payload["id"])
            context = build_message_context(session, lease)
            self.assertEqual(context["ip_recipient_name_text"], "ИП Домовой")
            self.assertEqual(context["ip_recipient_account_text"], "OBJECT-ACCOUNT")
            self.assertEqual(context["personal_recipient_phone_text"], "+7 911 111-11-11")

            _status, _match_type, _linked_id, issues, _comment = apply_receipt_match(
                session,
                lease,
                {
                    "amount": 1,
                    "transfer_type": "по номеру телефона",
                    "recipient_name": "",
                    "recipient_phone": "+7 911 111-11-11",
                    "recipient_bank": "Банк объекта",
                },
            )
            self.assertFalse(any("номер получателя" in issue.lower() for issue in issues))

    def test_global_settings_are_used_only_when_no_profile_is_assigned(self) -> None:
        with Session(self.engine) as session:
            obj = RentalObject(name="Без профиля")
            apartment = Apartment(object=obj, name="1")
            session.add_all([obj, apartment])
            session.commit()
            settings = {"ip_recipient_name": "Глобальный ИП", "personal_recipient_phone": "+7 900 000-00-00"}
            resolved = effective_payment_settings(apartment, settings)
            self.assertEqual(resolved["ip_recipient_name"], "Глобальный ИП")
            self.assertEqual(resolved["personal_recipient_phone"], "+7 900 000-00-00")
            self.assertEqual(effective_payment_profile(apartment), (None, "global"))

    def test_used_profile_can_be_archived_but_not_deleted_or_newly_assigned(self) -> None:
        with Session(self.engine) as session:
            profile = create_payment_profile({"name": "Рабочий набор"}, session)
            obj = create_object({"name": "Дом", "payment_profile_id": profile["id"]}, session)
            archived = update_payment_profile(profile["id"], {"active": False}, session)
            self.assertFalse(archived["active"])
            self.assertEqual(session.get(RentalObject, obj["id"]).payment_profile_id, profile["id"])

            with self.assertRaises(HTTPException) as assignment_error:
                create_apartment(
                    {"object_id": obj["id"], "name": "1", "payment_profile_id": profile["id"]},
                    session,
                )
            self.assertEqual(assignment_error.exception.status_code, 409)

            with self.assertRaises(HTTPException) as delete_error:
                delete_payment_profile(profile["id"], session)
            self.assertEqual(delete_error.exception.status_code, 409)

    def test_object_and_apartment_with_active_lease_cannot_be_archived(self) -> None:
        with Session(self.engine) as session:
            obj = create_object({"name": "Жилой дом"}, session)
            apartment = create_apartment({"object_id": obj["id"], "name": "1"}, session)
            onboard_tenant(
                {
                    "apartment_id": apartment["id"],
                    "full_name": "Марина Соколова",
                    "start_date": date.today().isoformat(),
                    "payment_day": date.today().day,
                },
                session,
            )

            with self.assertRaises(HTTPException) as apartment_error:
                update_apartment(apartment["id"], {"active": False}, session)
            self.assertEqual(apartment_error.exception.status_code, 409)

            with self.assertRaises(HTTPException) as object_error:
                update_object(obj["id"], {"active": False}, session)
            self.assertEqual(object_error.exception.status_code, 409)

            with self.assertRaises(HTTPException) as delete_error:
                delete_object(obj["id"], session)
            self.assertEqual(delete_error.exception.status_code, 409)

    def test_unused_profile_and_empty_object_can_be_deleted(self) -> None:
        with Session(self.engine) as session:
            profile = create_payment_profile({"name": "Временный"}, session)
            self.assertTrue(delete_payment_profile(profile["id"], session)["ok"])
            self.assertIsNone(session.scalar(select(PaymentProfile).where(PaymentProfile.id == profile["id"])))

            obj = create_object({"name": "Пустой объект"}, session)
            self.assertTrue(delete_object(obj["id"], session)["ok"])
            self.assertIsNone(session.get(RentalObject, obj["id"]))


if __name__ == "__main__":
    unittest.main()
