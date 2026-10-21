from accounts.bank_account import BankAccount
from accounts.enums import AccountStatus
from accounts.exceptions import (
    AccountClosedError,
    AccountFrozenError,
    InsufficientFundsError,
)


def main():

    # Создаём активный счёт
    account = BankAccount(
        owner="Иван",
        balance=1000,
        currency="RUB"
    )

    print("Активный счёт:")
    print(account)

    # Пополняем и снимаем деньги
    account.deposit(500)
    account.withdraw(300)

    print("После пополнения на 500 и снятия 300:")
    print(account)

    print(account.get_account_info())

    # Создаём замороженный счёт
    frozen = BankAccount(
        owner="Пётр",
        balance=2000,
        currency="USD",
        status=AccountStatus.FROZEN
    )

    print("\nЗамороженный счёт:")
    print(frozen)

    # Пытаемся снять деньги
    try:
        frozen.withdraw(100)
    except AccountFrozenError as error:
        print("Ошибка:", error)

    # Пытаемся пополнить замороженный счёт
    try:
        frozen.deposit(100)
    except AccountFrozenError as error:
        print("Ошибка:", error)

    # Пытаемся снять больше денег, чем есть
    try:
        account.withdraw(100000)
    except InsufficientFundsError as error:
        print("Ошибка:", error)

    # Создаём закрытый счёт
    closed = BankAccount(
        owner="Анна",
        status=AccountStatus.CLOSED
    )

    try:
        closed.deposit(100)
    except AccountClosedError as error:
        print("Ошибка:", error)


if __name__ == "__main__":
    main()