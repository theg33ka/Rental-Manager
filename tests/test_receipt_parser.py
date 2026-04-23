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
БИК 045004641
Корреспондентский счет 30101810500000000641
Счёт получателя 40802810644050156191
Получатель ИП Чантурия Эраст Митридатович
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
Телефон получателя +7 (913) 385-44-41
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
Счет получателя 40802810644050156191
Получатель ИП Чантурия Эраст Митридатович
Назначение перевода БД 3
Служба поддержки fb@tbank.ru
По вопросам зачисления обращайтесь к получателю
Квитанция  № 1-103-296-522-804
"""


class ReceiptParserTests(unittest.TestCase):
    def test_parses_ozon_ip_receipt(self) -> None:
        parsed = parse_receipt_text(OZON_IP_RECEIPT_TEXT)

        self.assertEqual(parsed["source_bank"], "ozon_bank")
        self.assertEqual(parsed["amount"], 20000.0)
        self.assertEqual(parsed["paid_at"], "2026-03-14T09:10")
        self.assertEqual(parsed["payer_name"], "Сажин Евгений Викторович")
        self.assertEqual(parsed["recipient_name"], "ИП Чантурия Эраст Митридатович")
        self.assertEqual(parsed["recipient_account"], "40802810644050156191")
        self.assertEqual(parsed["purpose"], "ЧД2 Без НДС")
        self.assertTrue(parsed["is_success"])

    def test_parses_tbank_phone_receipt(self) -> None:
        parsed = parse_receipt_text(TBANK_PHONE_RECEIPT_TEXT)

        self.assertEqual(parsed["source_bank"], "tbank")
        self.assertEqual(parsed["amount"], 7620.0)
        self.assertEqual(parsed["paid_at"], "2026-03-13T15:03")
        self.assertEqual(parsed["transfer_type"], "По номеру телефона")
        self.assertEqual(parsed["payer_name"], "Денис Часовских")
        self.assertEqual(parsed["recipient_phone"], "+7 (913) 385-44-41")
        self.assertEqual(parsed["recipient_name"], "Эрнест К.")
        self.assertEqual(parsed["recipient_bank"], "Сбербанк")
        self.assertEqual(parsed["receipt_number"], "1-130-088-396-459")
        self.assertTrue(parsed["is_success"])

    def test_parses_tbank_ip_receipt(self) -> None:
        parsed = parse_receipt_text(TBANK_IP_RECEIPT_TEXT)

        self.assertEqual(parsed["source_bank"], "tbank")
        self.assertEqual(parsed["amount"], 20000.0)
        self.assertEqual(parsed["paid_at"], "2026-03-01T15:46")
        self.assertEqual(parsed["transfer_type"], "Юридическому лицу")
        self.assertEqual(parsed["recipient_name"], "ИП Чантурия Эраст Митридатович")
        self.assertEqual(parsed["recipient_account"], "40802810644050156191")
        self.assertEqual(parsed["purpose"], "БД 3")
        self.assertEqual(parsed["receipt_number"], "1-103-296-522-804")
        self.assertTrue(parsed["is_success"])


if __name__ == "__main__":
    unittest.main()
