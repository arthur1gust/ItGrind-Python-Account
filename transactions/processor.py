from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable, TYPE_CHECKING

from accounts.enums import AccountStatus
from accounts.exceptions import InsufficientFundsError, InvalidOperationError
from accounts.premium_account import PremiumAccount
from accounts.savings_account import SavingsAccount
from bank_system import Bank

from .exceptions import TemporaryProcessingError, TransactionProcessingError, RiskBlockedError

if TYPE_CHECKING:
    from audit import AuditLog, RiskAnalyzer
from .models import Transaction, TransactionStatus, TransactionType
from .queue import TransactionQueue

CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class TransactionProcessor:
    """Исполняет операции из очереди, сохраняя результат и ошибки в Transaction.

    rates — словарь вида {("RUB", "USD"): "0.012"} с учебными курсами:
    1 единица первой валюты -> указанное количество второй валюты.
    gateway — тестовый вызов для имитации внешнего платежа; не переводит деньги!
    """

    def __init__(
        self,
        bank: Bank,
        rates: dict[tuple[str, str], Decimal | str] | None = None,
        external_fee_rate="0.01",
        external_min_fee="1.00",
        retry_delay_seconds: int = 30,
        gateway: Callable[[Transaction], None] | None = None,
        now_provider: Callable[[], datetime] | None = None,
        audit_log: "AuditLog | None" = None,
        risk_analyzer: "RiskAnalyzer | None" = None,
    ):
        if not isinstance(bank, Bank):
            raise TypeError("Требуется экземпляр Bank из Дня 3")
        self.bank = bank
        self._now = now_provider if now_provider is not None else bank._now
        if not callable(self._now):
            raise TypeError("now_provider должен быть функцией")
        if gateway is not None and not callable(gateway):
            raise TypeError("gateway должен быть функцией")
        self.gateway = gateway
        # День 5: опциональная интеграция. Старые сценарии Дня 4 не меняются,
        # пока risk_analyzer не передан. В demo_day5 активируем оба модуля.
        self.audit_log = audit_log
        self.risk_analyzer = risk_analyzer
        self.external_fee_rate = self._valid_rate(external_fee_rate, allow_zero=True)
        self.external_min_fee = money(self._valid_rate(external_min_fee, allow_zero=True))
        if type(retry_delay_seconds) is not int or retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds должен быть неотрицательным целым")
        self.retry_delay_seconds = retry_delay_seconds
        self.rates = {}
        for pair, rate in (rates or {}).items():
            if not (isinstance(pair, tuple) and len(pair) == 2):
                raise ValueError("Ключ курса должен иметь вид (исходная валюта, целевая валюта)")
            self.rates[pair] = self._valid_rate(rate)

    @staticmethod
    def _valid_rate(value, allow_zero=False) -> Decimal:
        from accounts.bank_account import BankAccount
        return BankAccount._validate_amount(value, allow_zero=allow_zero)

    @staticmethod
    def _check_open(account) -> None:
        account._check_status()  # единые проверки замороженного/закрытого счёта из Дня 1

    @staticmethod
    def _assert_currency(account, currency: str) -> None:
        if account.currency != currency:
            raise TransactionProcessingError("Валюта операции не совпадает с валютой отправителя")

    @staticmethod
    def _ensure_transfer_funds(account, debit: Decimal) -> None:
        """Проверяем достаточность *до* изменения любого из двух счетов.

        Для перевода нет комиссии PremiumAccount.fixed_fee (она относится к
        withdraw наличными); действуют разрешённый овердрафт и min_balance.
        """
        after = account.balance - debit
        if isinstance(account, PremiumAccount):
            if after < -account.overdraft_limit:
                raise InsufficientFundsError("Превышен лимит овердрафта для перевода")
        elif after < 0:
            raise InsufficientFundsError("Недостаточно средств для перевода")
        if isinstance(account, SavingsAccount) and after < account.min_balance:
            raise InvalidOperationError("После перевода нарушится минимальный остаток")

    def _execute(self, tx: Transaction) -> None:
        # Инварианты проверяются до изменения балансов.
        if tx.type == TransactionType.DEPOSIT:
            account = self.bank._get_owned_account(tx.client_id, tx.recipient_account_id)
            self.bank._check_operating_hours(tx.client_id, "transaction_deposit")
            self._check_open(account)
            self._assert_currency(account, tx.currency)
            account.deposit(tx.amount)
            return

        sender = self.bank._get_owned_account(tx.client_id, tx.sender_account_id)
        self.bank._check_operating_hours(tx.client_id, "transaction_" + tx.type.value)
        self._check_open(sender)
        self._assert_currency(sender, tx.currency)

        if tx.type == TransactionType.WITHDRAW:
            sender.withdraw(tx.amount)
            # Существующая логика premium.withdraw удерживает фиксированную комиссию.
            tx.fee = sender.fixed_fee if isinstance(sender, PremiumAccount) else Decimal("0.00")
            return

        if tx.type == TransactionType.TRANSFER:
            recipient = self.bank._get_account(tx.recipient_account_id)
            self._check_open(recipient)
            if recipient.currency == sender.currency:
                credited = tx.amount
            else:
                rate = self.rates.get((sender.currency, recipient.currency))
                if rate is None:
                    raise TransactionProcessingError(
                        f"Нет курса конвертации {sender.currency} -> {recipient.currency}"
                    )
                credited = money(tx.amount * rate)
                if credited <= 0:
                    raise TransactionProcessingError("Сумма после конвертации округлилась до нуля")
            self._ensure_transfer_funds(sender, tx.amount)
            # Атомарность для модели в памяти: все проверки пройдены до обеих записей.
            sender._balance -= tx.amount
            recipient._balance += credited
            tx.converted_amount = credited
            tx.converted_currency = recipient.currency
            return

        if tx.type == TransactionType.EXTERNAL_TRANSFER:
            tx.fee = money(max(tx.amount * self.external_fee_rate, self.external_min_fee))
            total_debit = tx.amount + tx.fee
            self._ensure_transfer_funds(sender, total_debit)
            if self.gateway is not None:
                # Шлюз может выбросить TemporaryProcessingError — тогда денег не списываем.
                self.gateway(tx)
            sender._balance -= total_debit
            # Внешний перевод учебный: фиксируем списание; реального зачисления нет.
            return

        raise TransactionProcessingError("Неизвестный тип транзакции")

    def _audit(self, event_type: str, level, tx: Transaction, now: datetime,
               details: str = "", error_type: str | None = None) -> None:
        """Журнал не должен ошибкой файлового ввода-вывода менять статус платежа.

        AuditLog.log() хранит событие в RAM, а проблемы записи на диск
        отражает в persistence_errors. Секретные данные сюда не передаём.
        """
        if self.audit_log is not None:
            self.audit_log.log(
                event_type=event_type, level=level,
                client_id=tx.client_id, transaction_id=tx.transaction_id,
                details=details, error_type=error_type, now=now,
            )

    def process(self, tx: Transaction, queue: TransactionQueue | None = None,
                now: datetime | None = None) -> Transaction:
        """Одна попытка: День 5 проверяет риск ДО любого изменения балансов."""
        from audit import AuditLevel  # локальный импорт исключает циклическую загрузку модулей

        now = now or self._now()
        if tx.status not in (TransactionStatus.PENDING, TransactionStatus.RETRY_PENDING):
            raise ValueError("Эта транзакция уже обрабатывалась или завершена")
        if tx.execute_at is not None and now < tx.execute_at:
            raise ValueError("Время выполнения ещё не наступило")
        if tx.next_attempt_at is not None and now < tx.next_attempt_at:
            raise ValueError("Время повторной попытки ещё не наступило")

        tx.status = TransactionStatus.PROCESSING
        tx.attempts += 1
        tx.started_at = now
        tx.failure_reason = None
        try:
            if self.risk_analyzer is not None:
                assessment = self.risk_analyzer.analyze(tx, now)
                tx.risk_level = assessment.level.value
                tx.risk_reasons = list(assessment.reasons)
                self.risk_analyzer.register_assessment(tx, assessment, now)
                self._audit("RISK_ASSESSED", assessment.level, tx, now,
                            details=",".join(assessment.reasons) or "NO_RISK_FLAGS")
                if assessment.level == AuditLevel.HIGH:
                    raise RiskBlockedError(
                        "Операция заблокирована риск-контролем: " + ", ".join(assessment.reasons)
                    )
            self._execute(tx)
        except Exception as error:
            # Явно фиксируем неуспех; повторяем ТОЛЬКО временные ошибки.
            tx.failure_reason = str(error) or type(error).__name__
            tx.error_log.append({
                "time": now.isoformat(timespec="seconds"),
                "attempt": tx.attempts,
                "error_type": type(error).__name__,
                "reason": tx.failure_reason,
            })
            self.bank._flag_suspicious(tx.client_id, "TRANSACTION_ERROR", tx.transaction_id)
            if isinstance(error, RiskBlockedError):
                self.bank._flag_suspicious(tx.client_id, "RISK_BLOCKED", tx.transaction_id)
                self._audit("TRANSACTION_BLOCKED", AuditLevel.HIGH, tx, now,
                            details=",".join(tx.risk_reasons), error_type=type(error).__name__)
                tx.status = TransactionStatus.FAILED
                tx.finished_at = now
                tx.next_attempt_at = None
            elif isinstance(error, TemporaryProcessingError) and tx.attempts < tx.max_attempts:
                tx.status = TransactionStatus.RETRY_PENDING
                tx.next_attempt_at = now + timedelta(
                    seconds=self.retry_delay_seconds * (2 ** (tx.attempts - 1))
                )
                self._audit("TRANSACTION_RETRY", AuditLevel.MEDIUM, tx, now,
                            details=tx.failure_reason, error_type=type(error).__name__)
                if queue is not None:
                    queue.add(tx)
            else:
                tx.status = TransactionStatus.FAILED
                tx.finished_at = now
                tx.next_attempt_at = None
                self._audit("TRANSACTION_FAILED", AuditLevel.MEDIUM, tx, now,
                            details=tx.failure_reason, error_type=type(error).__name__)
        else:
            tx.status = TransactionStatus.COMPLETED
            tx.finished_at = now
            tx.next_attempt_at = None
            tx.failure_reason = None
            if self.risk_analyzer is not None:
                self.risk_analyzer.register_success(tx)
            self._audit("TRANSACTION_COMPLETED", AuditLevel.LOW, tx, now,
                        details=f"{tx.type.value}:{tx.amount}:{tx.currency}")
        return tx

    def process_next(self, queue: TransactionQueue, now: datetime | None = None) -> Transaction | None:
        now = now or self._now()
        tx = queue.pop_ready(now)
        return self.process(tx, queue=queue, now=now) if tx is not None else None

    def run_ready(self, queue: TransactionQueue, now: datetime | None = None,
                  limit: int | None = None) -> list[Transaction]:
        """Выполнить все ДОСТУПНЫЕ на данный момент операции (не ожидать задержанные)."""
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError("limit должен быть неотрицательным целым")
        now = now or self._now()
        results = []
        while limit is None or len(results) < limit:
            tx = self.process_next(queue, now=now)
            if tx is None:
                break
            results.append(tx)
        return results
