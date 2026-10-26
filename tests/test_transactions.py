from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from bank_system import Bank, Client
from accounts.exceptions import InvalidOperationError
from transactions import (
    Transaction,
    TransactionProcessor,
    TransactionQueue,
    TransactionStatus,
    TransactionType,
    TemporaryProcessingError,
)


@pytest.fixture
def env():
    clock = [datetime(2026, 10, 24, 12, 0)]
    bank = Bank(now_provider=lambda: clock[0])
    ivan = bank.add_client(Client("Иван", 30, "+70001", "i@ex.com", "password123"))
    anna = bank.add_client(Client("Анна", 30, "+70002", "a@ex.com", "password123"))
    ivan_rub = bank.open_account(ivan, "bank", balance=100_000, currency="RUB")
    anna_rub = bank.open_account(anna, "bank", balance=1000, currency="RUB")
    anna_usd = bank.open_account(anna, "bank", balance=100, currency="USD")
    premium = bank.open_account(ivan, "premium", balance=0, currency="RUB")
    savings = bank.open_account(ivan, "savings", balance=10_000, min_balance=2000)
    return {
        "clock": clock, "bank": bank, "ivan": ivan, "anna": anna,
        "ivan_rub": ivan_rub, "anna_rub": anna_rub,
        "anna_usd": anna_usd, "premium": premium, "savings": savings,
        "processor": TransactionProcessor(bank, rates={("RUB", "USD"): "0.0125"}),
    }


def make_tx(env, type=TransactionType.TRANSFER, amount=100, **kwargs):
    params = {
        "type": type, "amount": amount, "currency": "RUB",
        "client_id": env["ivan"], "sender_account_id": env["ivan_rub"],
        "recipient_account_id": env["anna_rub"],
    }
    params.update(kwargs)
    return Transaction(**params)


def test_valid_fields_and_timestamps(env):
    tx = make_tx(env)
    assert len(tx.transaction_id) == 32
    assert tx.status == TransactionStatus.PENDING
    assert tx.fee == Decimal("0")
    assert isinstance(tx.created_at, datetime)
    assert tx.as_dict()["recipient_account_id"] == env["anna_rub"]
    assert tx.started_at is None and tx.finished_at is None


@pytest.mark.parametrize("bad_amount", [-1, 0, "text", "NaN", True, "1.001"])
def test_amount_validation(env, bad_amount):
    with pytest.raises((ValueError, InvalidOperationError)):
        make_tx(env, amount=bad_amount)


def test_queue_priority_and_fifo(env):
    queue = TransactionQueue()
    a = make_tx(env, amount=1, priority=0)
    b = make_tx(env, amount=2, priority=10)
    c = make_tx(env, amount=3, priority=10)
    for tx in (a, b, c):
        queue.add(tx)
    assert [queue.pop_ready(env["clock"][0]) for _ in range(3)] == [b, c, a]
    assert queue.pop_ready(env["clock"][0]) is None


def test_queue_scheduled_does_not_block_ready(env):
    queue = TransactionQueue()
    late = make_tx(env, amount=200, priority=100, execute_at=env["clock"][0] + timedelta(hours=1))
    now = make_tx(env, amount=100, priority=0)
    queue.add(late)
    queue.add(now)
    assert queue.pop_ready(env["clock"][0]) is now
    assert queue.pop_ready(env["clock"][0]) is None
    env["clock"][0] += timedelta(hours=1)
    assert queue.pop_ready(env["clock"][0]) is late


def test_cancel_prevents_debit(env):
    tx = make_tx(env)
    queue = TransactionQueue()
    queue.add(tx)
    assert queue.cancel(tx.transaction_id, now=env["clock"][0]) is True
    assert queue.cancel(tx.transaction_id) is False
    assert tx.status == TransactionStatus.CANCELLED
    assert env["processor"].run_ready(queue) == []
    assert env["bank"]._get_account(env["ivan_rub"]).balance == Decimal("100000")
    with pytest.raises(ValueError):
        queue.add(tx)


def test_rejects_same_transaction_twice(env):
    queue = TransactionQueue()
    tx = make_tx(env)
    queue.add(tx)
    with pytest.raises(ValueError):
        queue.add(tx)
    assert env["processor"].process_next(queue).status == TransactionStatus.COMPLETED
    with pytest.raises(ValueError):
        queue.add(tx)
    with pytest.raises(ValueError):
        env["processor"].process(tx)


def test_internal_transfer_and_sum_preserved(env):
    tx = make_tx(env, amount=1250)
    env["processor"].process(tx)
    assert tx.status == TransactionStatus.COMPLETED
    assert tx.fee == 0
    assert env["bank"]._get_account(env["ivan_rub"]).balance == 98750
    assert env["bank"]._get_account(env["anna_rub"]).balance == 2250
    assert tx.converted_amount == Decimal("1250")


def test_currency_conversion_uses_explicit_rate(env):
    tx = make_tx(env, amount=1000, recipient_account_id=env["anna_usd"])
    env["processor"].process(tx)
    assert tx.status == TransactionStatus.COMPLETED
    assert tx.converted_currency == "USD"
    assert tx.converted_amount == Decimal("12.50")
    assert env["bank"]._get_account(env["anna_usd"]).balance == Decimal("112.50")
    assert env["bank"]._get_account(env["ivan_rub"]).balance == 99000


def test_missing_fx_rate_is_error_without_debit(env):
    tx = make_tx(env, amount=100, recipient_account_id=env["anna_usd"])
    processor = TransactionProcessor(env["bank"])
    processor.process(tx)
    assert tx.status == TransactionStatus.FAILED
    assert "Нет курса" in tx.failure_reason
    assert tx.error_log[0]["attempt"] == 1
    assert env["bank"]._get_account(env["ivan_rub"]).balance == 100000
    assert env["bank"]._get_account(env["anna_usd"]).balance == 100


def test_external_transfer_fee_and_no_internal_credit(env):
    tx = make_tx(env, TransactionType.EXTERNAL_TRANSFER, 1000,
                 recipient_account_id=None, recipient_details="OUTSIDE-123")
    env["processor"].process(tx)
    assert tx.status == TransactionStatus.COMPLETED
    assert tx.fee == Decimal("10.00")
    assert env["bank"]._get_account(env["ivan_rub"]).balance == Decimal("98990.00")
    assert env["bank"]._get_account(env["anna_rub"]).balance == 1000


def test_external_fee_minimum(env):
    tx = make_tx(env, TransactionType.EXTERNAL_TRANSFER, 5,
                 recipient_account_id=None, recipient_details="OTHER-BANK")
    env["processor"].process(tx)
    assert tx.status == TransactionStatus.COMPLETED
    assert tx.fee == Decimal("1.00")
    assert env["bank"]._get_account(env["ivan_rub"]).balance == Decimal("99994")


def test_savings_minimum_balance(env):
    tx = make_tx(env, amount=9000, sender_account_id=env["savings"])
    env["processor"].process(tx)
    assert tx.status == TransactionStatus.FAILED
    assert "минимальный остаток" in tx.failure_reason
    assert env["bank"]._get_account(env["savings"]).balance == 10000
    assert env["bank"]._get_account(env["anna_rub"]).balance == 1000


def test_premium_allowed_to_go_negative_but_only_within_overdraft(env):
    ok = make_tx(env, amount=4500, sender_account_id=env["premium"])
    fail = make_tx(env, amount=600, sender_account_id=env["premium"])
    env["processor"].process(ok)
    env["processor"].process(fail)
    assert ok.status == TransactionStatus.COMPLETED
    assert fail.status == TransactionStatus.FAILED
    assert env["bank"]._get_account(env["premium"]).balance == -4500
    assert env["bank"]._get_account(env["anna_rub"]).balance == 5500


def test_frozen_sender_and_receiver_cannot_transfer(env):
    bank = env["bank"]
    bank.freeze_account(env["ivan_rub"])
    tx = make_tx(env)
    env["processor"].process(tx)
    assert tx.status == TransactionStatus.FAILED
    assert "заморожен" in tx.failure_reason
    bank.unfreeze_account(env["ivan_rub"])
    bank.freeze_account(env["anna_rub"])
    tx2 = make_tx(env)
    env["processor"].process(tx2)
    assert tx2.status == TransactionStatus.FAILED
    assert bank._get_account(env["ivan_rub"]).balance == 100000


def test_other_person_cannot_spend_someone_elses_money(env):
    tx = make_tx(env, client_id=env["anna"])
    env["processor"].process(tx)
    assert tx.status == TransactionStatus.FAILED
    assert "не является владельцем" in tx.failure_reason
    assert env["bank"]._get_account(env["ivan_rub"]).balance == 100000


def test_not_enough_funds_no_partial_credit(env):
    tx = make_tx(env, amount=100001)
    env["processor"].process(tx)
    assert tx.status == TransactionStatus.FAILED
    assert env["bank"]._get_account(env["ivan_rub"]).balance == 100000
    assert env["bank"]._get_account(env["anna_rub"]).balance == 1000


def test_cash_deposit_and_withdraw(env):
    deposit = make_tx(env, TransactionType.DEPOSIT, 250,
                      sender_account_id=None, recipient_account_id=env["ivan_rub"])
    withdraw = make_tx(env, TransactionType.WITHDRAW, 50, recipient_account_id=None)
    env["processor"].process(deposit)
    env["processor"].process(withdraw)
    assert deposit.status == withdraw.status == TransactionStatus.COMPLETED
    assert env["bank"]._get_account(env["ivan_rub"]).balance == 100200


def test_premium_cash_withdrawal_has_its_existing_fixed_fee(env):
    tx = make_tx(env, TransactionType.WITHDRAW, 500,
                 sender_account_id=env["premium"], recipient_account_id=None)
    env["processor"].process(tx)
    assert tx.status == TransactionStatus.COMPLETED
    assert tx.fee == 50
    assert env["bank"]._get_account(env["premium"]).balance == -550


def test_retry_after_temporary_gateway_outage(env):
    calls = []

    def unreliable(tx):
        calls.append(tx.transaction_id)
        if len(calls) == 1:
            raise TemporaryProcessingError("Шлюз временно недоступен")

    processor = TransactionProcessor(env["bank"], gateway=unreliable, retry_delay_seconds=30)
    tx = make_tx(env, TransactionType.EXTERNAL_TRANSFER, 1000,
                 recipient_account_id=None, recipient_details="OTHER-BANK")
    queue = TransactionQueue()
    queue.add(tx)
    assert processor.process_next(queue).status == TransactionStatus.RETRY_PENDING
    assert len(tx.error_log) == 1
    assert tx.attempts == 1
    assert len(queue) == 1
    assert env["bank"]._get_account(env["ivan_rub"]).balance == 100000
    assert processor.process_next(queue) is None
    env["clock"][0] += timedelta(seconds=30)
    assert processor.process_next(queue).status == TransactionStatus.COMPLETED
    assert tx.attempts == 2
    assert len(queue) == 0
    assert env["bank"]._get_account(env["ivan_rub"]).balance == 98990


def test_retry_exhaustion(env):
    def broken_gateway(_):
        raise TemporaryProcessingError("Нет связи")

    processor = TransactionProcessor(env["bank"], gateway=broken_gateway, retry_delay_seconds=30)
    tx = make_tx(env, TransactionType.EXTERNAL_TRANSFER, 1000, max_attempts=2,
                 recipient_account_id=None, recipient_details="OTHER-BANK")
    queue = TransactionQueue()
    queue.add(tx)
    processor.process_next(queue)
    env["clock"][0] += timedelta(seconds=30)
    processor.process_next(queue)
    assert tx.status == TransactionStatus.FAILED
    assert tx.attempts == 2
    assert len(tx.error_log) == 2
    assert tx.finished_at == env["clock"][0]
    assert len(queue) == 0
    assert env["bank"]._get_account(env["ivan_rub"]).balance == 100000


def test_permanent_failure_does_not_retry(env):
    tx = make_tx(env, amount=100001)
    q = TransactionQueue()
    q.add(tx)
    env["processor"].run_ready(q)
    assert tx.status == TransactionStatus.FAILED
    assert tx.attempts == 1 and len(q) == 0
    assert env["bank"].suspicious_events[-1]["action"] == "TRANSACTION_ERROR"


def test_restricted_night_hours_inherited_from_day3(env):
    env["clock"][0] = datetime(2026, 10, 25, 2)
    tx = make_tx(env)
    env["processor"].process(tx)
    assert tx.status == TransactionStatus.FAILED
    assert "00:00 до 05:00" in tx.failure_reason
    assert env["bank"]._get_account(env["ivan_rub"]).balance == 100000


def test_10_transactions_all_are_executed(env):
    """Формальное задание: создать десять, добавить в очередь и обработать."""
    q = TransactionQueue()
    transfers = [make_tx(env, amount=100 + i, priority=(i % 3)) for i in range(10)]
    for tx in transfers:
        q.add(tx)
    result = env["processor"].run_ready(q)
    assert len(result) == 10
    assert len(q) == 0
    assert all(tx.status == TransactionStatus.COMPLETED for tx in transfers)
    assert sum(tx.amount for tx in transfers) == Decimal("1045")
    assert env["bank"]._get_account(env["ivan_rub"]).balance == 98955
    assert env["bank"]._get_account(env["anna_rub"]).balance == 2045


def test_cannot_run_scheduled_transaction_directly_before_its_time(env):
    tx = make_tx(env, execute_at=env["clock"][0] + timedelta(hours=1))
    with pytest.raises(ValueError):
        env["processor"].process(tx)
    assert tx.attempts == 0
