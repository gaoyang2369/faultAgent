from __future__ import annotations

from decimal import Decimal

from fault_diagnosis.single_agent.sql_result_parser import parse_sql_rows


def test_parse_sql_rows_accepts_python_datetime_repr() -> None:
    raw_output = (
        "[(673, datetime.datetime(2026, 6, 10, 12, 12, 59), 'G120电机1', 'G120电机1', "
        "'2026-06-10', '12:12:59', '42', 'A07089', '0', '5246', '10679', "
        "555.228, 823.412, 442.209, 0.775, 0.215, 0.028, -199.85, 46.811, "
        "31.123, 0.018, 1.295, 0.137, '0000:39:25', 37.008, 43.732, "
        "59.429, 3.548, 0.434, -0.009, datetime.datetime(2026, 6, 10, 12, 12, 59))]"
    )

    rows = parse_sql_rows(raw_output)

    assert len(rows) == 1
    assert rows[0]["id"] == 673
    assert rows[0]["device_name"] == "G120电机1"
    assert rows[0]["fault_code"] == "A07089"
    assert rows[0]["create_time"] == "2026-06-10 12:12:59"


def test_parse_sql_rows_accepts_driver_value_objects() -> None:
    raw_output = [
        (
            1,
            "2026-06-10 12:12:59",
            "G120电机1",
            "G120电机1",
            "2026-06-10",
            "12:12:59",
            "42",
            "A07089",
            "0",
            "5246",
            "10679",
            Decimal("555.228"),
        )
    ]

    rows = parse_sql_rows(raw_output)

    assert rows[0]["dc_voltage"] == Decimal("555.228")
    assert rows[0]["create_time"] is None
