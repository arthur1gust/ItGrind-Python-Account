from abc import ABC, abstractmethod
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from accounts.enums import AccountStatus
from accounts.exceptions import InvalidOperationError


class AbstractAccount(ABC):
    _used_ids = set()

    def __init__(
        self,
        owner,
        balance=0,
        status=AccountStatus.ACTIVE,
        account_id=None
    ):
        # Проверка владельца
        if not isinstance(owner, str) or not owner.strip():
            raise InvalidOperationError(
                "Имя владельца должно быть непустой строкой"
            )

        # Проверка статуса
        if not isinstance(status, AccountStatus):
            raise InvalidOperationError(
                "Недопустимый статус счёта"
            )

        # Проверка начального баланса
        initial_balance = self._validate_amount(
            balance, allow_zero=True
        )

        # Генерация номера счёта
        if account_id is None:
            while True:
                candidate = str(
                    uuid4().int % 10**12
                ).zfill(12)

                if candidate not in AbstractAccount._used_ids:
                    account_id = candidate
                    break

        elif (
            not isinstance(account_id, str)
            or not account_id.isascii()
            or not account_id.isdigit()
        ):
            raise InvalidOperationError(
                "Номер счёта должен состоять из цифр"
            )

        # Проверка уникальности
        if account_id in AbstractAccount._used_ids:
            raise InvalidOperationError(
                "Счёт с таким номером уже существует"
            )

        AbstractAccount._used_ids.add(account_id)

        # Атрибуты счёта
        self.account_id = account_id
        self.owner = owner.strip()
        self._balance = initial_balance
        self.status = status

    @staticmethod
    def _validate_amount(amount, allow_zero=False):
        if isinstance(amount, bool) or not isinstance(
            amount, (int, float, str, Decimal)
        ):
            raise InvalidOperationError(
                "Сумма должна быть числом"
            )

        try:
            value = Decimal(str(amount))
        except (InvalidOperation, ValueError):
            raise InvalidOperationError(
                "Некорректная сумма"
            ) from None

        if (
            not value.is_finite()
            or value < 0
            or (value == 0 and not allow_zero)
        ):
            raise InvalidOperationError(
                "Сумма должна быть положительной"
            )

        return value

    @property
    def balance(self):
        return self._balance

    @abstractmethod
    def deposit(self, amount):
        pass

    @abstractmethod
    def withdraw(self, amount):
        pass

    @abstractmethod
    def get_account_info(self):
        pass