from __future__ import annotations

import re


TENANT_SYSTEM_PROMPT = """Ты AI-помощник Rental Manager для арендатора.
Отвечай только по предоставленному контексту этого арендатора.
Не показывай данные других жильцов, не выдумывай суммы и даты, не обещай отсрочки, скидки, возврат залога или изменение договора.
Если вопрос требует решения владельца, коротко скажи, что передашь вопрос владельцу.
Пиши по-русски, спокойно и конкретно. Немного живого тона можно, но без цирка на проводе.
"""

OWNER_SYSTEM_PROMPT = """Ты Hermes-супервизор Rental Manager для владельца.
Используй только предоставленный контекст пульта. Помогай найти долги, риски и следующие действия.
Не меняй данные и не утверждай, что действие выполнено. Для изменений предлагай действие на подтверждение владельца.
Пиши по-русски, кратко, по делу, с приоритетом проблем.
"""

AUDIT_USER_PROMPT = """Проведи ревизию Rental Manager.
Найди, кого нужно пнуть, какие долги или чеки требуют внимания, где не хватает показаний, отчётов, оплат поставщикам или компенсаций.
Дай короткий список действий по приоритету.
"""

AI_UNAVAILABLE_TEXT = "ИИ-помощник сейчас недоступен. Базовые команды работают: /debts, /requisites, /help."
AI_DISABLED_TEXT = "ИИ-помощник выключен в настройках. Шаблонные команды работают, паника отменяется."
AI_BUDGET_EXCEEDED_TEXT = "Лимит ИИ на месяц исчерпан. Чтобы бюджет не сделал вид, что он резиновый, отвечаю только штатными командами."
TENANT_ESCALATION_TEXT = "Этот вопрос лучше подтвердить у владельца. Я передал его дальше и не буду обещать лишнего."

TENANT_ESCALATION_PATTERNS = [
    r"\bотсроч",
    r"\bскидк",
    r"\bзалог",
    r"\bдепозит",
    r"\bсъезж",
    r"\bвыезж",
    r"\bрасторг",
    r"\bдоговор",
    r"\bремонт",
    r"\bжалоб",
    r"\bштраф",
    r"\bперерасч",
    r"\bне\s+буду\s+плат",
]


def tenant_question_needs_owner(text: str) -> bool:
    lowered = (text or "").lower()
    return any(re.search(pattern, lowered) for pattern in TENANT_ESCALATION_PATTERNS)


def estimate_tokens(text: str) -> int:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return 0
    return max(1, int(len(cleaned) / 4) + 1)


def clean_ai_response(text: str, max_chars: int = 3600) -> str:
    value = (text or "").strip()
    value = re.sub(r"\n{3,}", "\n\n", value)
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 20].rstrip() + "\n\n[ответ сокращён]"
