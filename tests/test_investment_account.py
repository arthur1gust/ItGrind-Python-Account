
from decimal import Decimal

import pytest

from accounts.investment_account import InvestmentAccount
from accounts.enums import AccountStatus
from accounts.exceptions import (
    AccountFrozenError,
    InsufficientFundsError,
    InvalidOperationError,
)


def test_investment_portfolio():
    account = InvestmentAccount("Иван", 100000)

    account.invest("stocks", 30000)
    account.invest("bonds", 20000)
    account.invest("etf", 10000)

    assert account.balance == Decimal("40000")
    assert account.portfolio["stocks"] == Decimal("30000")
    assert account.portfolio["bonds"] == Decimal("20000")
    assert account.portfolio["etf"] == Decimal("10000")


def test_yearly_growth():
    account = InvestmentAccount("Иван", 100000)

    account.invest("stocks", 30000)
    account.invest("bonds", 20000)
    account.invest("etf", 10000)

    growth = account.project_yearly_growth({
        "stocks": Decimal("0.10"),
        "bonds": Decimal("0.04"),
        "etf": Decimal("0.07")
    })

    assert growth == Decimal("4500.00")


def test_invalid_asset():
    account = InvestmentAccount("Иван", 10000)

    with pytest.raises(InvalidOperationError):
        account.invest("crypto", 1000)


def test_insufficient_funds():
    account = InvestmentAccount("Иван", 1000)

    with pytest.raises(InsufficientFundsError):
        account.invest("stocks", 2000)


def test_withdraw_only_free_balance():
    account = InvestmentAccount("Иван", 10000)
    account.invest("stocks", 8000)

    with pytest.raises(InsufficientFundsError):
        account.withdraw(3000)

    assert account.balance == Decimal("2000")


def test_frozen_investment_account():
    account = InvestmentAccount(
        "Иван",
        balance=1000,
        status=AccountStatus.FROZEN
    )

    with pytest.raises(AccountFrozenError):
        account.invest("stocks", 500)


def test_investment_account_info():
    account = InvestmentAccount("Иван", 10000)
    account.invest("stocks", 3000)

    info = account.get_account_info()

    assert info["portfolio_total"] == "3000"
    assert "InvestmentAccount" in str(account)
