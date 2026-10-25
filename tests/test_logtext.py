"""Tests for ESPHome console-text helpers and the streaming log filter.

Sample lines follow the formats ESPHome 2026.10 produces: the device's
``LogBuffer::write_header`` (ANSI colour, ``[L][tag:line]``, optional
``[thread]``) and ``esphome logs`` (adds ``[HH:MM:SS.mmm]``; in dashboard mode
ESC is printed as the literal text ``\\033``).
"""
import re

import pytest

from esphome_mcp_client.logtext import (
    LEVELS,
    LogEntry,
    LogFilter,
    clean_line,
    compile_search,
    parse_log_line,
    strip_ansi,
)

ESC = "\x1b"
# Device-native message: colour, header, body, reset.
DEVICE_LINE = f"{ESC}[0;36m[D][sensor:104]: 'Temp': Sending state 21.50 °C{ESC}[0m"
# ESP32 log from a non-main task: thread name in bold red, then colour restored.
THREAD_LINE = f"{ESC}[0;33m[W][esp32_ble_client:088]{ESC}[1;31m[BTU]{ESC}[0;33m: Disconnected{ESC}[0m"
# What the dashboard streams: timestamp first, ESC escaped as literal text.
DASH_LINE = r"[12:34:56.789]\033[0;32m[I][race:045]: Lap 3 finished\033[0m"


def entry(level, tag, msg="m"):
    letter = {1: "E", 2: "W", 3: "I", 4: "C", 5: "D", 6: "V", 7: "VV"}[level]
    return f"[{letter}][{tag}:001]: {msg}"


def run(flt, lines):
    out = []
    for line in lines:
        out.extend(flt.feed(line))
    return out


# ---- stripping ---------------------------------------------------------------


def test_strip_ansi_real_escapes():
    assert strip_ansi(DEVICE_LINE) == "[D][sensor:104]: 'Temp': Sending state 21.50 °C"


def test_strip_ansi_literal_escapes_from_dashboard():
    assert strip_ansi(DASH_LINE) == "[12:34:56.789][I][race:045]: Lap 3 finished"


def test_strip_ansi_leaves_other_backslashes():
    assert strip_ansi(r"path C:\temp\new") == r"path C:\temp\new"


def test_clean_line_keeps_final_carriage_return_state():
    assert clean_line("[1/9] Building\r[2/9] Building\r[9/9] Linking\x1b[K") == "[9/9] Linking"


# ---- parsing -----------------------------------------------------------------


def test_parse_device_line():
    parsed = parse_log_line(clean_line(DEVICE_LINE))
    assert parsed == LogEntry(
        level=LEVELS["debug"], tag="sensor", line=104,
        message="'Temp': Sending state 21.50 °C",
    )


def test_parse_thread_name():
    parsed = parse_log_line(clean_line(THREAD_LINE))
    assert parsed.tag == "esp32_ble_client"
    assert parsed.thread == "BTU"
    assert parsed.level == LEVELS["warn"]
    assert parsed.message == "Disconnected"


def test_parse_dashboard_timestamp():
    parsed = parse_log_line(clean_line(DASH_LINE))
    assert parsed.time == "12:34:56.789"
    assert parsed.tag == "race"
    assert parsed.level == LEVELS["info"]


@pytest.mark.parametrize(
    ("letter", "level"),
    [("E", 1), ("W", 2), ("I", 3), ("C", 4), ("D", 5), ("V", 6), ("VV", 7)],
)
def test_parse_every_level(letter, level):
    assert parse_log_line(f"[{letter}][app:100]: x").level == level


@pytest.mark.parametrize(
    "line",
    [
        "INFO Starting log output from 192.168.1.50 using esphome API",
        "  continuation text",
        "[X][tag:001]: unknown level",
        "plain text",
    ],
)
def test_parse_non_entries(line):
    assert parse_log_line(line) is None


# ---- search ------------------------------------------------------------------


def test_search_substring_is_case_insensitive_and_literal():
    pattern = compile_search("lap 3 (fin")
    assert pattern.search("LAP 3 (FINISHED")
    assert not pattern.search("lap 3 finished")


def test_search_regex_forms():
    assert compile_search("/lap \\d+/").search("lap 12")
    assert not compile_search("/LAP/").search("lap")
    assert compile_search("/LAP/i").search("lap")


def test_search_invalid_regex_raises():
    with pytest.raises(re.error):
        compile_search("/lap (/")


# ---- filter ------------------------------------------------------------------


def test_default_filter_keeps_everything_but_config_dump():
    flt = LogFilter()
    out = run(flt, [entry(4, "logger", "Logger:"), entry(5, "race"), entry(7, "wifi")])
    assert out == [entry(5, "race"), entry(7, "wifi")]
    assert flt.lines_received == 3
    assert flt.lines_returned == 2


def test_include_config_dump():
    out = run(LogFilter(include_config_dump=True), [entry(4, "logger", "Logger:")])
    assert out == [entry(4, "logger", "Logger:")]


def test_min_level():
    flt = LogFilter(min_level=LEVELS["info"])
    out = run(flt, [entry(1, "a"), entry(2, "b"), entry(3, "c"), entry(5, "d"), entry(6, "e")])
    assert out == [entry(1, "a"), entry(2, "b"), entry(3, "c")]


def test_tags_include_with_glob_and_exclude():
    flt = LogFilter(tags=["race", "esp32_ble*"], exclude_tags=["esp32_ble_tracker"])
    lines = [entry(5, "race"), entry(5, "esp32_ble_client"), entry(5, "esp32_ble_tracker"), entry(5, "wifi")]
    assert run(flt, lines) == [entry(5, "race"), entry(5, "esp32_ble_client")]


def test_tag_match_is_exact_not_substring():
    assert run(LogFilter(tags=["race"]), [entry(5, "racetrack")]) == []


def test_search_matches_tag_or_message():
    flt = LogFilter(search="lap")
    lines = [entry(5, "race", "Lap 1"), entry(5, "wifi", "connected"), entry(5, "lap_timer", "tick")]
    assert run(flt, lines) == [entry(5, "race", "Lap 1"), entry(5, "lap_timer", "tick")]


def test_search_with_context_and_separators():
    flt = LogFilter(search="HIT", context=1)
    lines = [entry(5, "t", f"n{i}") for i in range(3)]
    lines += [entry(5, "t", "HIT one")]
    lines += [entry(5, "t", f"m{i}") for i in range(4)]
    lines += [entry(5, "t", "HIT two")]
    out = run(flt, lines)
    assert out == [
        entry(5, "t", "n2"),
        entry(5, "t", "HIT one"),
        entry(5, "t", "m0"),
        "--",
        entry(5, "t", "m3"),
        entry(5, "t", "HIT two"),
    ]
    # The separator isn't a log line, so it isn't counted.
    assert flt.lines_returned == 5


def test_context_only_draws_from_eligible_lines():
    flt = LogFilter(search="HIT", context=2, tags=["race"])
    lines = [entry(5, "race", "before"), entry(5, "wifi", "noise"), entry(5, "race", "HIT")]
    assert run(flt, lines) == [entry(5, "race", "before"), entry(5, "race", "HIT")]


def test_no_separator_without_context():
    flt = LogFilter(search="HIT")
    out = run(flt, [entry(5, "t", "HIT"), entry(5, "t", "x"), entry(5, "t", "HIT")])
    assert "--" not in out


def test_continuation_lines_follow_their_entry():
    flt = LogFilter(tags=["race"])
    lines = [
        entry(5, "race", "Results:"),
        "  1. car A",
        entry(5, "wifi", "Scan:"),
        "  ssid B",
        entry(5, "race", "done"),
    ]
    assert run(flt, lines) == [entry(5, "race", "Results:"), "  1. car A", entry(5, "race", "done")]


def test_continuations_of_config_dump_are_dropped_too():
    lines = [entry(4, "sensor", "Sensor 'Temp'"), "  Unit: °C", entry(5, "race")]
    assert run(LogFilter(), lines) == [entry(5, "race")]


def test_context_counts_entries_not_lines():
    # One entry of leading context includes all of that entry's continuation
    # lines, and never a continuation without its entry.
    flt = LogFilter(search="HIT", context=1)
    lines = [
        entry(5, "t", "older"),
        "  older detail",
        entry(5, "t", "pre"),
        "  pre detail 1",
        "  pre detail 2",
        entry(5, "t", "HIT"),
        "  hit detail",
        entry(5, "t", "post"),
        "  post detail",
        entry(5, "t", "later"),
    ]
    assert run(flt, lines) == [
        entry(5, "t", "pre"),
        "  pre detail 1",
        "  pre detail 2",
        entry(5, "t", "HIT"),
        "  hit detail",
        entry(5, "t", "post"),
        "  post detail",
    ]


def test_capture_status_lines_are_always_kept():
    flt = LogFilter(tags=["race"], search="nothing-matches")
    lines = [
        "INFO Starting log output from 192.168.1.50 using esphome API",
        entry(5, "wifi"),
        "WARNING Disconnected from API",
    ]
    assert run(flt, lines) == [lines[0], lines[2]]


def test_leading_indented_line_before_any_entry_is_kept():
    assert run(LogFilter(tags=["race"]), ["  preamble"]) == ["  preamble"]


def test_feed_cleans_ansi_from_real_formats():
    flt = LogFilter(tags=["esp32_ble_client", "race"])
    out = run(flt, [DEVICE_LINE, THREAD_LINE, DASH_LINE])
    assert out == [
        "[W][esp32_ble_client:088][BTU]: Disconnected",
        "[12:34:56.789][I][race:045]: Lap 3 finished",
    ]


def test_blank_lines_are_ignored():
    flt = LogFilter()
    assert run(flt, ["", "   ", f"{ESC}[0m"]) == []
    assert flt.lines_received == 3


def test_describe_reports_only_active_filters():
    assert LogFilter().describe() == {"level": "very_verbose", "include_config_dump": False}
    described = LogFilter(min_level=3, tags=["race"], search="lap", context=2).describe()
    assert described == {
        "level": "info", "include_config_dump": False,
        "tags": ["race"], "search": "lap", "context": 2,
    }
