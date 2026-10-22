
from decimal import Decimal

import pytest

from accounts.bank_account import BankAccount
from accounts.savings_account import SavingsAccount
from accounts.premium_account import PremiumAccount
from accounts.enums import AccountStatus
from accounts.exceptions import (
    AccountFrozenError,
    AccountClosedError,
    InsufficientFundsError,
    InvalidOperationError,
)


# ========== SAVINGS ACCOUNT ==========

def test_savings_creation():
    account = SavingsAccount(
        "Иван", 10000, min_balance=2000
    )

    assert account.balance == Decimal("10000")
    assert account.min_balance == Decimal("2000")
    assert "SavingsAccount" in str(account)


def test_savings_withdraw():
    account = SavingsAccount(
        "Иван", 10000, min_balance=2000
    )

    account.withdraw(8000)

    assert account.balance == Decimal("2000")


def test_savings_min_balance():
    account = SavingsAccount(
        "Иван", 10000, min_balance=2000
    )

    with pytest.raises(InvalidOperationError):
        account.withdraw(9000)

    assert account.balance == Decimal("10000")


def test_savings_interest():
    account = SavingsAccount(
        "Иван",
        balance=10000,
        monthly_interest_rate="0.01"
    )

    interest = account.apply_monthly_interest()

    assert interest == Decimal("100.00")
    assert account.balance == Decimal("10100.00")


def test_savings_invalid_balance():
    with pytest.raises(InvalidOperationError):
        SavingsAccount(
            "Иван", balance=100, min_balance=200
        )


def test_savings_frozen():
    account = SavingsAccount(
        "Иван",
        balance=1000,
        status=AccountStatus.FROZEN
    )

    with pytest.raises(AccountFrozenError):
        account.withdraw(100)

    with pytest.raises(AccountFrozenError):
        account.apply_monthly_interest()


# ========== PREMIUM ACCOUNT ==========

def test_premium_overdraft():
    account = PremiumAccount(
        "Иван",
        balance=1000,
        overdraft_limit=5000,
        fixed_fee=50
    )

    account.withdraw(1200)

    assert account.balance == Decimal("-250")


def test_premium_deposit():
    account = PremiumAccount("Иван", balance=1000)

    account.withdraw(1200)
    account.deposit(300)

    assert account.balance == Decimal("50")


def test_overdraft_exceeded():
    account = PremiumAccount(
        "Иван",
        balance=1000,
        overdraft_limit=5000,
        fixed_fee=50
    )

    with pytest.raises(InsufficientFundsError):
        account.withdraw(6000)

    assert account.balance == Decimal("1000")


def test_increased_withdrawal_limit():
    normal = BankAccount("Иван", balance=300000)
    premium = PremiumAccount(
        "Пётр", balance=300000, fixed_fee=0
    )

    with pytest.raises(InvalidOperationError):
        normal.withdraw(200000)

    premium.withdraw(200000)

    assert premium.balance == Decimal("100000")


def test_frozen_premium():
    account = PremiumAccount(
        "Иван", status=AccountStatus.FROZEN
    )

    with pytest.raises(AccountFrozenError):
        account.withdraw(100)


def test_closed_premium():
    account = PremiumAccount(
        "Иван", status=AccountStatus.CLOSED
    )

    with pytest.raises(AccountClosedError):
        account.withdraw(100)


def test_invalid_commission():
    with pytest.raises(InvalidOperationError):
        PremiumAccount("Иван", fixed_fee=-50)


def test_premium_account_info():
    account = PremiumAccount("Иван")

    info = account.get_account_info()

    assert info["overdraft_limit"] == "5000"
    assert info["fixed_fee"] == "50"
    assert info["max_withdrawal"] == "500000"
    assert "PremiumAccount" in str(account)
