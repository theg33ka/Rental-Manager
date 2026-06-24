from __future__ import annotations

from typing import Any


OWNER_OPERATION_SPECS: dict[str, dict[str, Any]] = {
    "create_object": {
        "label": "создать объект аренды",
        "required": ["name"],
        "optional": ["short_code", "notes"],
        "steps": ["создать объект", "сохранить его реквизиты"],
    },
    "create_apartment": {
        "label": "создать квартиру",
        "required": ["object_id", "name"],
        "optional": ["sort_order", "odn_share_percent", "active"],
        "steps": ["проверить объект", "создать квартиру", "сохранить параметры учёта"],
    },
    "update_apartment": {
        "label": "изменить квартиру",
        "required": ["apartment_id"],
        "optional": ["name", "sort_order", "odn_share_percent", "active"],
        "steps": ["повторно загрузить квартиру", "применить указанные изменения", "пересчитать начисления при необходимости"],
    },
    "onboard_tenant": {
        "label": "заселить жильца",
        "required": ["apartment_id", "full_name", "start_date", "payment_day"],
        "optional": [
            "phone", "telegram", "whatsapp", "tenant_notes", "ip_amount", "personal_amount",
            "deposit_amount", "deposit_location", "deposit_terms", "notes", "ignored",
        ],
        "steps": ["проверить, что квартира свободна", "создать жильца и договор", "сформировать арендные начисления"],
    },
    "update_lease": {
        "label": "изменить договор и данные жильца",
        "required": ["lease_id"],
        "optional": [
            "apartment_id", "start_date", "end_date", "payment_day", "ip_amount", "personal_amount",
            "deposit_amount", "deposit_location", "deposit_terms", "notes", "ignored",
            "full_name", "phone", "telegram", "whatsapp", "tenant_notes",
        ],
        "steps": ["повторно загрузить договор", "применить перечисленные поля", "пересчитать начисления"],
    },
    "transfer_lease": {
        "label": "оформить переезд в другую квартиру",
        "required": ["lease_id", "apartment_id", "transfer_date"],
        "optional": ["ip_amount", "personal_amount"],
        "steps": ["проверить доступность новой квартиры", "закрыть старый договор накануне переезда", "создать новый договор и начисления"],
    },
    "delete_lease": {
        "label": "удалить договор",
        "required": ["lease_id"],
        "optional": [],
        "steps": ["проверить существование договора", "отвязать связанные служебные записи", "удалить договор, начисления и его платежи"],
    },
    "move_out": {
        "label": "оформить выезд",
        "required": ["lease_id", "end_date"],
        "optional": ["notes", "notify_tenant", "tenant_message"],
        "steps": ["закрыть договор указанной датой", "убрать будущие неоплаченные начисления", "при явном указании отправить жильцу сообщение"],
    },
    "set_lease_automation": {
        "label": "изменить автоматизацию договора",
        "required": ["lease_id"],
        "optional": ["message_rent_due", "message_rent_overdue", "message_utility_bill"],
        "steps": ["проверить договор", "сохранить частоту каждого типа напоминаний"],
    },
    "clear_lease_automation": {
        "label": "сбросить автоматизацию договора",
        "required": ["lease_id"],
        "optional": [],
        "steps": ["проверить договор", "удалить индивидуальные настройки напоминаний"],
    },
    "set_lease_ignored": {
        "label": "изменить учёт договора",
        "required": ["lease_id", "ignored"],
        "optional": [],
        "steps": ["проверить договор", "включить или выключить его участие в расчётах и дашборде"],
    },
    "send_tenant_message": {
        "label": "отправить сообщение жильцу",
        "required": ["lease_id", "text"],
        "optional": [],
        "steps": ["проверить Telegram-привязку", "отправить точный текст", "записать результат отправки в журнал"],
    },
    "broadcast_message": {
        "label": "отправить рассылку жильцам",
        "required": ["message"],
        "optional": ["all", "scope", "lease_ids", "object_ids", "apartment_ids"],
        "steps": ["сформировать список адресатов", "пропустить непривязанные и повторяющиеся чаты", "отправить текст и записать результаты"],
    },
    "run_reminders": {
        "label": "запустить напоминания",
        "required": [],
        "optional": [],
        "steps": ["проверить актуальные долги и расписания", "отправить положенные напоминания", "вернуть сводку отправок и ошибок"],
    },
    "generate_rent_charges": {
        "label": "сформировать арендные начисления",
        "required": [],
        "optional": ["until"],
        "steps": ["определить период генерации", "создать отсутствующие начисления без дублей"],
    },
    "add_rent_payment": {
        "label": "добавить оплату аренды",
        "required": ["charge_id", "channel", "amount"],
        "optional": ["paid_at", "source", "status", "recipient_name", "recipient_details", "notes"],
        "steps": ["проверить начисление и сумму", "распределить оплату по правилам", "пересчитать остатки"],
    },
    "create_manual_payment": {
        "label": "добавить ручной платёж",
        "required": ["lease_id", "kind", "amount"],
        "optional": [
            "channel", "paid_at", "source", "notes", "rent_charge_id", "target_month",
            "target_year", "utility_line_id",
        ],
        "steps": ["проверить договор и назначение", "создать платёж по выбранной цели", "пересчитать баланс"],
    },
    "create_manual_debt": {
        "label": "создать ручной долг",
        "required": ["lease_id", "title", "amount", "due_date"],
        "optional": ["kind", "channel", "period_start", "period_end", "note", "notes"],
        "steps": ["проверить договор и сумму", "создать начисление", "обновить статус долга"],
    },
    "update_manual_debt": {
        "label": "изменить ручной долг",
        "required": ["debt_id"],
        "optional": ["lease_id", "title", "amount", "paid_amount", "due_date", "kind", "channel", "period_start", "period_end", "notes"],
        "steps": ["повторно загрузить долг", "применить указанные изменения", "пересчитать его статус"],
    },
    "add_manual_debt_payment": {
        "label": "добавить оплату ручного долга",
        "required": ["debt_id", "amount"],
        "optional": ["paid_at", "notes"],
        "steps": ["проверить долг и сумму", "увеличить оплаченную часть", "обновить статус"],
    },
    "delete_manual_debt": {
        "label": "удалить ручной долг",
        "required": ["debt_id"],
        "optional": [],
        "steps": ["проверить долг", "выключить его из активного учёта"],
    },
    "update_payment_receipt": {
        "label": "изменить платёж",
        "required": ["receipt_id"],
        "optional": ["amount", "paid_at", "notes", "target_kind", "rent_charge_id", "utility_line_id", "channel", "target_month", "target_year", "status"],
        "steps": ["повторно загрузить платёж", "изменить сумму, дату или цель зачёта", "пересчитать затронутые балансы"],
    },
    "delete_payment_receipt": {
        "label": "удалить платёж",
        "required": ["receipt_id"],
        "optional": [],
        "steps": ["проверить платёж", "удалить связанную запись", "пересчитать баланс договора"],
    },
    "ignore_payment_receipt": {
        "label": "скрыть платёж из проверки",
        "required": ["receipt_id"],
        "optional": [],
        "steps": ["проверить платёж", "пометить его проигнорированным"],
    },
    "moderate_payment_receipt": {
        "label": "провести модерацию платежа",
        "required": ["receipt_id", "action"],
        "optional": ["note", "channel"],
        "steps": ["проверить платёж и привязку", "зачесть его в аренду/коммуналку либо отклонить", "пересчитать начисления"],
    },
    "defer_rent": {
        "label": "выдать отсрочку по аренде",
        "required": ["lease_id", "charge_id", "deferral_until"],
        "optional": ["note", "notify_tenant", "tenant_message"],
        "steps": ["проверить наличие долга", "установить новый срок", "при явном указании отправить жильцу сообщение", "пересчитать статус начисления"],
    },
    "update_utility_service": {
        "label": "изменить сроки коммунальной услуги",
        "required": ["service_id"],
        "optional": ["provider_reading_due_day", "provider_due_day", "resident_due_days"],
        "steps": ["проверить коммунальную услугу", "сохранить новые контрольные дни"],
    },
    "save_meter_reading": {
        "label": "сохранить показание счётчика",
        "required": ["meter_id", "reading_date", "value"],
        "optional": ["note"],
        "steps": ["проверить счётчик", "создать или заменить показание на указанную дату"],
    },
    "save_meter_readings_batch": {
        "label": "сохранить набор показаний",
        "required": ["reading_date", "readings"],
        "optional": [],
        "steps": ["проверить список счётчиков", "создать или заменить каждое непустое показание"],
    },
    "create_tariff": {
        "label": "создать тариф",
        "required": ["service_id", "starts_on", "name", "tiers"],
        "optional": [],
        "steps": ["проверить услугу и тарифные ступени", "создать новую версию тарифа"],
    },
    "calculate_utility_bill": {
        "label": "рассчитать коммунальный счёт",
        "required": ["service_id", "period_start", "period_end"],
        "optional": ["allow_estimate"],
        "steps": ["проверить период и показания", "рассчитать общий расход и доли квартир", "создать черновик счёта"],
    },
    "delete_utility_bill": {
        "label": "удалить коммунальный счёт",
        "required": ["bill_id"],
        "optional": [],
        "steps": ["проверить отсутствие принятых оплат", "отвязать журналы и удалить счёт", "пересчитать затронутые договоры"],
    },
    "issue_utility_bill": {
        "label": "выставить коммунальный счёт",
        "required": ["bill_id"],
        "optional": [],
        "steps": ["проверить черновик и адресатов", "выставить строки жильцам", "зачесть авансы", "отправить уведомления привязанным жильцам"],
    },
    "mark_provider_paid": {
        "label": "отметить оплату поставщику",
        "required": ["bill_id"],
        "optional": [],
        "steps": ["проверить коммунальный счёт", "зафиксировать дату оплаты поставщику"],
    },
    "add_utility_payment": {
        "label": "добавить оплату коммунальных услуг",
        "required": ["line_id", "amount"],
        "optional": ["paid_at", "source", "status", "recipient_name", "recipient_details", "notes"],
        "steps": ["проверить строку счёта и сумму", "создать оплату", "пересчитать остаток"],
    },
    "create_expense": {
        "label": "создать расход",
        "required": ["expense_date", "category", "amount"],
        "optional": ["object_id", "apartment_id", "source_funds", "payment_method", "description", "file_path", "notes"],
        "steps": ["проверить сумму и привязку", "создать расход", "назначить статус компенсации"],
    },
    "compensate_expense": {
        "label": "отметить компенсацию расхода",
        "required": ["expense_id"],
        "optional": [],
        "steps": ["проверить расход", "пометить его компенсированным и сохранить дату"],
    },
    "accept_monthly_report": {
        "label": "принять месячный отчёт",
        "required": ["year", "month"],
        "optional": ["kind"],
        "steps": ["проверить месяц и тип отчёта", "зафиксировать принятие отчёта"],
    },
}


ARGUMENT_LABELS = {
    "lease_id": "договор",
    "charge_id": "начисление аренды",
    "apartment_id": "квартира",
    "object_id": "объект",
    "service_id": "коммунальная услуга",
    "meter_id": "счётчик",
    "bill_id": "коммунальный счёт",
    "line_id": "строка коммунального счёта",
    "debt_id": "ручной долг",
    "receipt_id": "платёж",
    "expense_id": "расход",
    "text": "текст сообщения",
    "message": "текст рассылки",
    "amount": "сумма",
    "deferral_until": "новый срок оплаты",
    "end_date": "дата выезда",
    "transfer_date": "дата переезда",
    "reading_date": "дата показания",
    "period_start": "начало периода",
    "period_end": "конец периода",
    "due_date": "срок оплаты",
}


def owner_operation_catalog() -> list[dict[str, Any]]:
    return [
        {
            "operation": name,
            "description": spec["label"],
            "required": spec["required"],
            "optional": spec["optional"],
        }
        for name, spec in OWNER_OPERATION_SPECS.items()
    ]


def validate_owner_operation(operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
    spec = OWNER_OPERATION_SPECS.get(operation)
    if spec is None:
        raise ValueError(f"Операция «{operation}» не поддерживается")
    missing = [
        key
        for key in spec["required"]
        if key not in arguments or arguments.get(key) is None or arguments.get(key) == ""
    ]
    if missing:
        raise ValueError("Не хватает параметров: " + ", ".join(missing))
    allowed = set(spec["required"]) | set(spec["optional"])
    return {
        key: value.strip() if isinstance(value, str) else value
        for key, value in arguments.items()
        if key in allowed
    }


def owner_operation_preview(operation: str, arguments: dict[str, Any], target: str = "") -> str:
    spec = OWNER_OPERATION_SPECS[operation]
    lines = [f"Предлагаю: {spec['label']}"]
    if target:
        lines.append(f"Цель: {target}")
    lines.append("Параметры:")
    for key, value in arguments.items():
        rendered = "да" if value is True else "нет" if value is False else str(value)
        if len(rendered) > 700:
            rendered = rendered[:697] + "..."
        lines.append(f"• {ARGUMENT_LABELS.get(key, key)}: {rendered}")
    lines.append("Порядок выполнения:")
    for index, step in enumerate(spec["steps"], start=1):
        lines.append(f"{index}. {step}.")
    lines.append("")
    lines.append("До подтверждения данные не изменятся.")
    return "\n".join(lines)


def owner_operation_label(operation: str) -> str:
    return str(OWNER_OPERATION_SPECS.get(operation, {}).get("label") or operation)
