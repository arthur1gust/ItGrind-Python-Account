class TransactionProcessingError(Exception):
    """Операция невозможна: повтор не поможет без изменения условий."""


class TemporaryProcessingError(TransactionProcessingError):
    """Временный сбой (например, внешнего шлюза); допускает повтор."""


class RiskBlockedError(TransactionProcessingError):
    """День 5: высокий риск, операция остановлена до изменения балансов."""
