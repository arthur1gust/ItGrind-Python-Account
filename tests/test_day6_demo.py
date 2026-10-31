import json
from collections import Counter
from decimal import Decimal

import pytest

from accounts.enums import AccountStatus
from audit import AuditLevel, AuditLog
from bank_simulation import Day6Simulation, DemoReports
from transactions import TransactionStatus, TransactionType


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    destination = tmp_path_factory.mktemp("day6")
    demo = Day6Simulation(output_dir=destination, sink=None)
    report = demo.run()
    return demo, report


def test_initialization_has_seven_clients_and_fourteen_accounts(prepared):
    demo, report = prepared
    assert len(demo.clients) == report["clients_count"] == 7
    assert len(demo.accounts) == report["accounts_count"] == 14
    assert all(len(demo.bank.search_accounts(client_id=client_id)) == 2
               for client_id in demo.clients.values())


def test_45_transactions_have_expected_final_states(prepared):
    demo, report = prepared
    assert len(demo.transactions) == 45
    assert report["transaction_statistics"]["by_status"] == {
        "cancelled": 1, "completed": 36, "failed": 8
    }
    assert all(tx.status in (TransactionStatus.COMPLETED, TransactionStatus.FAILED,
                             TransactionStatus.CANCELLED) for tx in demo.transactions)
    assert len(demo.queue) == 0


def test_queue_and_execution_logging_for_all_transactions(prepared):
    demo, report = prepared
    assert len([event for event in demo.events if "[QUEUE]" in event]) == 45
    assert any("[EXEC]" in event for event in demo.events)
    assert any("[FAIL]" in event for event in demo.events)
    assert any("[BLOCK]" in event for event in demo.events)
    assert any("[RETRY]" in event for event in demo.events)
    assert any("[CANCEL]" in event for event in demo.events)
    assert len(demo.audit_log.filter(event_type="TRANSACTION_QUEUED")) == 45
    assert len(demo.audit_log.filter(event_type="TRANSACTION_COMPLETED")) == 36


def test_exactly_five_risk_blocked_before_balance_changes(prepared):
    demo, report = prepared
    risk_blocked = [tx for tx in demo.transactions
                    if tx.error_log and tx.error_log[-1]["error_type"] == "RiskBlockedError"]
    assert len(risk_blocked) == 5
    assert all(tx.status == TransactionStatus.FAILED and tx.risk_level == "high"
               for tx in risk_blocked)
    assert report["error_statistics"]["blocked"] == 5
    assert len(report["high_risk_operations"]) == 5
    assert all(tx.attempts == 1 for tx in risk_blocked)


def test_five_specific_risk_cases(prepared):
    demo, _ = prepared
    tx = demo.transactions
    assert "LARGE_AMOUNT" in tx[30].risk_reasons
    assert "NEW_RECIPIENT" in tx[30].risk_reasons
    assert "LARGE_AMOUNT" in tx[34].risk_reasons
    assert "VERY_LARGE_AMOUNT" in tx[35].risk_reasons
    assert "HIGH_FREQUENCY" in tx[39].risk_reasons
    assert "NIGHT_OPERATION" in tx[40].risk_reasons


def test_regular_errors_do_not_change_balances(prepared):
    demo, _ = prepared
    for index, error in ((31, "InvalidOperationError"),
                         (32, "AccountFrozenError"),
                         (33, "InsufficientFundsError")):
        tx = demo.transactions[index]
        assert tx.status == TransactionStatus.FAILED
        assert tx.error_log[-1]["error_type"] == error
        assert tx.risk_level != "high"
    savings = demo.bank._get_account(demo.accounts["ivan_savings"])
    frozen = demo.bank._get_account(demo.accounts["dmitry_frozen"])
    assert savings.balance == Decimal("20000")
    assert frozen.status == AccountStatus.FROZEN
    assert frozen.balance == Decimal("800")


def test_currency_conversion_and_accounts(prepared):
    demo, _ = prepared
    rub_to_usd = demo.transactions[21]
    usd_to_rub = demo.transactions[22]
    assert rub_to_usd.status == TransactionStatus.COMPLETED
    assert rub_to_usd.converted_amount == Decimal("10.00")
    assert rub_to_usd.converted_currency == "USD"
    assert usd_to_rub.converted_amount == Decimal("1600.00")
    assert usd_to_rub.converted_currency == "RUB"


def test_premium_overdraft_and_investment_portfolio(prepared):
    demo, _ = prepared
    premium = demo.bank._get_account(demo.accounts["pavel_premium"])
    investment = demo.bank._get_account(demo.accounts["maria_investment"])
    assert premium.balance == Decimal("-550")
    assert demo.transactions[24].fee == Decimal("50")
    assert investment.portfolio["stocks"] == Decimal("3000")
    assert investment.balance == Decimal("9000")


def test_external_transfer_fees(prepared):
    demo, report = prepared
    external = [tx for tx in demo.transactions if tx.type == TransactionType.EXTERNAL_TRANSFER]
    assert len(external) == 6
    assert all(tx.status == TransactionStatus.COMPLETED for tx in external)
    assert demo.transactions[25].fee == Decimal("7.00")
    assert demo.transactions[41].fee == Decimal("3.50")
    assert report["transaction_statistics"]["fees_by_currency"]["RUB"] == "77.50"


def test_retry_delayed_and_cancellation(prepared):
    demo, report = prepared
    retry, delayed, canceled = demo.transactions[41:44]
    assert retry.status == TransactionStatus.COMPLETED
    assert retry.attempts == 2
    assert len(retry.error_log) == 1
    assert delayed.status == TransactionStatus.COMPLETED
    assert delayed.finished_at >= delayed.execute_at
    assert canceled.status == TransactionStatus.CANCELLED
    assert canceled.attempts == 0
    assert report["transaction_statistics"]["retries"] == 1
    assert len(demo.audit_log.filter(event_type="TRANSACTION_CANCELLED")) == 1


def test_client_accounts_history_incoming_and_outgoing(prepared):
    demo, _ = prepared
    ivan_id = demo.clients["Иван Петров"]
    anna_id = demo.clients["Анна Сидорова"]
    accounts = demo.reports.client_accounts(ivan_id)
    assert len(accounts) == 2
    assert {a["currency"] for a in accounts} == {"RUB"}
    anna_history = demo.reports.client_history(anna_id)
    assert len(anna_history) >= 10
    assert any(row["client_id"] != anna_id and row["recipient_account_id"] in
               demo.bank._clients[anna_id].account_numbers for row in anna_history)
    assert all(row["status"] in {"completed", "failed", "cancelled"}
               for row in anna_history)


def test_history_pagination_and_unknown_client(prepared):
    demo, _ = prepared
    client_id = demo.clients["Иван Петров"]
    assert len(demo.reports.client_history(client_id, limit=2)) == 2
    assert demo.reports.client_history(client_id, limit=0) == []
    with pytest.raises(KeyError):
        demo.reports.client_history("does-not-exist")
    with pytest.raises(ValueError):
        demo.reports.client_history(client_id, limit=-1)


def test_top_3_and_separated_currency_totals(prepared):
    demo, report = prepared
    ranking = report["top_3_clients_rub"]
    assert len(ranking) == 3
    assert ranking[0]["full_name"] == "Иван Петров"
    assert all(Decimal(ranking[i]["total_balance"]) >= Decimal(ranking[i+1]["total_balance"])
               for i in (0, 1))
    assert report["total_balance_by_currency"] == {
        "RUB": "311022.50", "USD": "1715.00"
    }


def test_audit_file_and_report_json_persist(prepared):
    demo, report = prepared
    assert demo.log_file.exists()
    assert demo.report_file.exists()
    with demo.log_file.open(encoding="utf-8") as file:
        rows = [json.loads(line) for line in file if line.strip()]
    assert len(rows) == len(demo.audit_log.entries)
    assert any(event["event_type"] == "TRANSACTION_BLOCKED" for event in rows)
    assert json.loads(demo.report_file.read_text(encoding="utf-8")) == report
    assert not demo.audit_log.persistence_errors
    reloaded = AuditLog(log_file=demo.log_file, load_existing=True)
    assert len(reloaded.entries) == len(rows)


def test_report_is_free_of_passwords_and_contacts(prepared):
    demo, _ = prepared
    report_data = demo.report_file.read_text(encoding="utf-8")
    assert "DemoPassword123" not in report_data
    assert "@example.local" not in report_data
    assert "+700000000" not in report_data


def test_cannot_run_simulation_twice(prepared):
    demo, _ = prepared
    with pytest.raises(RuntimeError, match="уже завершена"):
        demo.run()
