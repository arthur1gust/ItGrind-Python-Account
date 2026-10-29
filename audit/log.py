from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import json
from pathlib import Path
from typing import Callable


class AuditLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class AuditEvent:
    timestamp: datetime
    event_type: str
    level: AuditLevel
    client_id: str | None = None
    transaction_id: str | None = None
    details: str = ""
    error_type: str | None = None

    def as_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(timespec="seconds"),
            "event_type": self.event_type,
            "level": self.level.value,
            "client_id": self.client_id,
            "transaction_id": self.transaction_id,
            "details": self.details,
            "error_type": self.error_type,
        }


class AuditLog:
    """Ведёт историю событий. Сохраняет строку JSONL при каждом log().

    Если файл временно недоступен, событие остаётся в RAM, ошибка записи
    сохраняется в persistence_errors; не меняем результат финансовой операции.
    Это best-effort аудит, а не защищённый неизменяемый журнал.
    """

    def __init__(self, log_file: str | Path | None = None,
                 now_provider: Callable[[], datetime] | None = None,
                 load_existing: bool = False):
        self._entries: list[AuditEvent] = []
        self.log_file = Path(log_file) if log_file is not None else None
        self._now = now_provider or datetime.now
        if not callable(self._now):
            raise TypeError("now_provider должен быть функцией")
        self.persistence_errors: list[str] = []
        if load_existing:
            if self.log_file is None:
                raise ValueError("Для load_existing нужен log_file")
            self._load()

    def _load(self) -> None:
        if not self.log_file.exists():
            return
        with self.log_file.open("r", encoding="utf-8") as source:
            for index, line in enumerate(source, 1):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    self._entries.append(AuditEvent(
                        timestamp=datetime.fromisoformat(data["timestamp"]),
                        event_type=data["event_type"],
                        level=AuditLevel(data["level"]),
                        client_id=data.get("client_id"),
                        transaction_id=data.get("transaction_id"),
                        details=data.get("details", ""),
                        error_type=data.get("error_type"),
                    ))
                except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise ValueError(f"Некорректная строка аудита {index}") from exc

    def log(self, event_type: str, level: AuditLevel,
            client_id: str | None = None, transaction_id: str | None = None,
            details: str = "", error_type: str | None = None,
            now: datetime | None = None) -> AuditEvent:
        if not isinstance(level, AuditLevel):
            raise TypeError("level должен быть AuditLevel")
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("Нужно указать event_type")
        if not isinstance(details, str):
            raise TypeError("details должен быть строкой")
        event = AuditEvent(
            timestamp=now if now is not None else self._now(),
            event_type=event_type.strip(), level=level,
            client_id=client_id, transaction_id=transaction_id,
            details=details, error_type=error_type,
        )
        self._entries.append(event)
        if self.log_file is not None:
            try:
                self.log_file.parent.mkdir(parents=True, exist_ok=True)
                with self.log_file.open("a", encoding="utf-8") as dest:
                    dest.write(json.dumps(event.as_dict(), ensure_ascii=False) + "\n")
            except OSError as exc:
                self.persistence_errors.append(f"{type(exc).__name__}: {exc}")
        return event

    @property
    def entries(self) -> tuple[AuditEvent, ...]:
        return tuple(self._entries)

    def filter(self, *, level: AuditLevel | None = None,
               event_type: str | None = None,
               client_id: str | None = None,
               transaction_id: str | None = None,
               from_time: datetime | None = None,
               to_time: datetime | None = None) -> list[AuditEvent]:
        return [event for event in self._entries
                if (level is None or event.level == level)
                and (event_type is None or event.event_type == event_type)
                and (client_id is None or event.client_id == client_id)
                and (transaction_id is None or event.transaction_id == transaction_id)
                and (from_time is None or event.timestamp >= from_time)
                and (to_time is None or event.timestamp <= to_time)]
