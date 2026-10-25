class BankSystemError(Exception):
    """Общая ошибка управляющей системы банка."""


class ClientNotFoundError(BankSystemError):
    """Клиент не зарегистрирован."""


class AccountNotFoundError(BankSystemError):
    """Счёт не найден."""


class PermissionDeniedError(BankSystemError):
    """Клиент пытается работать с чужим счётом."""


class AuthenticationError(BankSystemError):
    """Неверные учётные данные."""


class ClientBlockedError(AuthenticationError):
    """Клиент заблокирован после трёх неудачных попыток входа."""


class RestrictedHoursError(BankSystemError):
    """Банковская операция запрещена с 00:00 до 05:00."""
