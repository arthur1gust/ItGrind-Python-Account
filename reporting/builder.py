import csv
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from enum import Enum
import json
from pathlib import Path

from accounts.bank_account import BankAccount
from bank_simulation import Day6Simulation
from transactions import TransactionStatus, TransactionType


class ReportBuilder:
    """Готовит отчёты по клиенту, банку и рискам, экспортирует CSV/JSON/TXT и PNG.

    Источник данных должен быть окончательно сформирован: demo.run() уже вызван.
    Публичные отчёты содержат сериализуемые примитивы и не ссылаются на
    изменяемые внутренние словари банка.
    """

    def __init__(self, demo: Day6Simulation):
        if not isinstance(demo, Day6Simulation):
            raise TypeError("Нужен объект Day6Simulation")
        if not demo.completed:
            raise ValueError("Перед построением отчётов выполните demo.run()")
        self.demo = demo

    def _client(self, client_id: str):
        if client_id not in self.demo.bank._clients:
            raise KeyError("Клиент не найден")
        return self.demo.bank._clients[client_id]

    def client_report(self, client_id: str) -> dict:
        """Счета, история входящих/исходящих, риск-профиль, итоги по валютам."""
        client = self._client(client_id)
        accounts = self.demo.reports.client_accounts(client_id)
        history = self.demo.reports.client_history(client_id)
        totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for account_id in client.account_numbers:
            account = self.demo.bank._get_account(account_id)
            totals[account.currency] += self.demo.bank._account_value(account)
        status_counts = Counter(row["status"] for row in history)
        return {
            "report_type": "client",
            "generated_at": self.demo.now().isoformat(timespec="seconds"),
            "client": {
                "client_id": client_id,
                "full_name": client.full_name,
                "status": client.status.value,
            },
            "accounts": accounts,
            "total_balance_by_currency": {k: str(v) for k, v in sorted(totals.items())},
            "transactions_count": len(history),
            "transaction_statuses": dict(sorted(status_counts.items())),
            "transactions": history,
            "risk_profile": self.demo.reports.audit.client_risk_profile(client_id),
        }

    def bank_report(self) -> dict:
        """Итоговые показатели банка, рейтинг RUB и статистика операций."""
        return {
            "report_type": "bank",
            "generated_at": self.demo.now().isoformat(timespec="seconds"),
            "name": self.demo.bank.name,
            "clients_count": len(self.demo.clients),
            "accounts_count": len(self.demo.accounts),
            "total_balance_by_currency": {
                k: str(v) for k, v in sorted(self.demo.bank.get_total_balance().items())
            },
            "top_3_clients_rub": self.demo.reports.top_clients(3, currency="RUB"),
            "transaction_statistics": self.demo.reports.transaction_statistics(),
        }

    def risk_report(self) -> dict:
        """Оценки риска, блокировки и ошибки по данным существующего AuditLog."""
        events = self.demo.reports.audit
        return {
            "report_type": "risk",
            "generated_at": self.demo.now().isoformat(timespec="seconds"),
            "by_risk_level": dict(sorted(Counter(
                tx.risk_level or "not_assessed" for tx in self.demo.transactions
            ).items())),
            "blocked_operations": [tx.as_dict() for tx in self.demo.transactions
                                   if tx.status == TransactionStatus.FAILED
                                   and any(err["error_type"] == "RiskBlockedError"
                                           for err in tx.error_log)],
            "suspicious_operations": events.suspicious_operations(),
            "high_risk_events": events.suspicious_operations(include_medium=False),
            "client_risk_profiles": {name: events.client_risk_profile(cid)
                                     for name, cid in self.demo.clients.items()},
            "error_statistics": events.error_statistics(),
        }

    @staticmethod
    def _check_report(report: dict) -> None:
        if not isinstance(report, dict) or report.get("report_type") not in (
            "client", "bank", "risk"
        ):
            raise ValueError("Ожидался отчёт client/bank/risk, созданный ReportBuilder")

    def text_report(self, report: dict) -> str:
        """Краткое читаемое резюме. Для детализации доступны JSON/CSV."""
        self._check_report(report)
        kind = report["report_type"]
        lines = [f"ITGRIND — ОТЧЁТ {kind.upper()}", f"Время: {report['generated_at']}", "=" * 60]
        if kind == "client":
            data = report["client"]
            lines += [f"Клиент: {data['full_name']} ({data['client_id']})",
                      f"Статус: {data['status']}",
                      f"Счетов: {len(report['accounts'])}",
                      f"Операций в истории: {report['transactions_count']}",
                      f"Статусы: {report['transaction_statuses']}",
                      f"Риск: {report['risk_profile']['risk_level']}"]
            lines.append("Балансы по валютам:")
            for currency, value in report["total_balance_by_currency"].items():
                lines.append(f"  {currency}: {value}")
            lines.append("Счета:")
            for acc in report["accounts"]:
                lines.append(f"  {acc['account_id']} | {acc['balance']} {acc['currency']} "
                             f"| {acc['status']}")
        elif kind == "bank":
            stats = report["transaction_statistics"]
            lines += [f"Банк: {report['name']}", f"Клиентов: {report['clients_count']}",
                      f"Счетов: {report['accounts_count']}",
                      f"Транзакций: {stats['total']}",
                      f"Статусы: {stats['by_status']}",
                      f"Типы: {stats['by_type']}",
                      f"Повторных попыток: {stats['retries']}",
                      "Совокупная учётная стоимость по валютам:"]
            for currency, value in report["total_balance_by_currency"].items():
                lines.append(f"  {currency}: {value}")
            lines.append("Топ-3 клиентов (RUB):")
            for index, row in enumerate(report["top_3_clients_rub"], 1):
                lines.append(f"  {index}. {row['full_name']}: {row['total_balance']} RUB")
        else:
            errors = report["error_statistics"]
            lines += [f"Риски: {report['by_risk_level']}",
                      f"Заблокировано риск-контролем: {len(report['blocked_operations'])}",
                      f"Подозрительных оценок: {len(report['suspicious_operations'])}",
                      f"Событий HIGH: {len(report['high_risk_events'])}",
                      f"Ошибок и блокировок: {errors['total_error_events']}",
                      f"Детализация ошибок: {errors['by_error_type']}",
                      "Заблокированные транзакции:"]
            for tx in report["blocked_operations"]:
                lines.append(f"  {tx['transaction_id'][:8]}: {tx['amount']} {tx['currency']} "
                             f"| {', '.join(tx['risk_reasons'])}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _json_default(obj):
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Enum):
            return obj.value
        raise TypeError(f"Нельзя сериализовать {type(obj).__name__}")

    def export_to_json(self, report: dict, path: str | Path) -> Path:
        self._check_report(report)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                          default=self._json_default) + "\n", encoding="utf-8")
        return destination

    @staticmethod
    def _csv_rows(value, path: tuple[str, ...] = ()):
        """Универсальный табличный экспорт вложенных данных без потери полей."""
        if isinstance(value, dict):
            for key, item in value.items():
                yield from ReportBuilder._csv_rows(item, path + (str(key),))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield from ReportBuilder._csv_rows(item, path + (str(index),))
        else:
            section = path[0] if path else "root"
            record = path[1] if len(path) >= 3 else ""
            field = ".".join(path[2:] if len(path) >= 3 else path[1:])
            yield section, record, field, "" if value is None else str(value)

    def export_to_csv(self, report: dict, path: str | Path) -> Path:
        """UTF-8 BOM для Excel: section,record,field,value, включая вложенные поля."""
        self._check_report(report)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", newline="", encoding="utf-8-sig") as target:
            writer = csv.writer(target)
            writer.writerow(("section", "record", "field", "value"))
            writer.writerows(self._csv_rows(report))
        return destination

    def export_to_text(self, report: dict, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(self.text_report(report), encoding="utf-8")
        return destination

    def export_transactions_csv(self, path: str | Path) -> Path:
        """Отдельная таблица: одна транзакция = одна строка, удобно для Excel."""
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        columns = ("transaction_id", "type", "status", "amount", "currency", "fee",
                   "client_id", "sender_account_id", "recipient_account_id", "priority",
                   "attempts", "risk_level", "failure_reason", "started_at", "finished_at")
        with destination.open("w", newline="", encoding="utf-8-sig") as target:
            writer = csv.DictWriter(target, fieldnames=columns)
            writer.writeheader()
            for tx in self.demo.transactions:
                row = tx.as_dict()
                writer.writerow({column: row.get(column) for column in columns})
        return destination

    @staticmethod
    def _transaction_deltas(tx) -> list[tuple[str, Decimal]]:
        """Изменения денежных остатков по счетам; только успешные проводки.

        Инвестиции в Day6 совершаются до первой транзакции симуляции и потому
        учитываются как неизменный вклад в суммарную учётную стоимость.
        """
        if tx.status != TransactionStatus.COMPLETED:
            return []
        if tx.type == TransactionType.DEPOSIT:
            return [(tx.recipient_account_id, tx.amount)]
        if tx.type in (TransactionType.WITHDRAW, TransactionType.EXTERNAL_TRANSFER):
            return [(tx.sender_account_id, -(tx.amount + tx.fee))]
        if tx.type == TransactionType.TRANSFER:
            return [(tx.sender_account_id, -tx.amount),
                    (tx.recipient_account_id, tx.converted_amount)]
        return []

    def balance_history(self, currency: str = "RUB", client_id: str | None = None) -> list[dict]:
        """Реконструирует баланс ДО и ПОСЛЕ успешных операций по timestamps.

        Отталкиваемся от итоговой учётной стоимости, вычитаем все дельты,
        а затем добавляем их в хронологическом порядке. Это реконструкция
        событий Day6, НЕ банковский ledger и не рыночная переоценка активов.
        Несколько транзакций в один timestamp агрегируются в одну точку.
        """
        if currency not in BankAccount.ALLOWED_CURRENCIES:
            raise ValueError("Неподдерживаемая валюта")
        if client_id is not None:
            self._client(client_id)
        bank = self.demo.bank
        selected = {account_id: account for account_id, account in bank._accounts.items()
                    if account.currency == currency and (
                        client_id is None or bank._account_owners[account_id] == client_id
                    )}
        total = sum((bank._account_value(acc) for acc in selected.values()), Decimal("0"))
        changes: dict[datetime, Decimal] = defaultdict(lambda: Decimal("0"))
        for tx in self.demo.transactions:
            if tx.status != TransactionStatus.COMPLETED:
                continue
            assert tx.finished_at is not None
            for account_id, delta in self._transaction_deltas(tx):
                if account_id in selected:
                    changes[tx.finished_at] += delta
        start = total - sum(changes.values(), Decimal("0"))
        result = [{"step": 0, "time": None, "label": "До операций", "balance": str(start)}]
        balance = start
        for index, when in enumerate(sorted(changes), 1):
            balance += changes[when]
            result.append({"step": index, "time": when.isoformat(timespec="seconds"),
                           "label": when.strftime("%d.%m %H:%M"), "balance": str(balance)})
        if balance != total:
            raise RuntimeError("Ошибка реконструкции изменения баланса")
        return result

    def save_charts(self, output_dir: str | Path, client_id: str | None = None) -> dict[str, Path]:
        """Сохраняет PNG: pie статусов, bar топ-3, bar рисков, line RUB/USD.

        Использует объекты Figure без pyplot и GUI; работает на CI и в PyCharm.
        """
        from matplotlib import rc_context
        from matplotlib.figure import Figure

        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        if client_id is not None:
            self._client(client_id)

        def write(figure: Figure, name: str) -> Path:
            path = output / name
            figure.savefig(path, dpi=150, bbox_inches="tight")
            return path

        stats = self.demo.reports.transaction_statistics()
        files = {}
        with rc_context({"font.family": "DejaVu Sans"}):
            statuses = stats["by_status"]
            fig = Figure(figsize=(7, 5))
            ax = fig.subplots()
            ax.pie(list(statuses.values()), labels=list(statuses), autopct="%1.1f%%",
                   startangle=90)
            ax.set_title("Распределение транзакций по статусам")
            files["transaction_status_pie"] = write(fig, "transaction_status_pie.png")

            ranking = self.demo.reports.top_clients(limit=3, currency="RUB")
            fig = Figure(figsize=(8, 5))
            ax = fig.subplots()
            bars = ax.bar([r["full_name"] for r in ranking],
                          [float(Decimal(r["total_balance"])) for r in ranking])
            ax.bar_label(bars, fmt="%.0f", padding=3)
            ax.set_ylabel("RUB")
            ax.set_title("Топ-3 клиентов по рублёвой учётной стоимости")
            ax.tick_params(axis="x", labelrotation=12)
            fig.subplots_adjust(bottom=0.2)
            files["top_clients_bar"] = write(fig, "top_clients_bar.png")

            risk_counts = stats["by_risk"]
            fig = Figure(figsize=(7, 5))
            ax = fig.subplots()
            bars = ax.bar(list(risk_counts), list(risk_counts.values()))
            ax.bar_label(bars, padding=3)
            ax.set_title("Транзакции по уровням риска")
            ax.set_ylabel("Количество")
            files["risk_levels_bar"] = write(fig, "risk_levels_bar.png")

            available = sorted({acc.currency for acc in self.demo.bank._accounts.values()
                                if client_id is None or
                                self.demo.bank._account_owners[acc.account_id] == client_id})
            for currency in available:
                history = self.balance_history(currency, client_id=client_id)
                fig = Figure(figsize=(11, 5))
                ax = fig.subplots()
                steps = [p["step"] for p in history]
                values = [float(Decimal(p["balance"])) for p in history]
                ax.plot(steps, values, marker="o", linewidth=2, markersize=4)
                tick_every = max(1, len(history) // 10)
                indices = sorted(set(list(range(0, len(history), tick_every)) + [len(history) - 1]))
                ax.set_xticks([steps[i] for i in indices],
                              [history[i]["label"] for i in indices], rotation=40, ha="right")
                ax.set_xlabel("Момент исполнения (точки после групп операций)")
                ax.set_ylabel(currency)
                who = (f"клиент {self._client(client_id).full_name}" if client_id
                       else "банк")
                ax.set_title(f"Движение учётной стоимости: {who} — {currency}")
                ax.grid(alpha=0.25)
                fig.subplots_adjust(bottom=0.27)
                key = f"balance_{currency.lower()}" if client_id is None else f"client_balance_{currency.lower()}"
                files[key] = write(fig, f"{key}.png")
        return files
