from datetime import datetime
from decimal import Decimal

import pytest

from accounts.enums import AccountStatus
from accounts.exceptions import (
    AccountClosedError,
    AccountFrozenError,
    InvalidOperationError,
)
from bank_system import Bank, Client, ClientStatus
from bank_system.exceptions import (
    AccountNotFoundError,
    AuthenticationError,
    ClientBlockedError,
    ClientNotFoundError,
    PermissionDeniedError,
    RestrictedHoursError,
)


@pytest.fixture
def bank():
    # Рабочий день, 12:00: тесты стабильны в любом часовом поясе.
    return Bank(now_provider=lambda: datetime(2026, 10, 24, 12, 0))


@pytest.fixture
def ivan():
    return Client("Иван Петров", 25, "+79990000000", "ivan@example.com", "Secret123")


def test_client_creation(ivan):
    assert ivan.full_name == "Иван Петров"
    assert ivan.age == 25
    assert ivan.status == ClientStatus.ACTIVE
    assert ivan.account_numbers == []
    assert ivan.contacts["email"] == "ivan@example.com"
    assert len(ivan.client_id) == 32


@pytest.mark.parametrize("age", [0, 17, -1, True, "20"])
def test_client_cannot_be_underage_or_invalid(age):
    with pytest.raises(ValueError):
        Client("Иван", age, "+7999", "ivan@x.com", "Secret123")


def test_client_minimum_age_18():
    client = Client("Иван", 18, "+7999", "ivan@x.com", "Secret123")
    assert client.age == 18


def test_client_contacts_and_accounts_are_copies(ivan):
    ivan.contacts["email"] = "changed@example.com"
    ivan.account_numbers.append("fake-account")
    assert ivan.contacts["email"] == "ivan@example.com"
    assert ivan.account_numbers == []


def test_register_and_open_all_types(bank, ivan):
    cid = bank.add_client(ivan)
    aid_bank = bank.open_account(cid, "bank", balance=100)
    aid_savings = bank.open_account(cid, "savings", balance=1000, min_balance=200)
    aid_premium = bank.open_account(cid, "premium", balance=1000)
    aid_invest = bank.open_account(cid, "investment", balance=1000)
    assert set(ivan.account_numbers) == {aid_bank, aid_savings, aid_premium, aid_invest}
    assert len(bank.search_accounts(client_id=cid)) == 4


def test_duplicate_registration(bank, ivan):
    bank.add_client(ivan)
    with pytest.raises(InvalidOperationError):
        bank.add_client(ivan)


def test_unknown_client_cannot_open_account(bank):
    with pytest.raises(ClientNotFoundError):
        bank.open_account("unknown")


def test_unknown_account_cannot_be_frozen(bank):
    with pytest.raises(AccountNotFoundError):
        bank.freeze_account("unknown")


def test_correct_authentication(bank, ivan):
    cid = bank.add_client(ivan)
    assert bank.authenticate_client(cid, "Secret123") is True
    assert ivan.status == ClientStatus.ACTIVE


def test_login_three_failures_lock_client(bank, ivan):
    cid = bank.add_client(ivan)
    for _ in range(2):
        with pytest.raises(AuthenticationError):
            bank.authenticate_client(cid, "wrongpass")
    with pytest.raises(ClientBlockedError):
        bank.authenticate_client(cid, "wrongpass")
    assert ivan.status == ClientStatus.BLOCKED
    assert ivan.failed_login_attempts == 3
    assert len([e for e in bank.suspicious_events if e["action"] == "FAILED_LOGIN"]) == 3
    with pytest.raises(ClientBlockedError):
        bank.authenticate_client(cid, "Secret123")
    with pytest.raises(ClientBlockedError):
        bank.open_account(cid)


def test_successful_login_resets_failed_attempts(bank, ivan):
    cid = bank.add_client(ivan)
    with pytest.raises(AuthenticationError):
        bank.authenticate_client(cid, "wrongpass")
    assert bank.authenticate_client(cid, "Secret123") is True
    assert ivan.failed_login_attempts == 0
    with pytest.raises(AuthenticationError):
        bank.authenticate_client(cid, "wrongpass")
    assert ivan.status == ClientStatus.ACTIVE
    assert ivan.failed_login_attempts == 1


def test_unknown_login_is_flagged(bank):
    with pytest.raises(AuthenticationError):
        bank.authenticate_client("invalid-id", "somepassword")
    assert bank.suspicious_events[0]["action"] == "UNKNOWN_CLIENT_LOGIN"


def test_withdraw_and_deposit(bank, ivan):
    cid = bank.add_client(ivan)
    aid = bank.open_account(cid, balance=1000)
    assert bank.withdraw(cid, aid, 200) == Decimal("800")
    assert bank.deposit(cid, aid, 100) == Decimal("900")


def test_cannot_use_foreign_account(bank, ivan):
    cid = bank.add_client(ivan)
    anna = Client("Анна", 22, "+79991", "anna@x.com", "Password456")
    other_id = bank.add_client(anna)
    aid = bank.open_account(cid, balance=1000)
    with pytest.raises(PermissionDeniedError):
        bank.withdraw(other_id, aid, 100)
    assert bank.get_total_balance()["RUB"] == Decimal("1000")
    assert bank.suspicious_events[-1]["action"] == "FOREIGN_ACCOUNT_ACCESS"


def test_freeze_and_unfreeze(bank, ivan):
    cid = bank.add_client(ivan)
    aid = bank.open_account(cid, balance=100)
    bank.freeze_account(aid)
    assert bank.search_accounts(status=AccountStatus.FROZEN)[0]["account_id"] == aid
    with pytest.raises(AccountFrozenError):
        bank.withdraw(cid, aid, 10)
    bank.unfreeze_account(aid)
    assert bank.withdraw(cid, aid, 10) == Decimal("90")


def test_close_only_empty_account(bank, ivan):
    cid = bank.add_client(ivan)
    aid = bank.open_account(cid, balance=10)
    with pytest.raises(InvalidOperationError):
        bank.close_account(cid, aid)
    bank.withdraw(cid, aid, 10)
    bank.close_account(cid, aid)
    assert bank.search_accounts(status=AccountStatus.CLOSED)[0]["account_id"] == aid
    with pytest.raises(AccountClosedError):
        bank.unfreeze_account(aid)


def test_cannot_close_frozen_account(bank, ivan):
    cid = bank.add_client(ivan)
    aid = bank.open_account(cid)
    bank.freeze_account(aid)
    with pytest.raises(AccountFrozenError):
        bank.close_account(cid, aid)


@pytest.mark.parametrize("hour,minute", [(0, 0), (2, 0), (4, 59)])
def test_night_restriction(bank, ivan, hour, minute):
    night = Bank(now_provider=lambda: datetime(2026, 10, 24, hour, minute))
    cid = night.add_client(ivan)
    with pytest.raises(RestrictedHoursError):
        night.open_account(cid)
    assert night.suspicious_events[0]["action"] == "NIGHT_OPERATION"


def test_operations_allowed_at_0500(ivan):
    bank = Bank(now_provider=lambda: datetime(2026, 10, 24, 5, 0))
    cid = bank.add_client(ivan)
    assert bank.open_account(cid) in ivan.account_numbers


def test_no_night_deposit_or_withdraw(ivan):
    current = [datetime(2026, 10, 24, 12, 0)]
    bank = Bank(now_provider=lambda: current[0])
    cid = bank.add_client(ivan)
    aid = bank.open_account(cid, balance=100)
    current[0] = datetime(2026, 10, 25, 1, 0)
    with pytest.raises(RestrictedHoursError):
        bank.deposit(cid, aid, 10)
    with pytest.raises(RestrictedHoursError):
        bank.withdraw(cid, aid, 10)
    assert bank.get_total_balance()["RUB"] == Decimal("100")


def test_total_balance_separate_currencies(bank, ivan):
    cid = bank.add_client(ivan)
    bank.open_account(cid, balance=100, currency="RUB")
    bank.open_account(cid, balance=20, currency="USD")
    assert bank.get_total_balance() == {"RUB": Decimal("100"), "USD": Decimal("20")}


def test_investment_total_counts_portfolio(bank, ivan):
    cid = bank.add_client(ivan)
    aid = bank.open_account(cid, "investment", balance=100000)
    bank.invest(cid, aid, "stocks", 30000)
    bank.invest(cid, aid, "bonds", 20000)
    assert bank.get_total_balance()["RUB"] == Decimal("100000")
    bank.sell_asset(cid, aid, "stocks", 10000)
    assert bank.get_total_balance()["RUB"] == Decimal("100000")
    with pytest.raises(InvalidOperationError):
        bank.close_account(cid, aid)


def test_bank_rejects_investment_on_regular_account(bank, ivan):
    cid = bank.add_client(ivan)
    aid = bank.open_account(cid, balance=1000)
    with pytest.raises(InvalidOperationError):
        bank.invest(cid, aid, "stocks", 100)


def test_search_filters_by_currency_status_client(bank, ivan):
    cid = bank.add_client(ivan)
    bank.open_account(cid, balance=100, currency="RUB")
    usd = bank.open_account(cid, balance=200, currency="USD")
    assert len(bank.search_accounts(currency="USD", client_id=cid)) == 1
    bank.freeze_account(usd)
    assert len(bank.search_accounts(status=AccountStatus.FROZEN, currency="USD")) == 1


def test_ranking(bank):
    ivan = Client("Иван", 22, "+7", "ivan@x.com", "Secret123")
    anna = Client("Анна", 24, "+8", "anna@x.com", "Secret456")
    cid_ivan, cid_anna = bank.add_client(ivan), bank.add_client(anna)
    bank.open_account(cid_ivan, balance=100, currency="RUB")
    bank.open_account(cid_anna, balance=200, currency="RUB")
    bank.open_account(cid_ivan, balance=1000, currency="USD")
    ranking = bank.get_clients_ranking(currency="RUB")
    assert [r["full_name"] for r in ranking] == ["Анна", "Иван"]
    assert ranking[0]["total_balance"] == Decimal("200")


def test_password_is_not_stored_in_plain_text(ivan):
    assert "password" not in vars(ivan)
    assert ivan._password_hash != b"Secret123"
    assert ivan._verify_password("Secret123") is True
    assert ivan._verify_password("Incorrect") is False


def test_audit_log_is_read_only_snapshot(bank, ivan):
    cid = bank.add_client(ivan)
    with pytest.raises(AuthenticationError):
        bank.authenticate_client(cid, "WrongPass")
    events = bank.suspicious_events
    events[0]["action"] = "fake"
    events.append({"action": "extra"})
    assert len(bank.suspicious_events) == 1
    assert bank.suspicious_events[0]["action"] == "FAILED_LOGIN"
