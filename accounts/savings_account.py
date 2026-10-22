
from decimal import Decimal, ROUND_HALF_UP

from accounts.bank_account import BankAccount
from accounts.exceptions import (
    InsufficientFundsError,
    InvalidOperationError,
)


class SavingsAccount(BankAccount):

    def __init__(
        self,
        owner,
        balance=0,
        currency="RUB",
        status=None,
        account_id=None,
        min_balance=0,
        monthly_interest_rate="0.01"
    ):
        from accounts.enums import AccountStatus

        if status is None:
            status = AccountStatus.ACTIVE

        # Проверяем параметры сберегательного счёта
        min_balance = self._validate_amount(
            min_balance, allow_zero=True
        )

        monthly_interest_rate = self._validate_amount(
            monthly_interest_rate, allow_zero=True
        )

        initial_balance = self._validate_amount(
            balance, allow_zero=True
        )

        if initial_balance < min_balance:
            raise InvalidOperationError(
                "Начальный баланс меньше минимального остатка"
            )

        # Инициализируем базовый банковский счёт
        super().__init__(
            owner=owner,
            balance=balance,
            currency=currency,
            status=status,
            account_id=account_id
        )

        self.min_balance = min_balance
        self.monthly_interest_rate = monthly_interest_rate

    # Снятие средств с учётом минимального остатка
    def withdraw(self, amount):
        self._check_status()
        amount = self._validate_amount(amount)

        if amount > self._balance:
            raise InsufficientFundsError(
                "Недостаточно средств"
            )

        if self._balance - amount < self.min_balance:
            raise InvalidOperationError(
                "Нельзя нарушать минимальный остаток"
            )

        super().withdraw(amount)

    # Начисление месячных процентов
    def apply_monthly_interest(self):
        self._check_status()

        interest = (
            self._balance * self.monthly_interest_rate
        ).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP
        )

        self._balance += interest
        return interest

    # Информация о сберегательном счёте
    def get_account_info(self):
        info = super().get_account_info()

        info["min_balance"] = str(self.min_balance)
        info["monthly_interest_rate"] = str(
            self.monthly_interest_rate
        )

        return info

    # Строковое представление
    def __str__(self):
        return (
            f"{super().__str__()}; "
            f"Минимальный остаток: {self.min_balance:.2f}; "
            f"Месячная ставка: "
            f"{self.monthly_interest_rate * 100:.2f}%"
        )
