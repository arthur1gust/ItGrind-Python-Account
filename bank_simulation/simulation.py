from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Callable

from accounts.enums import AccountStatus
from audit import AuditLevel, AuditLog, RiskAnalyzer
from bank_system import Bank, Client
from transactions import (
    TemporaryProcessingError, Transaction, TransactionProcessor,
    TransactionQueue, TransactionStatus, TransactionType,
)
from .reporting import DemoReports


class Day6Simulation:
    def __init__(self, output_dir: str | Path | None = None,
                 sink: Callable[[str], None] | None = print):
        self._clock = [datetime(2026, 10, 26, 10, 0)]
        self._emit = sink if sink is not None else (lambda _: None)
        self.output_dir = Path(output_dir) if output_dir is not None else (
            Path(__file__).resolve().parents[1] / "audit_output"
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.output_dir / "day6_audit.jsonl"
        self.report_file = self.output_dir / "day6_reports.json"
        # Только путь Дня 6: чужие файлы и логи Дня 5 не трогаем.
        self.log_file.write_text("", encoding="utf-8")
        self.bank = Bank("ItGrind Day6 Bank", now_provider=self.now)
        self.audit_log = AuditLog(log_file=self.log_file, now_provider=self.now)
        self.risk_analyzer = RiskAnalyzer(large_amount=10000,
                                          very_large_amount=50000,
                                          frequency_limit=3,
                                          frequency_window_seconds=60)
        self._flaky_transaction_id = None
        self._failed_gateway_ids = set()

        def fake_gateway(tx: Transaction) -> None:
            if (tx.transaction_id == self._flaky_transaction_id
                    and tx.transaction_id not in self._failed_gateway_ids):
                self._failed_gateway_ids.add(tx.transaction_id)
                raise TemporaryProcessingError("Учебный временный сбой платёжного шлюза")

        self.processor = TransactionProcessor(
            self.bank,
            rates={("RUB", "USD"): "0.0125", ("USD", "RUB"): "80"},
            gateway=fake_gateway,
            audit_log=self.audit_log,
            risk_analyzer=self.risk_analyzer,
            retry_delay_seconds=30,
        )
        self.queue = TransactionQueue()
        self.clients: dict[str, str] = {}
        self.accounts: dict[str, str] = {}
        self.transactions: list[Transaction] = []
        self.events: list[str] = []
        self.completed = False
        self._initialize()
        self.reports = DemoReports(self)

    def now(self) -> datetime:
        return self._clock[0]

    def _log(self, message: str) -> None:
        stamp = self.now().strftime("%Y-%m-%d %H:%M:%S")
        record = f"[{stamp}] {message}"
        self.events.append(record)
        self._emit(record)

    def _initialize(self) -> None:
        people = (
            ("Иван Петров", 32), ("Анна Сидорова", 29),
            ("Павел Кузнецов", 35), ("Мария Смирнова", 27),
            ("Сергей Волков", 41), ("Елена Морозова", 30),
            ("Дмитрий Орлов", 38),
        )
        for n, (name, age) in enumerate(people, 1):
            client_id = self.bank.add_client(Client(
                name, age, f"+700000000{n:02}", f"user{n}@example.local", "DemoPassword123"
            ))
            self.clients[name] = client_id

        def account(key, name, kind="bank", balance=0, currency="RUB", **options):
            account_id = self.bank.open_account(
                self.clients[name], kind, balance=balance, currency=currency, **options
            )
            self.accounts[key] = account_id
            return account_id

        account("ivan_rub", "Иван Петров", balance=100000)
        account("ivan_savings", "Иван Петров", "savings", 20000, min_balance=2000)
        account("anna_rub", "Анна Сидорова", balance=35000)
        account("anna_usd", "Анна Сидорова", balance=1000, currency="USD")
        account("pavel_rub", "Павел Кузнецов", balance=15000)
        account("pavel_premium", "Павел Кузнецов", "premium", 2000, overdraft_limit=5000)
        account("maria_rub", "Мария Смирнова", balance=40000)
        account("maria_investment", "Мария Смирнова", "investment", 12000)
        account("sergey_rub", "Сергей Волков", balance=25000)
        account("sergey_usd", "Сергей Волков", balance=700, currency="USD")
        account("elena_rub", "Елена Морозова", balance=30000)
        account("elena_savings", "Елена Морозова", "savings", 15000, min_balance=1000)
        account("dmitry_rub", "Дмитрий Орлов", balance=18000)
        account("dmitry_frozen", "Дмитрий Орлов", balance=800)
        self.bank.freeze_account(self.accounts["dmitry_frozen"])
        # Показываем совместимость со вторым днём: часть средств инвестирована.
        self.bank.invest(self.clients["Мария Смирнова"], self.accounts["maria_investment"],
                         "stocks", 3000)
        self._log("[INIT] Созданы 7 клиентов и 14 счетов (включая Savings, Premium, Investment и Frozen)")

    def _tx(self, op: TransactionType, name: str, amount, source=None, target=None,
            currency="RUB", priority=0, details=None, execute_at=None):
        return Transaction(
            type=op, amount=amount, currency=currency, client_id=self.clients[name],
            sender_account_id=self.accounts[source] if source else None,
            recipient_account_id=self.accounts[target] if target else None,
            recipient_details=details, priority=priority, execute_at=execute_at,
        )

    def deposit(self, who, target, amount, currency="RUB", priority=0, execute_at=None):
        return self._tx(TransactionType.DEPOSIT, who, amount, target=target,
                        currency=currency, priority=priority, execute_at=execute_at)

    def withdraw(self, who, source, amount, currency="RUB", priority=0):
        return self._tx(TransactionType.WITHDRAW, who, amount, source=source,
                        currency=currency, priority=priority)

    def transfer(self, who, source, target, amount, currency="RUB", priority=0):
        return self._tx(TransactionType.TRANSFER, who, amount, source, target,
                        currency=currency, priority=priority)

    def external(self, who, source, amount, external_details, priority=0):
        return self._tx(TransactionType.EXTERNAL_TRANSFER, who, amount, source,
                        details=external_details, priority=priority)

    def enqueue(self, *items: Transaction) -> None:
        for tx in items:
            self.queue.add(tx)
            self.transactions.append(tx)
            self.audit_log.log(
                "TRANSACTION_QUEUED", AuditLevel.LOW,
                client_id=tx.client_id, transaction_id=tx.transaction_id,
                details=f"{tx.type.value}:{tx.amount}:{tx.currency}:priority={tx.priority}",
                now=self.now(),
            )
            self._log(f"[QUEUE] {len(self.transactions):02} {tx.transaction_id[:8]} "
                      f"{tx.type.value:17} {tx.amount:>8} {tx.currency} "
                      f"priority={tx.priority} size={len(self.queue)}")

    def process_ready(self) -> list[Transaction]:
        results = self.processor.run_ready(self.queue, now=self.now())
        for tx in results:
            marker = {
                TransactionStatus.COMPLETED: "EXEC",
                TransactionStatus.RETRY_PENDING: "RETRY",
                TransactionStatus.FAILED: "BLOCK" if tx.risk_level == "high" else "FAIL",
            }.get(tx.status, "STATE")
            self._log(f"[{marker}] {tx.transaction_id[:8]} {tx.type.value:17} "
                      f"status={tx.status.value:13} risk={tx.risk_level or '-':6} "
                      f"attempts={tx.attempts} "
                      f"{(tx.failure_reason or '')[:105]}")
        return results

    def cancel(self, tx: Transaction) -> bool:
        cancelled = self.queue.cancel(tx.transaction_id, now=self.now())
        if cancelled:
            self.audit_log.log("TRANSACTION_CANCELLED", AuditLevel.LOW,
                               client_id=tx.client_id, transaction_id=tx.transaction_id,
                               details="Отмена до исполнения", now=self.now())
            self._log(f"[CANCEL] {tx.transaction_id[:8]} отменена до обработки")
        return cancelled

    def _ordinary_transactions(self) -> list[Transaction]:
        """30 правдоподобных операций разного типа, в том числе валютных."""
        return [
            self.deposit("Иван Петров", "ivan_rub", 500),
            self.deposit("Анна Сидорова", "anna_rub", 750),
            self.withdraw("Павел Кузнецов", "pavel_rub", 200),
            self.deposit("Мария Смирнова", "maria_rub", 1000),
            self.withdraw("Сергей Волков", "sergey_rub", 300),
            self.deposit("Елена Морозова", "elena_rub", 600),
            self.deposit("Дмитрий Орлов", "dmitry_rub", 400),
            self.transfer("Иван Петров", "ivan_rub", "anna_rub", 500),
            self.transfer("Анна Сидорова", "anna_rub", "sergey_rub", 600),
            self.transfer("Павел Кузнецов", "pavel_rub", "maria_rub", 100),
            self.transfer("Мария Смирнова", "maria_rub", "elena_rub", 250),
            self.transfer("Сергей Волков", "sergey_rub", "dmitry_rub", 200),
            self.transfer("Елена Морозова", "elena_rub", "ivan_rub", 175),
            self.transfer("Дмитрий Орлов", "dmitry_rub", "anna_rub", 200),
            self.transfer("Иван Петров", "ivan_rub", "anna_rub", 700),
            self.transfer("Анна Сидорова", "anna_rub", "sergey_rub", 150),
            self.transfer("Павел Кузнецов", "pavel_rub", "maria_rub", 200),
            self.transfer("Мария Смирнова", "maria_rub", "elena_rub", 300),
            self.transfer("Сергей Волков", "sergey_rub", "dmitry_rub", 100),
            self.transfer("Елена Морозова", "elena_rub", "ivan_rub", 100),
            self.transfer("Дмитрий Орлов", "dmitry_rub", "anna_rub", 125),
            self.transfer("Иван Петров", "ivan_rub", "anna_usd", 800),
            self.transfer("Анна Сидорова", "anna_usd", "sergey_rub", 20, currency="USD"),
            self.transfer("Сергей Волков", "sergey_usd", "anna_usd", 10, currency="USD"),
            self.withdraw("Павел Кузнецов", "pavel_premium", 2500),
            self.external("Иван Петров", "ivan_rub", 700, "ext:ivan"),
            self.external("Павел Кузнецов", "pavel_rub", 500, "ext:pavel"),
            self.external("Мария Смирнова", "maria_rub", 650, "ext:maria"),
            self.external("Сергей Волков", "sergey_rub", 300, "ext:sergey"),
            self.external("Дмитрий Орлов", "dmitry_rub", 250, "ext:dmitry"),
        ]

    def run(self) -> dict:
        """Выполняется один раз. Для нового запуска создавай новую симуляцию."""
        if self.completed:
            raise RuntimeError("Демонстрация уже завершена — создайте новый Day6Simulation")
        self._log("[START] День 6: 45 транзакций (обычные, ошибочные, рискованные)")
        everyday = self._ordinary_transactions()
        assert len(everyday) == 30
        # Группы по шесть: время двигаем на 2 минуты, чтобы не вызвать
        # ложную массовую блокировку по частоте в обычном сценарии.
        for start in range(0, 30, 6):
            batch = everyday[start:start + 6]
            for n, tx in enumerate(batch, start):
                tx.priority = n % 4  # демонстрация порядка обработки по приоритетам
            self.enqueue(*batch)
            self.process_ready()
            self._clock[0] += timedelta(minutes=2)

        self._log("[STAGE] Ошибочные и подозрительные операции 31–36")
        anomalies = [
            self.transfer("Анна Сидорова", "anna_rub", "elena_rub", 12000),  # сумма + новый
            self.withdraw("Иван Петров", "ivan_savings", 19500),             # остаток < min
            self.deposit("Дмитрий Орлов", "dmitry_frozen", 100),             # заморожен
            self.withdraw("Анна Сидорова", "anna_rub", 45000),               # недостаточно
            self.transfer("Елена Морозова", "elena_rub", "maria_rub", 11000), # сумма + новый
            self.deposit("Сергей Волков", "sergey_rub", 55000),              # очень крупная
        ]
        self.enqueue(*anomalies)
        self.process_ready()

        self._clock[0] += timedelta(minutes=2)
        self._log("[STAGE] Частые операции 37–40: четвёртая должна заблокироваться")
        burst = [self.transfer("Павел Кузнецов", "pavel_rub", "maria_rub", amount)
                 for amount in (50, 60, 70, 80)]
        self.enqueue(*burst)
        self.process_ready()

        self._clock[0] = datetime(2026, 10, 27, 2, 0)
        self._log("[STAGE] Ночная операция 41")
        night = self.deposit("Иван Петров", "ivan_rub", 150)
        self.enqueue(night)
        self.process_ready()

        self._clock[0] = datetime(2026, 10, 27, 11, 0)
        self._log("[STAGE] Повтор, отложенный платёж и отмена 42–44")
        retry = self.external("Мария Смирнова", "maria_rub", 350, "ext:maria")
        self._flaky_transaction_id = retry.transaction_id
        future = self.deposit("Анна Сидорова", "anna_usd", 25, currency="USD",
                              execute_at=self.now() + timedelta(minutes=60))
        canceled = self.withdraw("Елена Морозова", "elena_rub", 90)
        self.enqueue(retry, future, canceled)
        assert self.cancel(canceled)
        first = self.process_ready()
        assert len(first) == 1 and retry.status == TransactionStatus.RETRY_PENDING

        self._clock[0] += timedelta(seconds=31)
        self._log("[TIME] Прошло 31 секунда — повторяем внешний перевод")
        self.process_ready()
        assert retry.status == TransactionStatus.COMPLETED and retry.attempts == 2

        self._clock[0] += timedelta(minutes=61)
        self._log("[TIME] Наступило время отложенного пополнения")
        self.process_ready()
        assert future.status == TransactionStatus.COMPLETED

        self._clock[0] += timedelta(minutes=2)
        self._log("[STAGE] Последняя операция 45 — новый получатель, средний риск")
        final = self.transfer("Дмитрий Орлов", "dmitry_rub", "elena_rub", 350)
        self.enqueue(final)
        self.process_ready()

        self.completed = True
        report = self.reports.make_report()
        self.report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        self._log(f"[DONE] Очередь={len(self.queue)}; отчёт={self.report_file}")
        if self.audit_log.persistence_errors:
            self._log(f"[WARN] Ошибок записи аудита: {len(self.audit_log.persistence_errors)}")
        return report
