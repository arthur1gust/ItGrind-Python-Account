import csv
from decimal import Decimal
import json
from pathlib import Path

import pytest

from bank_simulation import Day6Simulation
from reporting import ReportBuilder
from transactions import TransactionStatus, TransactionType


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    base = tmp_path_factory.mktemp("day7_source")
    simulation = Day6Simulation(output_dir=base, sink=None)
    simulation.run()
    return simulation, ReportBuilder(simulation)


def test_builder_requires_completed_simulation(tmp_path):
    demo = Day6Simulation(output_dir=tmp_path, sink=None)
    with pytest.raises(ValueError, match="demo.run"):
        ReportBuilder(demo)


def test_builder_requires_simulation():
    with pytest.raises(TypeError):
        ReportBuilder(object())


def test_bank_report_contains_real_day6_totals(session):
    demo, builder = session
    data = builder.bank_report()
    assert data["report_type"] == "bank"
    assert (data["clients_count"], data["accounts_count"]) == (7, 14)
    assert data["transaction_statistics"]["total"] == 45
    assert data["transaction_statistics"]["by_status"] == {
        "cancelled": 1, "completed": 36, "failed": 8
    }
    assert data["total_balance_by_currency"] == {
        "RUB": "311022.50", "USD": "1715.00"
    }
    assert data["top_3_clients_rub"][0]["full_name"] == "Иван Петров"


def test_client_report_contains_accounts_and_incoming_history(session):
    demo, builder = session
    client_id = demo.clients["Анна Сидорова"]
    data = builder.client_report(client_id)
    assert data["report_type"] == "client"
    assert len(data["accounts"]) == 2
    assert data["client"]["full_name"] == "Анна Сидорова"
    assert data["transactions_count"] == len(data["transactions"])
    assert data["transactions_count"] > 0
    assert any(tx["client_id"] != client_id for tx in data["transactions"])
    assert set(data["total_balance_by_currency"]) == {"RUB", "USD"}
    assert data["risk_profile"]["client_id"] == client_id


def test_client_report_rejects_unknown_id(session):
    _, builder = session
    with pytest.raises(KeyError):
        builder.client_report("missing")


def test_risk_report_counts_blocked_and_errors(session):
    _, builder = session
    risk = builder.risk_report()
    assert risk["report_type"] == "risk"
    assert len(risk["blocked_operations"]) == 5
    assert len(risk["high_risk_events"]) == 5
    assert risk["error_statistics"]["blocked"] == 5
    assert len(risk["client_risk_profiles"]) == 7
    assert sum(risk["by_risk_level"].values()) == 45


@pytest.mark.parametrize("kind", ["client", "bank", "risk"])
def test_text_report_contains_header(session, kind):
    demo, builder = session
    content = {
        "client": builder.client_report(next(iter(demo.clients.values()))),
        "bank": builder.bank_report(),
        "risk": builder.risk_report(),
    }[kind]
    text = builder.text_report(content)
    assert "ITGRIND" in text and "Время:" in text
    assert text.endswith("\n")
    assert "DemoPassword123" not in text


def test_invalid_report_is_rejected(session, tmp_path):
    _, builder = session
    for operation in (
        lambda: builder.text_report({}),
        lambda: builder.export_to_json({}, tmp_path / "bad.json"),
        lambda: builder.export_to_csv({}, tmp_path / "bad.csv"),
    ):
        with pytest.raises(ValueError):
            operation()


def test_json_export_roundtrip_utf8(session, tmp_path):
    demo, builder = session
    data = builder.client_report(demo.clients["Иван Петров"])
    path = builder.export_to_json(data, tmp_path / "nested" / "client.json")
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == data
    assert "Иван Петров" in path.read_text(encoding="utf-8")


def test_csv_export_contains_nested_fields(session, tmp_path):
    _, builder = session
    report = builder.bank_report()
    destination = builder.export_to_csv(report, tmp_path / "bank.csv")
    with destination.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    assert rows
    assert list(rows[0]) == ["section", "record", "field", "value"]
    assert any(row["section"] == "total_balance_by_currency" and
               row["field"] == "RUB" and row["value"] == "311022.50" for row in rows)
    assert any(row["section"] == "top_3_clients_rub" and
               row["field"] == "full_name" and row["value"] == "Иван Петров" for row in rows)


def test_transactions_csv_has_45_rows(session, tmp_path):
    _, builder = session
    destination = builder.export_transactions_csv(tmp_path / "transactions.csv")
    with destination.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 45
    assert {row["status"] for row in rows} == {"completed", "failed", "cancelled"}
    assert any(row["risk_level"] == "high" for row in rows)


def test_text_export(session, tmp_path):
    _, builder = session
    data = builder.risk_report()
    saved = builder.export_to_text(data, tmp_path / "risk.txt")
    assert saved.read_text(encoding="utf-8") == builder.text_report(data)


def test_balance_history_bank_final_equals_actual_totals(session):
    demo, builder = session
    for currency, value in demo.bank.get_total_balance().items():
        history = builder.balance_history(currency)
        assert Decimal(history[-1]["balance"]) == value
        assert history[0]["label"] == "До операций"
        assert all(row["step"] == i for i, row in enumerate(history))
        assert len(history) >= 2


def test_balance_history_reconstructs_initial_totals(session):
    _, builder = session
    # Day6 starts at RUB 312800 and USD 1700. Before investing 3000 RUB
    # the balance is simply reallocated, so total accounting value unchanged.
    assert Decimal(builder.balance_history("RUB")[0]["balance"]) == Decimal("312800")
    assert Decimal(builder.balance_history("USD")[0]["balance"]) == Decimal("1700")


def test_balance_history_single_client_equals_report(session):
    demo, builder = session
    for client_id in demo.clients.values():
        data = builder.client_report(client_id)
        for currency, value in data["total_balance_by_currency"].items():
            history = builder.balance_history(currency, client_id)
            assert Decimal(history[-1]["balance"]) == Decimal(value)


def test_balance_history_ignores_failed_cancelled_transactions(session):
    demo, builder = session
    all_tx = demo.transactions
    assert any(tx.status == TransactionStatus.FAILED for tx in all_tx)
    assert any(tx.status == TransactionStatus.CANCELLED for tx in all_tx)
    for currency in ("RUB", "USD"):
        total = Decimal(builder.balance_history(currency)[0]["balance"])
        for tx in all_tx:
            for aid, delta in builder._transaction_deltas(tx):
                if demo.bank._accounts[aid].currency == currency:
                    total += delta
        assert total == demo.bank.get_total_balance()[currency]


def test_balance_history_rejects_unknown_client_and_currency(session):
    _, builder = session
    with pytest.raises(KeyError):
        builder.balance_history("RUB", client_id="missing")
    with pytest.raises(ValueError):
        builder.balance_history("BTC")


def test_save_charts_creates_valid_pngs(session, tmp_path):
    demo, builder = session
    client_id = demo.clients["Анна Сидорова"]
    charts = builder.save_charts(tmp_path / "charts", client_id)
    assert {"transaction_status_pie", "top_clients_bar", "risk_levels_bar",
            "client_balance_rub", "client_balance_usd"} <= set(charts)
    for key, path in charts.items():
        assert path.is_file(), key
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        assert path.stat().st_size > 2000


def test_save_charts_bank_currency_separation(session, tmp_path):
    _, builder = session
    charts = builder.save_charts(tmp_path / "charts")
    assert "balance_rub" in charts
    assert "balance_usd" in charts
    assert "client_balance_rub" not in charts


def test_save_charts_rejects_unknown_client(session, tmp_path):
    _, builder = session
    with pytest.raises(KeyError):
        builder.save_charts(tmp_path / "charts", client_id="missing")


def test_reports_do_not_contain_sensitive_client_fields(session):
    demo, builder = session
    reports = [builder.bank_report(), builder.risk_report()]
    reports.extend(builder.client_report(cid) for cid in demo.clients.values())
    joined = json.dumps(reports, ensure_ascii=False)
    assert "DemoPassword123" not in joined
    assert "@example.local" not in joined
    assert "+700000000" not in joined


def test_report_builder_does_not_mutate_balances_or_states(session, tmp_path):
    demo, builder = session
    balances_before = demo.bank.get_total_balance().copy()
    before = [(tx.transaction_id, tx.status, tx.attempts) for tx in demo.transactions]
    builder.bank_report()
    builder.risk_report()
    builder.client_report(next(iter(demo.clients.values())))
    builder.save_charts(tmp_path / "no_mutation")
    assert demo.bank.get_total_balance() == balances_before
    assert [(tx.transaction_id, tx.status, tx.attempts) for tx in demo.transactions] == before


def test_cli_full_day7_end_to_end(tmp_path, capsys):
    from demo_day7 import main
    demo, builder, files = main(["--quiet", "--output-dir", str(tmp_path)])
    output = capsys.readouterr().out
    assert "СОХРАНЁННЫЕ ФАЙЛЫ" in output
    assert "Готово: 7 клиентов, 14 счетов, 45 транзакций" in output
    assert len(files) == 16
    assert all(path.is_file() for path in files.values())
    assert {"balance_rub", "balance_usd", "client_balance_rub"} <= files.keys()
    assert demo.completed
    assert builder.bank_report()["transaction_statistics"]["total"] == 45


def test_cli_can_generate_usd_client_chart(tmp_path, capsys):
    from demo_day7 import main
    _, _, files = main(["--quiet", "--client", "2", "--output-dir", str(tmp_path)])
    capsys.readouterr()
    assert "client_balance_rub" in files and "client_balance_usd" in files
    data = json.loads((tmp_path / "client_report.json").read_text(encoding="utf-8"))
    assert data["client"]["full_name"] == "Анна Сидорова"
