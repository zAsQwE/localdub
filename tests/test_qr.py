"""Проверка QR-кодера.

Главный тест — round-trip: кодируем строку, рендерим картинку и читаем её
настоящим сканером (zbarimg). Дополнительно сверяем матрицу с qrencode:
расхождение допустимо только в выборе маски, любой валидной маски достаточно.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ld import qr  # noqa: E402

CASES = [
    "http://192.168.1.5:8080",
    "http://10.0.0.42:8080",
    "http://192.168.100.200:31337/join",
    "A",
    "http://localdub.local:8080/?room=ABCD",
    "x" * 60,
    "http://192.168.1.5:8080/join?room=WXYZ&v=1&lang=ru",
    "y" * 120,
    "z" * 200,
    "http://192.168.0.101:8080  Привет",
]


def have(cmd: str) -> bool:
    try:
        subprocess.run([cmd, "--version"], capture_output=True, check=False)
        return True
    except OSError:
        return False


def to_ppm(mods, scale: int = 6, quiet: int = 4) -> bytes:
    """Рендерит матрицу в бинарный PPM — его читает zbarimg без доп. библиотек."""
    size = (len(mods) + quiet * 2) * scale
    rows = []
    for y in range(size):
        my = y // scale - quiet
        row = bytearray()
        for x in range(size):
            mx = x // scale - quiet
            inside = 0 <= my < len(mods) and 0 <= mx < len(mods)
            dark = inside and mods[my][mx]
            row += b"\x00\x00\x00" if dark else b"\xff\xff\xff"
        rows.append(bytes(row))
    return b"P6\n%d %d\n255\n" % (size, size) + b"".join(rows)


def decode(text: str) -> str | None:
    with tempfile.NamedTemporaryFile(suffix=".ppm", delete=False) as fh:
        fh.write(to_ppm(qr.encode(text)))
        path = fh.name
    try:
        out = subprocess.run(
            ["zbarimg", "-q", "--raw", "-Sdisable", "-Sqrcode.enable", path],
            capture_output=True, check=False,
        )
        return out.stdout.decode("utf-8", "replace").rstrip("\n") or None
    finally:
        Path(path).unlink(missing_ok=True)


def qrencode_matrix(text: str):
    out = subprocess.run(
        ["qrencode", "-8", "-l", "M", "-m", "0", "-t", "ASCII"],
        input=text, capture_output=True, text=True, check=True,
    )
    return [
        [1 if line[i * 2:i * 2 + 2] == "##" else 0 for i in range(len(line) // 2)]
        for line in out.stdout.rstrip("\n").split("\n")
    ]


def main() -> int:
    failures = 0

    if have("zbarimg"):
        print("— чтение сканером zbarimg —")
        for text in CASES:
            got = decode(text)
            ok = got == text
            failures += not ok
            mark = "OK      " if ok else "ОШИБКА  "
            print(f"{mark} v{(len(qr.encode(text)) - 17) // 4:<2} {text[:40]!r}")
            if not ok:
                print(f"         прочитано: {got!r}")
    else:
        print("zbarimg не найден — round-trip пропущен")

    if have("qrencode"):
        print("\n— сверка с qrencode (различие в маске допустимо) —")
        for text in CASES:
            mine, ref = qr.encode(text), qrencode_matrix(text)
            if mine == ref:
                verdict = "идентично"
            elif len(mine) == len(ref):
                verdict = "та же версия, другая маска"
            else:
                verdict = "РАЗНЫЙ РАЗМЕР"
                failures += 1
            print(f"  {verdict:28} {text[:40]!r}")

    print("\nпровалов:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
