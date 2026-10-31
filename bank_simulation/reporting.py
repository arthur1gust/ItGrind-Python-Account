from collections import Counter, defaultdict
from decimal import Decimal

from audit import AuditReports
from transactions import TransactionStatus


class DemoReports:
    """Читает уже созданные объекты; не меняет балансы, очередь или риск-историю."""

    def __init__(self, simulation):
        self.demo = simulation
        self.audit = AuditReports(simulation.audit_log, simulation.risk_analyzer)

    def client_accounts(self, client_id: str) -> list[dict]:
        if client_id not in self.demo.bank._clients:
            raise KeyError("Неизвестный клиент")
        return self.demo.bank.search_accounts(client_id=client_id)

    def client_history(self, client_id: str, limit: int | None = None) -> list[dict]:
        """Исходящие операции + входящие на счета клиента, включая ошибки и отмены."""
        if client_id not in self.demo.bank._clients:
            raise KeyError("Неизвестный клиент")
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError("limit должен быть целым неотрицательным числом")
        own = set(self.demo.bank._clients[client_id].account_numbers)
        history = [tx.as_dict() for tx in self.demo.transactions
                   if tx.client_id == client_id or tx.recipient_account_id in own]
        return history[-limit:] if limit is not None and limit > 0 else ([] if limit == 0 else history)

    def transaction_statistics(self) -> dict:
        by_status = Counter(tx.status.value for tx in self.demo.transactions)
        by_type = Counter(tx.type.value for tx in self.demo.transactions)
        by_risk = Counter(tx.risk_level or "not_assessed" for tx in self.demo.transactions)
        by_error = Counter(
            tx.error_log[-1]["error_type"] for tx in self.demo.transactions
            if tx.status == TransactionStatus.FAILED and tx.error_log
        )
        fees = defaultdict(lambda: Decimal("0"))
        amount_completed = defaultdict(lambda: Decimal("0"))
        for tx in self.demo.transactions:
            if tx.status == TransactionStatus.COMPLETED:
                fees[tx.currency] += tx.fee
                amount_completed[tx.currency] += tx.amount
        return {
            "total": len(self.demo.transactions),
            "by_status": dict(sorted(by_status.items())),
            "by_type": dict(sorted(by_type.items())),
            "by_risk": dict(sorted(by_risk.items())),
            "by_error_type": dict(sorted(by_error.items())),
            "retries": sum(max(tx.attempts - 1, 0) for tx in self.demo.transactions),
            "fees_by_currency": {key: str(value) for key, value in sorted(fees.items())},
            "completed_amount_by_currency": {
                key: str(value) for key, value in sorted(amount_completed.items())
            },
            "queue_remaining": len(self.demo.queue),
        }

    def top_clients(self, limit: int = 3, currency: str = "RUB") -> list[dict]:
        """Рейтинг по учётной стоимости в одной валюте, включая портфель по себестоимости."""
        if type(limit) is not int or limit < 1:
            raise ValueError("limit должен быть положительным целым")
        return [{**row, "total_balance": str(row["total_balance"])}
                for row in self.demo.bank.get_clients_ranking(currency=currency)[:limit]]

    def make_report(self) -> dict:
        return {
            "demo": "ItGrind Day6",
            "generated_at_simulated": self.demo.now().isoformat(timespec="seconds"),
            "clients_count": len(self.demo.clients),
            "accounts_count": len(self.demo.accounts),
            "transaction_statistics": self.transaction_statistics(),
            "top_3_clients_rub": self.top_clients(3, currency="RUB"),
            "total_balance_by_currency": {
                currency: str(balance)
                for currency, balance in sorted(self.demo.bank.get_total_balance().items())
            },
            "suspicious_operations": self.audit.suspicious_operations(),
            "high_risk_operations": self.audit.suspicious_operations(include_medium=False),
            "risk_profiles": {
                name: self.audit.client_risk_profile(client_id)
                for name, client_id in self.demo.clients.items()
            },
            "error_statistics": self.audit.error_statistics(),
            "transactions": [tx.as_dict() for tx in self.demo.transactions],
            "clients": [
                {"full_name": name, "client_id": client_id,
                 "accounts": self.client_accounts(client_id)}
                for name, client_id in self.demo.clients.items()
            ],
        }
