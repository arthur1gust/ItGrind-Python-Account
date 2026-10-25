from enum import Enum
from hashlib import pbkdf2_hmac
from hmac import compare_digest
from secrets import token_bytes
from uuid import uuid4


class ClientStatus(Enum):
    ACTIVE = "активный"
    BLOCKED = "заблокирован"


class Client:
    """Физическое лицо с контактами, банковскими счетами и паролем.

    Примечание: защита пароля предусмотрена для обучения; это не
    полноценный сервис идентификации и авторизации.
    """

    def __init__(self, full_name, age, phone, email, password):
        if not isinstance(full_name, str) or not full_name.strip():
            raise ValueError("ФИО должно быть непустой строкой")
        if type(age) is not int or age < 18:
            raise ValueError("Клиенту должно быть не меньше 18 лет")
        if not isinstance(phone, str) or not phone.strip():
            raise ValueError("Укажите телефон клиента")
        if not isinstance(email, str) or not email.strip():
            raise ValueError("Укажите email клиента")
        if not isinstance(password, str) or len(password) < 8:
            raise ValueError("Пароль должен содержать не менее 8 символов")

        self.client_id = uuid4().hex
        self.full_name = full_name.strip()
        self.age = age
        self.status = ClientStatus.ACTIVE
        self._contacts = {
            "phone": phone.strip(),
            "email": email.strip(),
        }
        self._account_numbers = []
        self._failed_login_attempts = 0
        self._password_salt = token_bytes(16)
        self._password_hash = pbkdf2_hmac(
            "sha256", password.encode("utf-8"), self._password_salt, 100_000
        )

    @property
    def contacts(self):
        """Возвращает копию контактов (изменение копии не меняет объект)."""
        return self._contacts.copy()

    @property
    def account_numbers(self):
        """Возвращает копию списка счетов."""
        return self._account_numbers.copy()

    @property
    def failed_login_attempts(self):
        return self._failed_login_attempts

    def _verify_password(self, password):
        if not isinstance(password, str):
            return False
        candidate_hash = pbkdf2_hmac(
            "sha256", password.encode("utf-8"), self._password_salt, 100_000
        )
        return compare_digest(candidate_hash, self._password_hash)

    def _register_failed_login(self):
        self._failed_login_attempts += 1
        if self._failed_login_attempts >= 3:
            self.status = ClientStatus.BLOCKED

    def _reset_failed_logins(self):
        self._failed_login_attempts = 0

    def _add_account(self, account_id):
        if account_id not in self._account_numbers:
            self._account_numbers.append(account_id)

    def __repr__(self):
        return (
            f"Client(client_id={self.client_id!r}, "
            f"full_name={self.full_name!r}, status={self.status.name})"
        )
