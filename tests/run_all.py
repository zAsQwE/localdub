#!/usr/bin/env python3
"""Прогоняет все проверки лаунчера."""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

SUITES = [
    ("QR-кодер", "test_qr.py"),
    ("Синтез речи", "test_tts.py"),
    ("Связь с игрой", "test_wtd.py"),
    ("Сервер лаунчера", "test_server.py"),
    ("Игра с телефона", "test_relay.py"),
]


def main() -> int:
    failed = []
    for title, script in SUITES:
        print(f"\n=== {title} ===")
        if subprocess.run([sys.executable, str(HERE / script)]).returncode:
            failed.append(title)

    print("\n" + "=" * 50)
    if failed:
        print("ПРОВАЛЕНО:", ", ".join(failed))
        return 1
    print("Все проверки пройдены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
