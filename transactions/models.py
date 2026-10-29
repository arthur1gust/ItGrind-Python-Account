from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import uuid4

from accounts.bank_account import BankAccount
from accounts.exceptions import InvalidOperationError


class TransactionType(str, Enum):
    DEPOSIT = "deposit"                  # внесение наличных на счёт
    WITHDRAW = "withdraw"                # выдача наличных со счёта
    TRANSFER = "transfer"                # внутри банка: счёт -> счёт
    EXTERNAL_TRANSFER = "external_transfer"  # в сторонний банк (симуляция)


class TransactionStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRY_PENDING = "retry_pending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Transaction:
    """Описание операции и её жизненный цикл.

    client_id — инициатор, отправитель/получатель определяются полями счетов.
    Для EXTERNAL_TRANSFER recipient_details — реквизиты вне нашего банка.
    execute_at — не раньше указанного момента (наивная локальная datetime).
    """

    def __init__(
        self,
        type: TransactionType,
        amount,
        currency: str,
        client_id: str,
        sender_account_id: str | None = None,
        recipient_account_id: str | None = None,
        recipient_details: str | None = None,
        priority: int = 0,
        execute_at: datetime | None = None,
        max_attempts: int = 3,
    ):
        if not isinstance(type, TransactionType):
            raise ValueError("type должен быть TransactionType")
        if not isinstance(client_id, str) or not client_id.strip():
            raise ValueError("Необходимо указать client_id")
        if currency not in BankAccount.ALLOWED_CURRENCIES:
            raise ValueError("Неподдерживаемая валюта")
        amount = BankAccount._validate_amount(amount)
        if amount != amount.quantize(Decimal("0.01")):
            raise ValueError("Сумма операции: не более двух знаков после запятой")
        if type in (TransactionType.WITHDRAW, TransactionType.TRANSFER,
                    TransactionType.EXTERNAL_TRANSFER) and not sender_account_id:
            raise ValueError("Укажите счёт отправителя")
        if type in (TransactionType.DEPOSIT, TransactionType.TRANSFER) and not recipient_account_id:
            raise ValueError("Укажите счёт получателя")
        if type == TransactionType.EXTERNAL_TRANSFER and not recipient_details:
            raise ValueError("Укажите реквизиты внешнего получателя")
        if type == TransactionType.TRANSFER and sender_account_id == recipient_account_id:
            raise ValueError("Перевод самому себе на тот же счёт запрещён")
        if type == TransactionType.DEPOSIT and sender_account_id:
            raise ValueError("Для внесения наличных счёт отправителя не нужен")
        if type == TransactionType.WITHDRAW and recipient_account_id:
            raise ValueError("Для снятия наличных счёт получателя не нужен")
        if not isinstance(priority, int) or isinstance(priority, bool):
            raise ValueError("priority должен быть целым числом")
        if execute_at is not None and not isinstance(execute_at, datetime):
            raise ValueError("execute_at должен быть datetime")
        if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
            raise ValueError("max_attempts должен быть положительным целым числом")

        self.transaction_id = uuid4().hex
        self.type = type
        self.amount = amount
        self.currency = currency
        self.client_id = client_id
        self.sender_account_id = sender_account_id
        self.recipient_account_id = recipient_account_id
        self.recipient_details = recipient_details
        self.priority = priority
        self.execute_at = execute_at
        self.max_attempts = max_attempts
        self.status = TransactionStatus.PENDING
        self.fee = Decimal("0.00")
        self.converted_amount: Decimal | None = None
        self.converted_currency: str | None = None
        self.failure_reason: str | None = None
        self.attempts = 0
        self.created_at = datetime.now()
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None
        self.next_attempt_at: datetime | None = None
        self.error_log: list[dict] = []
        # День 5: заполняются только если подключён RiskAnalyzer.
        self.risk_level: str | None = None
        self.risk_reasons: list[str] = []

    def as_dict(self) -> dict:
        """Без изменяемых внутренних объектов и с удобными значениями для вывода."""
        return {
            "transaction_id": self.transaction_id,
            "type": self.type.value,
            "status": self.status.value,
            "amount": str(self.amount),
            "currency": self.currency,
            "fee": str(self.fee),
            "client_id": self.client_id,
            "sender_account_id": self.sender_account_id,
            "recipient_account_id": self.recipient_account_id,
            "recipient_details": self.recipient_details,
            "converted_amount": str(self.converted_amount) if self.converted_amount is not None else None,
            "converted_currency": self.converted_currency,
            "priority": self.priority,
            "attempts": self.attempts,
            "failure_reason": self.failure_reason,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "execute_at": self.execute_at.isoformat() if self.execute_at else None,
            "next_attempt_at": self.next_attempt_at.isoformat() if self.next_attempt_at else None,
            "error_log": [entry.copy() for entry in self.error_log],
            "risk_level": self.risk_level,
            "risk_reasons": self.risk_reasons.copy(),
        }
