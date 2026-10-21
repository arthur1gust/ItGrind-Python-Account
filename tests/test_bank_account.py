from decimal import Decimal

import pytest

from accounts.abstract_account import AbstractAccount
from accounts.bank_account import BankAccount
from accounts.enums import AccountStatus
from accounts.exceptions import (
    AccountClosedError,
    AccountFrozenError,
    InsufficientFundsError,
    InvalidOperationError,
)


def test_create_account():
    account = BankAccount("Иван", 1000, "RUB")

    assert account.status == AccountStatus.ACTIVE
    assert account.balance == Decimal("1000")
    assert len(account.account_id) == 12
    assert account.account_id.isdigit()


def test_deposit_and_withdraw():
    account = BankAccount("Иван", 1000)

    account.deposit(500)
    account.withdraw(300)

    assert account.balance == Decimal("1200")


def test_frozen_account():
    account = BankAccount(
        "Пётр",
        status=AccountStatus.FROZEN
    )

    with pytest.raises(AccountFrozenError):
        account.deposit(100)

    with pytest.raises(AccountFrozenError):
        account.withdraw(100)


def test_closed_account():
    account = BankAccount(
        "Анна",
        status=AccountStatus.CLOSED
    )

    with pytest.raises(AccountClosedError):
        account.deposit(100)


def test_insufficient_funds():
    account = BankAccount("Иван", 100)

    with pytest.raises(InsufficientFundsError):
        account.withdraw(101)


@pytest.mark.parametrize(
    "amount",
    [-1, 0, "abc", "NaN", float("inf"), True]
)
def test_invalid_deposit(amount):
    account = BankAccount("Иван")

    with pytest.raises(InvalidOperationError):
        account.deposit(amount)


def test_invalid_initial_data():
    with pytest.raises(InvalidOperationError):
        BankAccount("", 100)

    with pytest.raises(InvalidOperationError):
        BankAccount("Иван", -1)

    with pytest.raises(InvalidOperationError):
        BankAccount("Иван", currency="BTC")

    with pytest.raises(InvalidOperationError):
        BankAccount("Иван", status="ACTIVE")


def test_manual_account_number_and_duplicate():
    BankAccount(
        "Иван",
        account_id="4081781000001234"
    )

    with pytest.raises(InvalidOperationError):
        BankAccount(
            "Пётр",
            account_id="4081781000001234"
        )


def test_string_and_info():
    account = BankAccount(
        "Иван",
        100,
        "USD",
        account_id="12345678901234"
    )

    text = str(account)

    assert "****1234" in text
    assert "Иван" in text
    assert "100.00 USD" in text
    assert account.get_account_info()["currency"] == "USD"


def test_abstract_class_cannot_be_instantiated():
    with pytest.raises(TypeError):
        AbstractAccount("Иван")