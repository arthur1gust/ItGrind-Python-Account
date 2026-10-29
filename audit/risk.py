from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from accounts.bank_account import BankAccount
from transactions.models import Transaction, TransactionType
from .log import AuditLevel


@dataclass(frozen=True)
class RiskAssessment:
    level: AuditLevel
    score: int
    reasons: tuple[str, ...]


class RiskAnalyzer:
    """Анализ до списания; историю попыток и получателей хранит в RAM.

    Правила по умолчанию:
      >=10 000 = 2 балла; >=50 000 = 3 балла;
      первый перевод на новый счёт/реквизиты = 2 балла;
      4-я операция за 60 секунд (включая отклонённые) = 3 балла;
      00:00–04:59 = 3 балла.
    0=LOW, 1–2=MEDIUM, >=3=HIGH. HIGH запрещает исполнение.
    В частоте одна транзакция учитывается только один раз (повторы не удваивают).
    """

    def __init__(self, large_amount="10000", very_large_amount="50000",
                 frequency_limit: int = 3, frequency_window_seconds: int = 60):
        self.large_amount = BankAccount._validate_amount(large_amount)
        self.very_large_amount = BankAccount._validate_amount(very_large_amount)
        if self.very_large_amount <= self.large_amount:
            raise ValueError("very_large_amount должен превышать large_amount")
        if type(frequency_limit) is not int or frequency_limit < 1:
            raise ValueError("frequency_limit должен быть положительным int")
        if type(frequency_window_seconds) is not int or frequency_window_seconds < 1:
            raise ValueError("frequency_window_seconds должен быть положительным int")
        self.frequency_limit = frequency_limit
        self.window = timedelta(seconds=frequency_window_seconds)
        self._attempts: dict[str, list[tuple[str, datetime]]] = {}
        self._known_recipients: dict[str, set[str]] = {}
        self._assessments: dict[str, dict] = {}

    @staticmethod
    def _recipient(tx: Transaction) -> str | None:
        if tx.type == TransactionType.TRANSFER:
            return f"internal:{tx.recipient_account_id}"
        if tx.type == TransactionType.EXTERNAL_TRANSFER:
            return f"external:{tx.recipient_details}"
        return None

    def analyze(self, tx: Transaction, now: datetime) -> RiskAssessment:
        if not isinstance(tx, Transaction):
            raise TypeError("Нужен Transaction")
        if not isinstance(now, datetime):
            raise TypeError("now должен быть datetime")
        score = 0
        reasons = []
        if tx.amount >= self.very_large_amount:
            score += 3
            reasons.append("VERY_LARGE_AMOUNT")
        elif tx.amount >= self.large_amount:
            score += 2
            reasons.append("LARGE_AMOUNT")

        past = self._attempts.get(tx.client_id, [])
        # Исключаем текущую транзакцию при повторном исполнении.
        recent = sum(1 for tid, stamp in past
                     if tid != tx.transaction_id and timedelta(0) <= now - stamp <= self.window)
        if recent >= self.frequency_limit:
            score += 3
            reasons.append("HIGH_FREQUENCY")

        recipient = self._recipient(tx)
        if recipient is not None and recipient not in self._known_recipients.get(tx.client_id, set()):
            score += 2
            reasons.append("NEW_RECIPIENT")

        if 0 <= now.hour < 5:
            score += 3
            reasons.append("NIGHT_OPERATION")

        level = AuditLevel.HIGH if score >= 3 else (AuditLevel.MEDIUM if score > 0 else AuditLevel.LOW)
        return RiskAssessment(level=level, score=score, reasons=tuple(reasons))

    def register_assessment(self, tx: Transaction, assessment: RiskAssessment, now: datetime) -> None:
        attempts = self._attempts.setdefault(tx.client_id, [])
        # Учитываем как принятые, так и заблокированные заявки. Повтор той же
        # транзакции не считается новым платежом.
        if not any(tid == tx.transaction_id for tid, _ in attempts):
            attempts.append((tx.transaction_id, now))
        self._assessments[tx.transaction_id] = {
            "client_id": tx.client_id,
            "transaction_id": tx.transaction_id,
            "level": assessment.level,
            "score": assessment.score,
            "reasons": assessment.reasons,
            "time": now,
        }

    def register_success(self, tx: Transaction) -> None:
        recipient = self._recipient(tx)
        if recipient is not None:
            self._known_recipients.setdefault(tx.client_id, set()).add(recipient)

    def client_profile(self, client_id: str) -> dict:
        assessments = [v for v in self._assessments.values() if v["client_id"] == client_id]
        counts = {level.value: sum(a["level"] == level for a in assessments) for level in AuditLevel}
        max_level = (AuditLevel.HIGH if counts["high"] else
                     AuditLevel.MEDIUM if counts["medium"] else AuditLevel.LOW)
        all_reasons = sorted({r for a in assessments for r in a["reasons"]})
        return {
            "client_id": client_id,
            "risk_level": max_level.value,
            "transactions_assessed": len(assessments),
            "by_level": counts,
            "triggers": all_reasons,
            "known_recipients": len(self._known_recipients.get(client_id, set())),
        }
