import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from accounts.enums import AccountStatus
from audit import AuditEvent, AuditLevel, AuditLog, AuditReports, RiskAnalyzer
from bank_system import Bank, Client
from transactions import (
    Transaction, TransactionProcessor, TransactionQueue,
    TransactionStatus, TransactionType, TemporaryProcessingError,
)


@pytest.fixture
def env(tmp_path):
    clock = [datetime(2026, 10, 26, 12, 0)]
    bank = Bank(now_provider=lambda: clock[0])
    ivan = bank.add_client(Client("Иван", 24, "+700001", "ivan@example.test", "password123"))
    anna = bank.add_client(Client("Анна", 25, "+700002", "anna@example.test", "password123"))
    pavel = bank.add_client(Client("Павел", 25, "+700003", "pavel@example.test", "password123"))
    ia = bank.open_account(ivan, balance=100000)
    aa = bank.open_account(anna, balance=1000)
    pa = bank.open_account(pavel, balance=1000)
    frozen = bank.open_account(anna, balance=0)
    bank.freeze_account(frozen)
    audit = AuditLog(log_file=tmp_path / "audit" / "events.jsonl", now_provider=lambda: clock[0])
    analyzer = RiskAnalyzer()
    processor = TransactionProcessor(bank, audit_log=audit, risk_analyzer=analyzer)
    return {
        "clock": clock, "bank": bank, "ivan": ivan, "anna": anna, "pavel": pavel,
        "ia": ia, "aa": aa, "pa": pa, "frozen": frozen,
        "audit": audit, "analyzer": analyzer, "processor": processor,
    }


def transfer(e, amount=100, client=None, source=None, target=None):
    return Transaction(TransactionType.TRANSFER, amount, "RUB", client or e["ivan"],
                       sender_account_id=source or e["ia"], recipient_account_id=target or e["aa"])


def deposit(e, amount=100, client=None, account=None):
    return Transaction(TransactionType.DEPOSIT, amount, "RUB", client or e["ivan"],
                       recipient_account_id=account or e["ia"])


def balance(e, key):
    return e["bank"]._get_account(e[key]).balance


def test_log_writes_jsonl_and_readable_in_memory(tmp_path):
    file = tmp_path / "logs" / "events.jsonl"
    log = AuditLog(file, now_provider=lambda: datetime(2026, 10, 26, 12))
    event = log.log("LOGIN", AuditLevel.MEDIUM, client_id="client-A", details="Incorrect login")
    assert isinstance(event, AuditEvent)
    assert len(log.entries) == 1
    assert event.as_dict()["level"] == "medium"
    assert json.loads(file.read_text(encoding="utf-8").splitlines()[0])["client_id"] == "client-A"


def test_log_filter_by_level_client_event_transaction_and_time(env):
    log = env["audit"]
    start = datetime(2026, 10, 26, 11)
    log.log("ONE", AuditLevel.LOW, client_id="A", transaction_id="tx-1", now=start)
    log.log("TWO", AuditLevel.HIGH, client_id="B", transaction_id="tx-2", now=start + timedelta(hours=1))
    assert len(log.filter(level=AuditLevel.HIGH)) == 1
    assert len(log.filter(event_type="ONE", client_id="A", transaction_id="tx-1")) == 1
    assert log.filter(event_type="TWO", to_time=start) == []
    assert len(log.filter(from_time=start + timedelta(minutes=30))) == 1
    assert len(log.filter(to_time=start)) == 1


def test_log_entries_immutable_snapshot(env):
    event = env["audit"].log("ONE", AuditLevel.LOW)
    entries = env["audit"].entries
    assert isinstance(entries, tuple)
    with pytest.raises(AttributeError):
        event.level = AuditLevel.HIGH
    assert len(env["audit"].entries) == 1


def test_log_can_reload_existing_json_lines(tmp_path):
    file = tmp_path / "audit.jsonl"
    first = AuditLog(file)
    first.log("ONE", AuditLevel.LOW, client_id="a")
    first.log("TWO", AuditLevel.HIGH, client_id="b")
    second = AuditLog(file, load_existing=True)
    assert len(second.entries) == 2
    second.log("THREE", AuditLevel.MEDIUM)
    assert len(AuditLog(file, load_existing=True).entries) == 3


def test_log_rejects_invalid_jsonl(tmp_path):
    file = tmp_path / "bad.jsonl"
    file.write_text('{"timestamp": "bad"}', encoding="utf-8")
    with pytest.raises(ValueError, match="строка аудита"):
        AuditLog(file, load_existing=True)


def test_log_memory_only():
    log = AuditLog()
    log.log("OK", AuditLevel.LOW)
    assert log.log_file is None and len(log.entries) == 1


def test_log_file_failure_does_not_erase_in_memory_event(tmp_path):
    log = AuditLog(tmp_path)  # каталог вместо файла
    log.log("EVENT", AuditLevel.LOW)
    assert len(log.entries) == 1
    assert len(log.persistence_errors) == 1


@pytest.mark.parametrize("amount,expected_level,reason", [
    (100, AuditLevel.MEDIUM, "NEW_RECIPIENT"),
    (10000, AuditLevel.HIGH, "LARGE_AMOUNT"),  # вместе с новым получателем
    (50000, AuditLevel.HIGH, "VERY_LARGE_AMOUNT"),
])
def test_amount_and_new_recipient_rules(env, amount, expected_level, reason):
    assessment = env["analyzer"].analyze(transfer(env, amount), env["clock"][0])
    assert assessment.level == expected_level
    assert reason in assessment.reasons


def test_large_known_recipient_medium(env):
    first = transfer(env, 100)
    env["processor"].process(first)
    env["clock"][0] += timedelta(minutes=2)
    second = transfer(env, 10000)
    env["processor"].process(second)
    assert second.status == TransactionStatus.COMPLETED
    assert second.risk_level == "medium"
    assert second.risk_reasons == ["LARGE_AMOUNT"]


def test_new_recipient_blocked_if_combined_with_large_amount(env):
    tx = transfer(env, 10000)
    start_sender, start_receiver = balance(env, "ia"), balance(env, "aa")
    env["processor"].process(tx)
    assert tx.risk_level == "high"
    assert tx.status == TransactionStatus.FAILED
    assert "RiskBlockedError" == tx.error_log[0]["error_type"]
    assert tx.attempts == 1
    assert balance(env, "ia") == start_sender
    assert balance(env, "aa") == start_receiver
    assert len(env["audit"].filter(event_type="TRANSACTION_BLOCKED")) == 1
    assert env["bank"].suspicious_events[-1]["action"] == "RISK_BLOCKED"


def test_very_large_cash_deposit_blocked_before_credit(env):
    tx = deposit(env, 50000)
    env["processor"].process(tx)
    assert tx.status == TransactionStatus.FAILED
    assert tx.risk_level == "high"
    assert balance(env, "ia") == Decimal("100000")


def test_small_deposit_low_risk_and_audited(env):
    tx = deposit(env, 100)
    env["processor"].process(tx)
    assert tx.status == TransactionStatus.COMPLETED
    assert tx.risk_level == "low"
    assert balance(env, "ia") == Decimal("100100")
    assert len(env["audit"].filter(event_type="TRANSACTION_COMPLETED")) == 1


def test_repeated_known_recipient_is_no_longer_new(env):
    first = transfer(env, 100)
    env["processor"].process(first)
    tx = transfer(env, 100)
    env["processor"].process(tx)
    assert first.risk_level == "medium"
    assert tx.risk_level == "low"
    assert tx.status == TransactionStatus.COMPLETED
    assert env["analyzer"].client_profile(env["ivan"])["known_recipients"] == 1


def test_failed_new_recipient_stays_new(env):
    tx = transfer(env, 100, target=env["frozen"])
    env["processor"].process(tx)
    assert tx.status == TransactionStatus.FAILED
    assert "заморожен" in tx.failure_reason
    env["clock"][0] += timedelta(minutes=2)
    tx2 = transfer(env, 100, target=env["frozen"])
    assert env["analyzer"].analyze(tx2, env["clock"][0]).reasons == ("NEW_RECIPIENT",)


def test_high_frequency_blocks_fourth_attempt(env):
    txs = [transfer(env, 100) for _ in range(4)]
    for tx in txs:
        env["processor"].process(tx)
    assert [t.status for t in txs] == [
        TransactionStatus.COMPLETED,
        TransactionStatus.COMPLETED,
        TransactionStatus.COMPLETED,
        TransactionStatus.FAILED,
    ]
    assert "HIGH_FREQUENCY" in txs[3].risk_reasons
    assert balance(env, "ia") == Decimal("99700")


def test_frequency_window_expires(env):
    for _ in range(3):
        env["processor"].process(deposit(env, 10))
    env["clock"][0] += timedelta(seconds=61)
    tx = deposit(env, 10)
    env["processor"].process(tx)
    assert tx.risk_level == "low"
    assert tx.status == TransactionStatus.COMPLETED


def test_attempts_per_client_are_isolated(env):
    for _ in range(3):
        env["processor"].process(deposit(env, 10))
    another = deposit(env, 10, client=env["pavel"], account=env["pa"])
    env["processor"].process(another)
    assert another.risk_level == "low"
    assert another.status == TransactionStatus.COMPLETED


def test_risk_analyzer_counts_blocked_attempts_for_frequency(env):
    # Каждый из первых трёх платежей блокируется из-за очень большой суммы.
    for _ in range(3):
        env["processor"].process(deposit(env, 60000))
    fourth = deposit(env, 10)
    env["processor"].process(fourth)
    assert fourth.status == TransactionStatus.FAILED
    assert "HIGH_FREQUENCY" in fourth.risk_reasons
    assert balance(env, "ia") == 100000


def test_night_operations_blocked_before_balances_change(env):
    env["clock"][0] = datetime(2026, 10, 27, 2, 30)
    tx = deposit(env, 10)
    env["processor"].process(tx)
    assert tx.status == TransactionStatus.FAILED
    assert "NIGHT_OPERATION" in tx.risk_reasons
    assert balance(env, "ia") == 100000


def test_0500_not_night_risk(env):
    env["clock"][0] = datetime(2026, 10, 27, 5, 0)
    tx = deposit(env, 10)
    env["processor"].process(tx)
    assert tx.status == TransactionStatus.COMPLETED
    assert tx.risk_level == "low"


def test_frozen_account_rejected_and_error_audited(env):
    tx = transfer(env, 100, target=env["frozen"])
    env["processor"].process(tx)
    assert tx.status == TransactionStatus.FAILED
    assert len(env["audit"].filter(event_type="TRANSACTION_FAILED")) == 1
    assert env["audit"].filter(event_type="TRANSACTION_FAILED")[0].error_type == "AccountFrozenError"


def test_retry_with_logging_and_report(env):
    attempts = []

    def gateway(tx):
        attempts.append(tx.transaction_id)
        if len(attempts) == 1:
            raise TemporaryProcessingError("Шлюз временно недоступен")

    processor = TransactionProcessor(
        env["bank"], gateway=gateway, audit_log=env["audit"],
        risk_analyzer=env["analyzer"], retry_delay_seconds=30,
    )
    tx = Transaction(TransactionType.EXTERNAL_TRANSFER, 100, "RUB", env["ivan"],
                     sender_account_id=env["ia"], recipient_details="bank-123")
    queue = TransactionQueue()
    queue.add(tx)
    processor.process_next(queue)
    assert tx.status == TransactionStatus.RETRY_PENDING
    assert balance(env, "ia") == 100000
    env["clock"][0] += timedelta(seconds=30)
    processor.process_next(queue)
    assert tx.status == TransactionStatus.COMPLETED
    assert tx.attempts == 2
    assert env["analyzer"].client_profile(env["ivan"])["transactions_assessed"] == 1
    report = AuditReports(env["audit"], env["analyzer"]).error_statistics()
    assert report["retries"] == 1
    assert report["failed"] == 0
    assert len(tx.error_log) == 1
    assert len(env["audit"].filter(event_type="TRANSACTION_COMPLETED")) == 1


def test_audit_reports_suspicious_and_client_profile(env):
    env["processor"].process(transfer(env, 100))
    env["clock"][0] += timedelta(seconds=61)
    env["processor"].process(transfer(env, 10000))
    env["clock"][0] += timedelta(seconds=61)
    env["processor"].process(transfer(env, 60000))
    reports = AuditReports(env["audit"], env["analyzer"])
    assert len(reports.suspicious_operations(include_medium=True)) == 3
    assert len(reports.suspicious_operations(include_medium=False)) == 1
    profile = reports.client_risk_profile(env["ivan"])
    assert profile["transactions_assessed"] == 3
    assert profile["by_level"] == {"low": 0, "medium": 2, "high": 1}
    assert profile["risk_level"] == "high"
    assert profile["blocked_operations"] == 1
    assert profile["known_recipients"] == 1


def test_error_statistics_counts_blocked_failed_retry(env):
    env["processor"].process(transfer(env, 60000))
    env["clock"][0] += timedelta(minutes=2)
    env["processor"].process(transfer(env, 100, target=env["frozen"]))
    r = AuditReports(env["audit"], env["analyzer"]).error_statistics()
    assert r["blocked"] == 1
    assert r["failed"] == 1
    assert r["retries"] == 0
    assert r["by_error_type"] == {"RiskBlockedError": 1, "AccountFrozenError": 1}


def test_high_risk_from_queue_is_blocked_and_lower_risk_executed(env):
    q = TransactionQueue()
    bad = transfer(env, 60000)
    good = deposit(env, 25)
    bad.priority = 10
    good.priority = 1
    q.add(bad)
    q.add(good)
    res = env["processor"].run_ready(q)
    assert len(res) == 2
    assert bad.status == TransactionStatus.FAILED
    assert good.status == TransactionStatus.COMPLETED
    assert balance(env, "ia") == 100025
    assert len(q) == 0


def test_without_risk_analyzer_old_day4_behaviour_is_unchanged(env):
    processor = TransactionProcessor(env["bank"])
    tx = transfer(env, 60000)
    processor.process(tx)
    assert tx.status == TransactionStatus.COMPLETED
    assert tx.risk_level is None


def test_invalid_risk_configuration():
    with pytest.raises(ValueError):
        RiskAnalyzer(large_amount=50000, very_large_amount=10000)
    with pytest.raises(ValueError):
        RiskAnalyzer(frequency_limit=0)
    with pytest.raises(ValueError):
        RiskAnalyzer(frequency_window_seconds=-1)


def test_risk_evaluation_has_no_side_effect_until_registered(env):
    tx = transfer(env)
    a = env["analyzer"].analyze(tx, env["clock"][0])
    assert a.level == AuditLevel.MEDIUM
    assert env["analyzer"].client_profile(env["ivan"])["transactions_assessed"] == 0
