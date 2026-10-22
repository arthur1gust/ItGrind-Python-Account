
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from accounts.bank_account import BankAccount
from accounts.enums import AccountStatus
from accounts.exceptions import (
    InsufficientFundsError,
    InvalidOperationError,
)


class InvestmentAccount(BankAccount):

    ALLOWED_ASSETS = {"stocks", "bonds", "etf"}

    def __init__(
        self,
        owner,
        balance=0,
        currency="RUB",
        status=AccountStatus.ACTIVE,
        account_id=None
    ):
        super().__init__(
            owner=owner,
            balance=balance,
            currency=currency,
            status=status,
            account_id=account_id
        )

        # Сколько денег вложено в каждый тип актива
        self._portfolio = {
            "stocks": Decimal("0"),
            "bonds": Decimal("0"),
            "etf": Decimal("0")
        }

    @property
    def portfolio(self):
        return self._portfolio.copy()

    def _check_asset_type(self, asset_type):
        if asset_type not in self.ALLOWED_ASSETS:
            raise InvalidOperationError(
                f"Недопустимый тип актива: {asset_type}"
            )

    # Перевод свободных средств в портфель
    def invest(self, asset_type, amount):
        self._check_status()
        self._check_asset_type(asset_type)
        amount = self._validate_amount(amount)

        if amount > self._balance:
            raise InsufficientFundsError(
                "Недостаточно средств для инвестирования"
            )

        self._balance -= amount
        self._portfolio[asset_type] += amount

    # Возврат средств из портфеля
    def sell_asset(self, asset_type, amount):
        self._check_status()
        self._check_asset_type(asset_type)
        amount = self._validate_amount(amount)

        if amount > self._portfolio[asset_type]:
            raise InsufficientFundsError(
                "Недостаточно средств в выбранном активе"
            )

        self._portfolio[asset_type] -= amount
        self._balance += amount

    # Переопределяем снятие средств
    def withdraw(self, amount):
        self._check_status()
        amount = self._validate_amount(amount)

        if amount > self._balance:
            raise InsufficientFundsError(
                "Недостаточно свободных средств"
            )

        # Используем базовую операцию снятия
        super().withdraw(amount)

    # Прогноз годового прироста портфеля
    def project_yearly_growth(self, growth_rates):
        if not isinstance(growth_rates, dict):
            raise InvalidOperationError(
                "Ставки должны передаваться словарём"
            )

        total_growth = Decimal("0")

        for asset_type, invested_amount in self._portfolio.items():
            if invested_amount == 0:
                continue

            if asset_type not in growth_rates:
                raise InvalidOperationError(
                    f"Не указана ставка для {asset_type}"
                )

            raw_rate = growth_rates[asset_type]

            if isinstance(raw_rate, bool):
                raise InvalidOperationError(
                    "Некорректная ставка доходности"
                )

            try:
                rate = Decimal(str(raw_rate))
            except (InvalidOperation, ValueError, TypeError):
                raise InvalidOperationError(
                    "Некорректная ставка доходности"
                ) from None

            if not rate.is_finite():
                raise InvalidOperationError(
                    "Ставка должна быть конечным числом"
                )

            total_growth += invested_amount * rate

        return total_growth.quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP
        )

    def get_account_info(self):
        info = super().get_account_info()

        info["portfolio"] = {
            asset: str(amount)
            for asset, amount in self._portfolio.items()
        }

        info["portfolio_total"] = str(
            sum(self._portfolio.values(), Decimal("0"))
        )

        return info

    def __str__(self):
        portfolio_text = ", ".join(
            f"{asset}: {amount:.2f}"
            for asset, amount in self._portfolio.items()
        )

        return (
            f"{super().__str__()}; "
            f"Портфель: {portfolio_text}"
        )
