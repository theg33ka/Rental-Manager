from __future__ import annotations

import unittest

from rental_manager.services.receipt_parser import parse_receipt_text


OZON_IP_RECEIPT_TEXT = """\
Перевод 14.03.2026 09:10
Итого 20 000 ₽
Статус Успешно
Счёт списания Основной счёт
Сумма 20 000 ₽
Комиссия Без комиссии
Плательщик Сажин Евгений Викторович
Банк-
получатель
СИБИРСКИЙ БАНК ПАО СБЕРБАНК, г. Новосибирск
БИК 000000000
Корреспондентский счет 00000000000000000000
Счёт получателя 00000000000000000000
Получатель ИП Тестовый Получатель
Назначение платежа ЧД2 Без НДС
По вопросам зачисления обращайтесь к получателю
Служба поддержки Ozon Банка: 8 (800) 555-89-82
ООО «ОЗОН БАНК»
"""


TBANK_PHONE_RECEIPT_TEXT = """\
13.03.2026  15:03:51

Итого 7 620 i
Перевод По номеру телефона
Статус Успешно
7 620 iСумма
Комиссия Без комиссии
Денис ЧасовскихОтправитель
Телефон получателя +7 (000) 000-00-00
Получатель Эрнест К.
Банк получателя Сбербанк
Счет списания 423018103000****2261
Идентификатор операции СБП B60721203517031F0B10110011700501
Служба поддержки fb@tbank.ru
По вопросам зачисления обращайтесь к получателю
Квитанция  № 1-130-088-396-459
"""


TBANK_IP_RECEIPT_TEXT = """\
01.03.2026  15:46:54

Итого 20 000 i
Перевод Юридическому лицу
Статус Успешно
20 000 iСумма
Банк получателя СИБИРСКИЙ БАНК ПАО СБЕРБАНК
Счет получателя 00000000000000000000
Получатель ИП Тестовый Получатель
Назначение перевода БД 3
Служба поддержки fb@tbank.ru
По вопросам зачисления обращайтесь к получателю
Квитанция  № 1-103-296-522-804
"""


SBER_IP_RECEIPT_TEXT = """\
Чек по операции
СберБанк Онлайн
16 марта 2026 09:03:17 мск
ОПЛАТА ПО РЕКВИЗИТАМ
Сумма платежа
20 000,00 ₽
Комиссия
200,00 ₽
Итого
20 200,00 ₽
Плательщик
Тестов Тест Тестович
Получатель
ИНДИВИДУАЛЬНЫЙ ПРЕДПРИНИМАТЕЛЬ ТЕСТОВ ТЕСТ ТЕСТОВИЧ
БИК
000000000
Счёт получателя
00000000000000000000
Назначение платежа
КАВ 3
"""


SBER_PHONE_RECEIPT_TEXT = """\
Чек по операции
16 апреля 2026 17:57:01 (МСК)
Перевод клиенту СберБанка
ФИО получателя
Марьяна Сергеевна С
Телефон получателя
+7(000) 000-00-00
Сумма перевода
765,00 ₽
Номер документа
1000000004706610066
"""


OZON_PHONE_RECEIPT_TEXT = """\
Перевод 28.04.2026 05:42
Итого 15 000 ₽
Статус Успешно
Счёт списания Основной счёт
Сумма 15 000 ₽
Комиссия Без комиссии
Получатель Эрнест Игоревич К.
Телефон получателя +7 (000) 000-00-00
Банк получателя Сбербанк
Отправитель Евгений Викторович С.
ID операции B61180242138760B0G10100011750501
Cooбщение Коммунальные
По вопросам зачисления обращайтесь к получателю
Служба поддержки Ozon Банка: 8 (800) 555-89-82
ООО «ОЗОН БАНК»
БИК 000000000 ИНН 0000000000
К/С 00000000000000000000
"""


VTB_PHONE_RECEIPT_TEXT = """\
Исходящий перевод СБП
Эрнест Игоревич К.
Статус Выполнено
Дата операции 30.05.2026, 20:21
Счет списания *9767
Имя плательщика Евгений Викторович С.
Сообщение электроэнергия
Получатель Эрнест Игоревич К.
Телефон получателя +7 (000) 000‑00‑00
Банк получателя Сбербанк
ID операции в СБП B61501721253400A0B1014001
1770901
Сумма операции 1 444.19 ₽
Банк ВТБ (ПАО)
Операция выполнена
"""


class ReceiptParserTests(unittest.TestCase):
    def test_parses_ozon_ip_receipt(self) -> None:
        parsed = parse_receipt_text(OZON_IP_RECEIPT_TEXT)

        self.assertEqual(parsed["source_bank"], "ozon_bank")
        self.assertEqual(parsed["amount"], 20000.0)
        self.assertEqual(parsed["paid_at"], "2026-03-14T09:10")
        self.assertEqual(parsed["payer_name"], "Сажин Евгений Викторович")
        self.assertEqual(parsed["recipient_name"], "ИП Тестовый Получатель")
        self.assertEqual(parsed["recipient_account"], "00000000000000000000")
        self.assertEqual(parsed["purpose"], "ЧД2 Без НДС")
        self.assertTrue(parsed["is_success"])

    def test_parses_tbank_phone_receipt(self) -> None:
        parsed = parse_receipt_text(TBANK_PHONE_RECEIPT_TEXT)

        self.assertEqual(parsed["source_bank"], "tbank")
        self.assertEqual(parsed["amount"], 7620.0)
        self.assertEqual(parsed["paid_at"], "2026-03-13T15:03")
        self.assertEqual(parsed["transfer_type"], "По номеру телефона")
        self.assertEqual(parsed["payer_name"], "Денис Часовских")
        self.assertEqual(parsed["recipient_phone"], "+7 (000) 000-00-00")
        self.assertEqual(parsed["recipient_name"], "Эрнест К")
        self.assertEqual(parsed["recipient_bank"], "Сбербанк")
        self.assertEqual(parsed["receipt_number"], "1-130-088-396-459")
        self.assertTrue(parsed["is_success"])

    def test_parses_tbank_ip_receipt(self) -> None:
        parsed = parse_receipt_text(TBANK_IP_RECEIPT_TEXT)

        self.assertEqual(parsed["source_bank"], "tbank")
        self.assertEqual(parsed["amount"], 20000.0)
        self.assertEqual(parsed["paid_at"], "2026-03-01T15:46")
        self.assertEqual(parsed["transfer_type"], "Юридическому лицу")
        self.assertEqual(parsed["recipient_name"], "ИП Тестовый Получатель")
        self.assertEqual(parsed["recipient_account"], "00000000000000000000")
        self.assertEqual(parsed["purpose"], "БД 3")
        self.assertEqual(parsed["receipt_number"], "1-103-296-522-804")
        self.assertTrue(parsed["is_success"])

    def test_parses_sber_ip_receipt(self) -> None:
        parsed = parse_receipt_text(SBER_IP_RECEIPT_TEXT)

        self.assertEqual(parsed["source_bank"], "sberbank")
        self.assertEqual(parsed["amount"], 20000.0)
        self.assertEqual(parsed["paid_at"], "2026-03-16T09:03")
        self.assertEqual(parsed["transfer_type"], "По реквизитам")
        self.assertEqual(parsed["recipient_name"], "ИНДИВИДУАЛЬНЫЙ ПРЕДПРИНИМАТЕЛЬ ТЕСТОВ ТЕСТ ТЕСТОВИЧ")
        self.assertEqual(parsed["recipient_account"], "00000000000000000000")
        self.assertEqual(parsed["recipient_bik"], "000000000")
        self.assertEqual(parsed["purpose"], "КАВ 3")

    def test_parses_sber_phone_receipt(self) -> None:
        parsed = parse_receipt_text(SBER_PHONE_RECEIPT_TEXT)

        self.assertEqual(parsed["source_bank"], "sberbank")
        self.assertEqual(parsed["amount"], 765.0)
        self.assertEqual(parsed["paid_at"], "2026-04-16T17:57")
        self.assertEqual(parsed["transfer_type"], "По номеру телефона")
        self.assertEqual(parsed["recipient_name"], "Марьяна Сергеевна С")
        self.assertEqual(parsed["recipient_phone"], "+7(000) 000-00-00")
        self.assertEqual(parsed["receipt_number"], "1000000004706610066")

    def test_parses_ozon_phone_receipt_with_sender_and_message(self) -> None:
        parsed = parse_receipt_text(OZON_PHONE_RECEIPT_TEXT)

        self.assertEqual(parsed["source_bank"], "ozon_bank")
        self.assertEqual(parsed["amount"], 15000.0)
        self.assertEqual(parsed["paid_at"], "2026-04-28T05:42")
        self.assertEqual(parsed["transfer_type"], "По номеру телефона")
        self.assertEqual(parsed["payer_name"], "Евгений Викторович С")
        self.assertEqual(parsed["recipient_name"], "Эрнест Игоревич К")
        self.assertEqual(parsed["recipient_phone"], "+7 (000) 000-00-00")
        self.assertEqual(parsed["recipient_bank"], "Сбербанк")
        self.assertEqual(parsed["purpose"], "Коммунальные")
        self.assertEqual(parsed["receipt_number"], "B61180242138760B0G10100011750501")
        self.assertTrue(parsed["is_success"])

    def test_parses_vtb_phone_receipt(self) -> None:
        parsed = parse_receipt_text(VTB_PHONE_RECEIPT_TEXT)

        self.assertEqual(parsed["source_bank"], "vtb")
        self.assertEqual(parsed["amount"], 1444.19)
        self.assertEqual(parsed["paid_at"], "2026-05-30T20:21")
        self.assertEqual(parsed["transfer_type"], "По номеру телефона")
        self.assertEqual(parsed["status"], "Выполнено")
        self.assertEqual(parsed["payer_name"], "Евгений Викторович С")
        self.assertEqual(parsed["recipient_name"], "Эрнест Игоревич К")
        self.assertEqual(parsed["recipient_phone"], "+7 (000) 000-00-00")
        self.assertEqual(parsed["recipient_bank"], "Сбербанк")
        self.assertEqual(parsed["purpose"], "электроэнергия")
        self.assertEqual(parsed["receipt_number"], "B61501721253400A0B10140011770901")
        self.assertTrue(parsed["is_success"])


if __name__ == "__main__":
    unittest.main()
