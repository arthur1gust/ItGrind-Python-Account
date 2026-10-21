from accounts.abstract_account import AbstractAccount
from accounts.enums import AccountStatus
from accounts.exceptions import (
    AccountClosedError,
    AccountFrozenError,
    InsufficientFundsError,
    InvalidOperationError,
)


class BankAccount(AbstractAccount):

    ALLOWED_CURRENCIES = {
        "RUB", "USD", "EUR", "KZT", "CNY"
    }

    def __init__(
        self,
        owner,
        balance=0,
        currency="RUB",
        status=AccountStatus.ACTIVE,
        account_id=None
    ):
        # Проверка валюты
        if (
            not isinstance(currency, str)
            or currency not in self.ALLOWED_CURRENCIES
        ):
            raise InvalidOperationError(
                "Недопустимая валюта"
            )

        # Вызов конструктора родительского класса
        super().__init__(
            owner, balance, status, account_id
        )

        self.currency = currency

    # Проверка статуса счёта
    def _check_status(self):

        if self.status == AccountStatus.FROZEN:
            raise AccountFrozenError(
                "Счёт заморожен"
            )

        if self.status == AccountStatus.CLOSED:
            raise AccountClosedError(
                "Счёт закрыт"
            )

    # Пополнение счёта
    def deposit(self, amount):

        self._check_status()
        amount = self._validate_amount(amount)

        self._balance += amount

    # Снятие денег
    def withdraw(self, amount):

        self._check_status()
        amount = self._validate_amount(amount)

        if amount > self._balance:
            raise InsufficientFundsError(
                "Недостаточно средств"
            )

        self._balance -= amount

    # Получение информации о счёте
    def get_account_info(self):

        return {
            "account_id": self.account_id,
            "owner": self.owner,
            "balance": f"{self.balance:.2f}",
            "status": self.status.value,
            "currency": self.currency,
        }

    # Строковое представление
    def __str__(self):

        return (
            f"Тип: {self.__class__.__name__}; "
            f"Владелец: {self.owner}; "
            f"Номер: ****{self.account_id[-4:]}; "
            f"Статус: {self.status.value}; "
            f"Баланс: {self.balance:.2f} {self.currency}"
        )