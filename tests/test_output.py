"""Tests for trimming validate / build output.

Samples follow ESPHome 2026.10: ``esphome config`` prints log lines as
``LEVEL message``, the resolved YAML on success, and a ``Failed config`` block
(already narrowed by ESPHome to the failing parts) on error; Device Builder
prints ESC as the literal text ``\\033``. The build sample is shaped like a
real ESP-IDF remote build streamed through Device Builder.
"""
from esphome_mcp_client.output import (
    FAILED_BUILD_TAIL,
    trim_build_output,
    trim_validate_output,
)

VALIDATE_OK = [
    r"\033[32mINFO ESPHome 2026.10.0\033[0m",
    r"\033[32mINFO Reading configuration /config/esphome/decklights.yaml...\033[0m",
    r"\033[33mWARNING WiFi AP is configured but neither captive_portal nor web_server is enabled.\033[0m",
    "esphome:",
    "  name: decklights",
    "  min_version: 2026.10.0",
    "wifi:",
    "  networks:",
    "    - ssid: !secret 'wifi_ssid'",
    r"      password: \033[5m!secret 'wifi_password'\033[6m",
    "",
    r"\033[32mINFO Configuration is valid!\033[0m",
]

VALIDATE_FAILED = [
    r"\033[32mINFO ESPHome 2026.10.0\033[0m",
    r"\033[32mINFO Reading configuration /config/esphome/race.yaml...\033[0m",
    r"\033[1;31mFailed config\033[0m",
    "",
    r"\033[1;31msensor.template:\033[0m [source /config/esphome/race.yaml:12]",
    "  platform: template",
    "  name: Lap time",
    r"  \033[1;31m[lamda] is an invalid option for [sensor.template]. Did you mean [lambda]?\033[0m",
    "  lamda: return 1;",
    "",
    r"\033[1;31m[lamda] is an invalid option for [sensor.template]. Did you mean [lambda]?\033[0m",
]


def test_validate_success_drops_config_dump():
    trimmed = trim_validate_output(VALIDATE_OK)
    assert trimmed.lines == [
        "INFO ESPHome 2026.10.0",
        "INFO Reading configuration /config/esphome/decklights.yaml...",
        "WARNING WiFi AP is configured but neither captive_portal nor web_server is enabled.",
        "INFO Configuration is valid!",
    ]
    assert trimmed.omitted == 7  # the YAML lines; blanks aren't counted


def test_validate_failure_keeps_whole_failed_config_section():
    trimmed = trim_validate_output(VALIDATE_FAILED)
    assert trimmed.lines == [
        "INFO ESPHome 2026.10.0",
        "INFO Reading configuration /config/esphome/race.yaml...",
        "Failed config",
        "sensor.template: [source /config/esphome/race.yaml:12]",
        "  platform: template",
        "  name: Lap time",
        "  [lamda] is an invalid option for [sensor.template]. Did you mean [lambda]?",
        "  lamda: return 1;",
        "[lamda] is an invalid option for [sensor.template]. Did you mean [lambda]?",
    ]
    assert trimmed.omitted == 0


def test_validate_keeps_details_after_an_error_line():
    lines = [
        "INFO Reading configuration /config/esphome/race.yaml...",
        "ERROR Error while reading config: Invalid YAML syntax:",
        "",
        "mapping values are not allowed here",
        '  in "/config/esphome/race.yaml", line 4, column 9',
    ]
    trimmed = trim_validate_output(lines)
    assert trimmed.lines == [line for line in lines if line]


def test_validate_keeps_traceback_after_unexpected_exception():
    lines = [
        "ERROR Unexpected exception while reading configuration:",
        "Traceback (most recent call last):",
        '  File "/esphome/esphome/config.py", line 1000, in validate_config',
        "KeyError: 'platform'",
    ]
    assert trim_validate_output(lines).lines == lines


def test_validate_details_stop_at_next_log_line():
    lines = [
        "ERROR Something failed:",
        "  detail",
        "INFO Recovered",
        "  not part of an error",
    ]
    assert trim_validate_output(lines).lines == ["ERROR Something failed:", "  detail", "INFO Recovered"]


BUILD_OK = [
    "*** building configuration bundle for remote build ***",
    "INFO ESPHome 2026.10.0",
    "WARNING Bundle contains secrets (e.g. Wi-Fi passwords). Do not share it with untrusted parties.",
    "*** bundle ready (13 KiB); sending to build server ***",
    r"\033[32mINFO Generating C++ source...\033[0m",
    r"\033[32mINFO Compiling app... Build path: /config/.esphome/build/decklights\033[0m",
    "-- Minimal build - OFF",
    "-- ccache will be used for faster recompilation",
    "-- Building ESP-IDF components for target esp32s2",
    "-- Configuring done (3.1s)",
    "\r\n[0/2] Re-checking globbed directories...\\033[K",
    "\r[1/122] Building C object esp-idf/log/CMakeFiles/__idf_log.dir/src/util.c.obj\\033[K\r[2/122] Building C object x.obj\\033[K",
    "┏━━━━┳━━━┓",
    "┃ Memory Type/Section ┃ Used [bytes] ┃",
    "│ Flash Code │ 586994 │",
    "└────┴───┘",
    "Total image size: 777490 bytes (.bin may be padded larger)",
    "decklights.bin binary size 0xbdd80 bytes. Smallest app partition is 0x1c0000 bytes. 0x102280 bytes (58%) free.",
    "esptool.py v4.12.0",
    "Creating esp32s2 image...",
    "Merged 2 ELF sections",
    "Successfully created esp32s2 image.",
    "Generated /config/.esphome/build/decklights/build/bootloader/bootloader.bin",
    "RAM:   [=======   ]  70.3% (used 120992 bytes from 172032 bytes)",
    "Flash: [====      ]  42.4% (used 777490 bytes from 1835008 bytes)",
    r"\033[32mINFO Successfully compiled program.\033[0m",
]


def test_build_success_keeps_results_and_drops_progress():
    trimmed = trim_build_output(BUILD_OK)
    assert trimmed.lines == [
        "*** building configuration bundle for remote build ***",
        "INFO ESPHome 2026.10.0",
        "WARNING Bundle contains secrets (e.g. Wi-Fi passwords). Do not share it with untrusted parties.",
        "*** bundle ready (13 KiB); sending to build server ***",
        "INFO Generating C++ source...",
        "INFO Compiling app... Build path: /config/.esphome/build/decklights",
        "Total image size: 777490 bytes (.bin may be padded larger)",
        "decklights.bin binary size 0xbdd80 bytes. Smallest app partition is 0x1c0000 bytes. 0x102280 bytes (58%) free.",
        "RAM:   [=======   ]  70.3% (used 120992 bytes from 172032 bytes)",
        "Flash: [====      ]  42.4% (used 777490 bytes from 1835008 bytes)",
        "INFO Successfully compiled program.",
    ]
    assert trimmed.omitted == len(BUILD_OK) - len(trimmed.lines)


def test_log_lines_are_never_treated_as_noise():
    # "INFO Generating ..." starts like a noise pattern but is esphome's own log.
    assert trim_build_output(["INFO Generating C++ source..."]).lines == ["INFO Generating C++ source..."]


def test_compiler_diagnostics_keep_their_excerpt():
    noise = [f"[{i}/300] Building CXX object x{i}.o" for i in range(100)]
    diagnostic = [
        "src/race.cpp:12:5: error: 'lap_count' was not declared in this scope",
        "   12 |     lap_count++;",
        "      |     ^~~~~~~~~",
        "src/race.cpp:20:1: warning: no return statement in function returning non-void",
    ]
    trimmed = trim_build_output(noise[:50] + diagnostic + noise[50:])
    assert trimmed.lines == diagnostic


def test_failed_build_keeps_the_tail_unfiltered():
    lines = [f"[{i}/300] Building CXX object x{i}.o" for i in range(100)]
    lines += ["FAILED: esp-idf/main/CMakeFiles/__idf_main.dir/race.cpp.obj", "ninja: build stopped"]
    trimmed = trim_build_output(lines, failed=True)
    assert len(trimmed.lines) == FAILED_BUILD_TAIL
    assert trimmed.lines[-2:] == lines[-2:]
    # Progress lines inside the tail are kept too: the tail is unfiltered.
    assert trimmed.lines[0] == lines[-FAILED_BUILD_TAIL]


def test_unknown_lines_are_kept():
    lines = ["Something nobody anticipated", "-- cmake chatter"]
    assert trim_build_output(lines).lines == ["Something nobody anticipated"]


def test_platformio_build_noise():
    lines = [
        "Processing chargedock (board: d1_mini; framework: arduino; platform: platformio/espressif8266@4.2.1)",
        "Verbose mode can be enabled via `-v, --verbose` option",
        "Dependency Graph",
        "|-- ESPAsyncTCP-esphome @ 2.0.0",
        "Compiling .pio/build/chargedock/src/main.cpp.o",
        "Archiving .pio/build/chargedock/lib1a0/libESPAsyncTCP.a",
        "Linking .pio/build/chargedock/firmware.elf",
        "RAM:   [====      ]  41.2% (used 33772 bytes from 81920 bytes)",
    ]
    trimmed = trim_build_output(lines)
    assert trimmed.lines == [
        "Processing chargedock (board: d1_mini; framework: arduino; platform: platformio/espressif8266@4.2.1)",
        "Dependency Graph",
        "RAM:   [====      ]  41.2% (used 33772 bytes from 81920 bytes)",
    ]


def test_a_streamed_chunk_with_embedded_lines_is_split():
    chunk = "\r\n[5/11] cd /build && python check_sizes.py\\033[K\r\nRAM:   [==   ]  20.0%\r\n"
    assert trim_build_output([chunk]).lines == ["RAM:   [==   ]  20.0%"]


def test_clean_output_keeps_every_line_but_strips_ansi():
    from esphome_mcp_client.output import clean_output

    cleaned = clean_output(VALIDATE_OK)
    assert cleaned.omitted == 0
    assert len(cleaned.lines) == len([line for line in VALIDATE_OK if line])
    assert not any("\\033" in line for line in cleaned.lines)
