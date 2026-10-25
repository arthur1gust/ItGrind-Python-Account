from datetime import datetime
from decimal import Decimal

from accounts.bank_account import BankAccount
from accounts.enums import AccountStatus
from accounts.exceptions import (
    AccountClosedError,
    AccountFrozenError,
    InsufficientFundsError,
    InvalidOperationError,
)
from accounts.investment_account import InvestmentAccount
from accounts.premium_account import PremiumAccount
from accounts.savings_account import SavingsAccount

from .client import Client, ClientStatus
from .exceptions import (
    AccountNotFoundError,
    AuthenticationError,
    ClientBlockedError,
    ClientNotFoundError,
    PermissionDeniedError,
    RestrictedHoursError,
)


class Bank:
    """Хранит клиентов, счета и журнал подозрительных действий в памяти."""

    ACCOUNT_TYPES = {
        "bank": BankAccount,
        "savings": SavingsAccount,
        "premium": PremiumAccount,
        "investment": InvestmentAccount,
    }

    def __init__(self, name="Учебный банк", now_provider=None):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Название банка должно быть непустой строкой")
        self.name = name.strip()
        self._clients = {}           # client_id -> Client
        self._accounts = {}          # account_id -> BankAccount
        self._account_owners = {}    # account_id -> client_id
        self._suspicious_events = []
        self._now = now_provider if now_provider is not None else datetime.now
        if not callable(self._now):
            raise TypeError("now_provider должен быть функцией")

    def _flag_suspicious(self, client_id, action, details=""):
        """Записывает событие в журнал без паролей и других секретов."""
        self._suspicious_events.append({
            "time": self._now().isoformat(timespec="seconds"),
            "client_id": client_id,
            "action": action,
            "details": details,
        })

    @property
    def suspicious_events(self):
        """Снимок журнала; внешнему коду нельзя изменить журнал через него."""
        return [event.copy() for event in self._suspicious_events]

    def _get_client(self, client_id):
        client = self._clients.get(client_id)
        if client is None:
            raise ClientNotFoundError("Клиент не найден")
        return client

    def _require_active_client(self, client_id):
        client = self._get_client(client_id)
        if client.status == ClientStatus.BLOCKED:
            raise ClientBlockedError("Клиент заблокирован")
        return client

    def _check_operating_hours(self, client_id, operation):
        """С 00:00 включительно до 05:00 исключительно операции запрещены."""
        if 0 <= self._now().hour < 5:
            self._flag_suspicious(client_id, "NIGHT_OPERATION", operation)
            raise RestrictedHoursError(
                "Операции запрещены с 00:00 до 05:00"
            )

    def _get_account(self, account_id):
        account = self._accounts.get(account_id)
        if account is None:
            raise AccountNotFoundError("Счёт не найден")
        return account

    def _get_owned_account(self, client_id, account_id):
        self._require_active_client(client_id)
        account = self._get_account(account_id)
        if self._account_owners[account_id] != client_id:
            self._flag_suspicious(client_id, "FOREIGN_ACCOUNT_ACCESS", account_id)
            raise PermissionDeniedError("Клиент не является владельцем счёта")
        return account

    def add_client(self, client):
        """Регистрирует клиента. Возвращает client_id."""
        if not isinstance(client, Client):
            raise TypeError("Ожидался экземпляр Client")
        if client.client_id in self._clients:
            raise InvalidOperationError("Клиент уже зарегистрирован")
        self._clients[client.client_id] = client
        return client.client_id

    def authenticate_client(self, client_id, password):
        """Проверяет пароль и блокирует клиента после 3 ошибок подряд."""
        client = self._clients.get(client_id)
        if client is None:
            self._flag_suspicious(client_id, "UNKNOWN_CLIENT_LOGIN")
            raise AuthenticationError("Неверный ID клиента или пароль")
        if client.status == ClientStatus.BLOCKED:
            raise ClientBlockedError("Клиент заблокирован")
        if client._verify_password(password):
            client._reset_failed_logins()
            return True

        client._register_failed_login()
        self._flag_suspicious(
            client_id,
            "FAILED_LOGIN",
            f"Попыток подряд: {client.failed_login_attempts}",
        )
        if client.status == ClientStatus.BLOCKED:
            raise ClientBlockedError("Три неверные попытки: клиент заблокирован")
        raise AuthenticationError("Неверный ID клиента или пароль")

    def open_account(self, client_id, account_type="bank", balance=0,
                     currency="RUB", **kwargs):
        """Создаёт и привязывает один из четырёх типов счёта к клиенту."""
        client = self._require_active_client(client_id)
        self._check_operating_hours(client_id, "open_account")
        account_class = self.ACCOUNT_TYPES.get(account_type)
        if account_class is None:
            raise InvalidOperationError("Неизвестный тип счёта")
        forbidden_fields = {"owner", "account_id", "status"}
        if forbidden_fields.intersection(kwargs):
            raise InvalidOperationError("Служебные параметры задаёт банк")

        account = account_class(
            owner=client.full_name,
            balance=balance,
            currency=currency,
            **kwargs,
        )
        account_id = account.account_id
        self._accounts[account_id] = account
        self._account_owners[account_id] = client_id
        client._add_account(account_id)
        return account_id

    def deposit(self, client_id, account_id, amount):
        """Пополняет только собственный счёт активного клиента."""
        account = self._get_owned_account(client_id, account_id)
        self._check_operating_hours(client_id, "deposit")
        account.deposit(amount)
        return account.balance

    def withdraw(self, client_id, account_id, amount):
        """Снимает деньги по правилам конкретного типа счёта."""
        account = self._get_owned_account(client_id, account_id)
        self._check_operating_hours(client_id, "withdraw")
        try:
            account.withdraw(amount)
        except (InvalidOperationError, InsufficientFundsError):
            self._flag_suspicious(client_id, "REJECTED_WITHDRAWAL", account_id)
            raise
        return account.balance

    def invest(self, client_id, account_id, asset_type, amount):
        """Делегирует покупку учебных активов InvestmentAccount."""
        account = self._get_owned_account(client_id, account_id)
        self._check_operating_hours(client_id, "invest")
        if not isinstance(account, InvestmentAccount):
            raise InvalidOperationError("Счёт не является инвестиционным")
        account.invest(asset_type, amount)
        return account.balance

    def sell_asset(self, client_id, account_id, asset_type, amount):
        """Делегирует продажу учебных активов InvestmentAccount."""
        account = self._get_owned_account(client_id, account_id)
        self._check_operating_hours(client_id, "sell_asset")
        if not isinstance(account, InvestmentAccount):
            raise InvalidOperationError("Счёт не является инвестиционным")
        account.sell_asset(asset_type, amount)
        return account.balance

    @staticmethod
    def _account_value(account):
        """Учётная стоимость: свободные деньги + вложения по себестоимости."""
        total = account.balance
        if isinstance(account, InvestmentAccount):
            total += sum(account.portfolio.values(), Decimal("0"))
        return total

    def close_account(self, client_id, account_id):
        """Закрывает только активный пустой счёт; портфель тоже пуст."""
        account = self._get_owned_account(client_id, account_id)
        self._check_operating_hours(client_id, "close_account")
        if account.status == AccountStatus.CLOSED:
            raise AccountClosedError("Счёт уже закрыт")
        if account.status == AccountStatus.FROZEN:
            raise AccountFrozenError("Нельзя закрыть замороженный счёт")
        if self._account_value(account) != 0:
            raise InvalidOperationError("Для закрытия баланс и портфель должны быть пустыми")
        account.status = AccountStatus.CLOSED

    def freeze_account(self, account_id):
        """Административная заморозка; доступна независимо от времени суток."""
        account = self._get_account(account_id)
        if account.status == AccountStatus.CLOSED:
            raise AccountClosedError("Нельзя заморозить закрытый счёт")
        account.status = AccountStatus.FROZEN

    def unfreeze_account(self, account_id):
        """Административная разморозка."""
        account = self._get_account(account_id)
        if account.status == AccountStatus.CLOSED:
            raise AccountClosedError("Нельзя разморозить закрытый счёт")
        account.status = AccountStatus.ACTIVE

    def search_accounts(self, client_id=None, currency=None, status=None):
        """Административный поиск. Возвращает список словарей, не объекты."""
        if status is not None and not isinstance(status, AccountStatus):
            raise ValueError("status должен быть AccountStatus")
        if client_id is not None:
            self._get_client(client_id)
        result = []
        for account_id, account in self._accounts.items():
            if client_id is not None and self._account_owners[account_id] != client_id:
                continue
            if currency is not None and account.currency != currency:
                continue
            if status is not None and account.status != status:
                continue
            result.append(account.get_account_info())
        return result

    def get_total_balance(self):
        """Общая учётная стоимость по валютам, без некорректного сложения валют."""
        totals = {}
        for account in self._accounts.values():
            totals.setdefault(account.currency, Decimal("0"))
            totals[account.currency] += self._account_value(account)
        return totals

    def get_clients_ranking(self, currency="RUB"):
        """Список клиентов от большего капитала к меньшему в одной валюте."""
        if currency not in BankAccount.ALLOWED_CURRENCIES:
            raise InvalidOperationError("Недопустимая валюта рейтинга")
        ranking = []
        for client in self._clients.values():
            total = sum(
                (
                    self._account_value(self._accounts[account_id])
                    for account_id in client.account_numbers
                    if account_id in self._accounts
                    and self._accounts[account_id].currency == currency
                ),
                Decimal("0"),
            )
            ranking.append({
                "client_id": client.client_id,
                "full_name": client.full_name,
                "currency": currency,
                "total_balance": total,
            })
        return sorted(ranking, key=lambda row: row["total_balance"], reverse=True)
