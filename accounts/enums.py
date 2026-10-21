from enum import Enum


class AccountStatus(Enum):
    ACTIVE = "активный"
    FROZEN = "замороженный"
    CLOSED = "закрытый"