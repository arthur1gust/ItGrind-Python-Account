class AccountFrozenError(Exception):
    """Операция запрещена: счёт заморожен."""


class AccountClosedError(Exception):
    """Операция запрещена: счёт закрыт."""


class InvalidOperationError(Exception):
    """Некорректные данные или сумма операции."""


class InsufficientFundsError(Exception):
    """Недостаточно средств для снятия."""