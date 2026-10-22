
from decimal import Decimal

from accounts.bank_account import BankAccount
from accounts.enums import AccountStatus
from accounts.exceptions import (
    InsufficientFundsError,
    InvalidOperationError,
)


class PremiumAccount(BankAccount):

    # Повышенный лимит снятия
    MAX_WITHDRAWAL = Decimal("500000")

    def __init__(
        self,
        owner,
        balance=0,
        currency="RUB",
        status=AccountStatus.ACTIVE,
        account_id=None,
        overdraft_limit=5000,
        fixed_fee=50
    ):

        # Проверяем новые параметры
        overdraft_limit = self._validate_amount(
            overdraft_limit, allow_zero=True
        )

        fixed_fee = self._validate_amount(
            fixed_fee, allow_zero=True
        )

        # Инициализируем родительский BankAccount
        super().__init__(
            owner=owner,
            balance=balance,
            currency=currency,
            status=status,
            account_id=account_id
        )

        self.overdraft_limit = overdraft_limit
        self.fixed_fee = fixed_fee

    # Снятие с учётом комиссии и овердрафта
    def withdraw(self, amount):

        self._check_status()
        amount = self._validate_amount(amount)

        # Проверяем лимит одной операции
        if amount > self.MAX_WITHDRAWAL:
            raise InvalidOperationError(
                "Превышен лимит премиального счёта"
            )

        # Сумма списания с комиссией
        total = amount + self.fixed_fee

        # Проверяем допустимую задолженность
        if self._balance - total < -self.overdraft_limit:
            raise InsufficientFundsError(
                "Превышен лимит овердрафта"
            )

        # Выполняем списание
        self._balance -= total

    # Расширенная информация о счёте
    def get_account_info(self):

        info = super().get_account_info()

        info["overdraft_limit"] = str(
            self.overdraft_limit
        )
        info["fixed_fee"] = str(self.fixed_fee)
        info["max_withdrawal"] = str(
            self.MAX_WITHDRAWAL
        )

        return info

    # Строковое представление
    def __str__(self):

        return (
            f"{super().__str__()}; "
            f"Овердрафт: {self.overdraft_limit:.2f}; "
            f"Комиссия: {self.fixed_fee:.2f}; "
            f"Лимит снятия: {self.MAX_WITHDRAWAL:.2f}"
        )
