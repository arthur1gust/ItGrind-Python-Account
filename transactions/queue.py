from datetime import datetime

from .models import Transaction, TransactionStatus


class TransactionQueue:
    def __init__(self):
        self._items: dict[str, tuple[int, Transaction]] = {}
        self._known_ids: set[str] = set()
        self._next_sequence = 0

    def add(self, transaction: Transaction) -> str:
        if not isinstance(transaction, Transaction):
            raise TypeError("В очередь можно добавить только Transaction")
        tid = transaction.transaction_id
        if tid in self._items:
            raise ValueError("Транзакция уже есть в очереди")
        if transaction.status not in (TransactionStatus.PENDING, TransactionStatus.RETRY_PENDING):
            raise ValueError("В очередь нельзя добавить завершённую/отменённую операцию")
        # Повторная постановка разрешена только после предусмотренной процессором ошибки.
        if tid in self._known_ids and transaction.status != TransactionStatus.RETRY_PENDING:
            raise ValueError("Повторное выполнение той же транзакции запрещено")
        self._known_ids.add(tid)
        self._items[tid] = (self._next_sequence, transaction)
        self._next_sequence += 1
        return tid

    def cancel(self, transaction_id: str, now: datetime | None = None) -> bool:
        item = self._items.pop(transaction_id, None)
        if item is None:
            return False
        transaction = item[1]
        transaction.status = TransactionStatus.CANCELLED
        transaction.failure_reason = "Отменено пользователем"
        transaction.finished_at = now or datetime.now()
        return True

    @staticmethod
    def _ready(transaction: Transaction, now: datetime) -> bool:
        return (
            (transaction.execute_at is None or transaction.execute_at <= now)
            and (transaction.next_attempt_at is None or transaction.next_attempt_at <= now)
        )

    def pop_ready(self, now: datetime | None = None) -> Transaction | None:
        """Из доступных выбирает наивысший приоритет; затем FIFO."""
        now = now or datetime.now()
        ready = [
            (sequence, transaction)
            for sequence, transaction in self._items.values()
            if self._ready(transaction, now)
        ]
        if not ready:
            return None
        sequence, selected = min(ready, key=lambda pair: (-pair[1].priority, pair[0]))
        del self._items[selected.transaction_id]
        return selected

    @property
    def size(self) -> int:
        return len(self._items)

    def __len__(self) -> int:
        return self.size

    def pending(self) -> list[Transaction]:
        return [tx for _, tx in sorted(self._items.values(), key=lambda pair: pair[0])]
