import argparse
from collections import Counter

from bank_simulation import Day6Simulation


def show_clients(demo: Day6Simulation) -> None:
    print("\nКЛИЕНТЫ БАНКА")
    for i, name in enumerate(demo.clients, 1):
        client_id = demo.clients[name]
        print(f" {i}. {name:<20} счетов: {len(demo.bank._clients[client_id].account_numbers)}")


def show_client(demo: Day6Simulation, index: int, history_limit: int = 10) -> None:
    if not 1 <= index <= len(demo.clients):
        raise ValueError("Номер клиента вне диапазона 1–7")
    name = list(demo.clients)[index - 1]
    client_id = demo.clients[name]
    print(f"\nСЧЕТА КЛИЕНТА: {name}")
    for acc in demo.reports.client_accounts(client_id):
        extra = ""
        if "min_balance" in acc:
            extra = f", min_balance={acc['min_balance']}"
        if "overdraft_limit" in acc:
            extra = f", overdraft={acc['overdraft_limit']}"
        if "portfolio_total" in acc:
            extra = f", portfolio_total={acc['portfolio_total']}"
        print(f"  {acc['account_id']} | {acc['balance']} {acc['currency']} | "
              f"{acc['status']}{extra}")
    history = demo.reports.client_history(client_id, limit=history_limit)
    print(f"ИСТОРИЯ (последние {len(history)} записей, входящие + исходящие):")
    for tx in history:
        direction = "OUT" if tx["client_id"] == client_id else "IN "
        print(f"  {direction} {tx['transaction_id'][:8]} {tx['type']:<17} "
              f"{tx['amount']:>8} {tx['currency']} {tx['status']:<12} "
              f"risk={tx['risk_level'] or '-'}")
    profile = demo.reports.audit.client_risk_profile(client_id)
    print(f"РИСК-ПРОФИЛЬ: {profile['risk_level']} | "
          f"блокировок={profile['blocked_operations']} | "
          f"причины={', '.join(profile['triggers']) or 'нет'}")


def show_report(report: dict) -> None:
    stats = report["transaction_statistics"]
    errors = report["error_statistics"]
    print("\n" + "=" * 66)
    print("ДЕНЬ 6 — ИТОГОВЫЙ ОТЧЁТ")
    print("=" * 66)
    print(f"Клиентов: {report['clients_count']}; счетов: {report['accounts_count']}; "
          f"транзакций: {stats['total']}")
    print("Статусы:", stats["by_status"])
    print("По видам:", stats["by_type"])
    print("По уровням риска:", stats["by_risk"])
    print("Повторных попыток:", stats["retries"])
    print("Комиссии по валютам:", stats["fees_by_currency"])
    print("Осталось в очереди:", stats["queue_remaining"])
    print("Ошибки и блокировки:", errors)
    print("\nТОП-3 КЛИЕНТОВ (по RUB, портфель по себестоимости):")
    for rank, client in enumerate(report["top_3_clients_rub"], 1):
        print(f"  {rank}. {client['full_name']:<20} {client['total_balance']} RUB")
    print("\nОБЩИЙ БАЛАНС БАНКА (по валютам, не суммируем разные валюты):")
    for currency, balance in report["total_balance_by_currency"].items():
        print(f"  {currency}: {balance}")
    print("\nПОДОЗРИТЕЛЬНЫЕ ОПЕРАЦИИ (HIGH — риск-оценки):")
    high = report["high_risk_operations"]
    for event in high:
        print(f"  {event['transaction_id'][:8]} "
              f"client={event['client_id'][:8]} причина={event['details']}")
    print(f"Всего HIGH-событий: {len(high)}")


def interactive_menu(demo: Day6Simulation, report: dict) -> None:
    while True:
        print("\nМЕНЮ: 1 — клиенты | 2 — счета/история клиента | "
              "3 — подозрительные | 4 — отчёт | 0 — выход")
        try:
            answer = input("Выбор: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nЗавершено")
            return
        if answer == "0":
            return
        if answer == "1":
            show_clients(demo)
        elif answer == "2":
            show_clients(demo)
            try:
                number = int(input("Номер клиента (1–7): ").strip())
                show_client(demo, number, history_limit=20)
            except (ValueError, EOFError) as exc:
                print("Некорректный выбор:", exc)
        elif answer == "3":
            print("\nПодозрительных оценок MEDIUM + HIGH:", len(report["suspicious_operations"]))
            print("Высоких рисков:", len(report["high_risk_operations"]))
            for e in report["high_risk_operations"]:
                print(f"  {e['transaction_id'][:8]} {e['details']}")
        elif answer == "4":
            show_report(report)
        else:
            print("Неизвестный пункт меню")


def main(argv=None):
    parser = argparse.ArgumentParser(description="ItGrind День 6 — демонстрация банка")
    parser.add_argument("--quiet", action="store_true", help="без построчного журнала очереди")
    parser.add_argument("--interactive", action="store_true", help="меню просмотра клиентов и истории")
    parser.add_argument("--client", type=int, default=1, help="кого показать (номер 1–7)")
    parser.add_argument("--history-limit", type=int, default=10, help="сколько операций показать")
    args = parser.parse_args(argv)
    if not 1 <= args.client <= 7:
        parser.error("--client должен быть от 1 до 7")
    if args.history_limit < 0:
        parser.error("--history-limit должен быть >= 0")
    demo = Day6Simulation(sink=None if args.quiet else print)
    report = demo.run()
    show_report(report)
    show_client(demo, args.client, history_limit=args.history_limit)
    print("\nФАЙЛЫ:")
    print("  Аудит JSONL:", demo.log_file)
    print("  Отчёты JSON:", demo.report_file)
    if args.interactive:
        interactive_menu(demo, report)
    print("\nДемонстрация Дня 6 выполнена успешно.")
    return demo, report


if __name__ == "__main__":
    main()
