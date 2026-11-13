"""Минимальный генератор QR-кодов (байтовый режим, уровень M, версии 1-10).

Своя реализация нужна, чтобы лаунчер показывал код для телефонов
без внешних зависимостей — приложение обязано работать оффлайн и «из коробки».
"""
from __future__ import annotations

# (версия -> (всего кодовых слов, EC-слов на блок, [(блоков, данных в блоке)]))
_SPEC = {
    1:  (26,  10, [(1, 16)]),
    2:  (44,  16, [(1, 28)]),
    3:  (70,  26, [(1, 44)]),
    4:  (100, 18, [(2, 32)]),
    5:  (134, 24, [(2, 43)]),
    6:  (172, 16, [(4, 27)]),
    7:  (196, 18, [(4, 31)]),
    8:  (242, 22, [(2, 38), (2, 39)]),
    9:  (292, 22, [(3, 36), (2, 37)]),
    10: (346, 26, [(4, 43), (1, 44)]),
}

_ALIGN = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}

# Остаточные биты после кодовых слов (версии 2-6 требуют 7 бит).
_REMAINDER = {1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7, 7: 0, 8: 0, 9: 0, 10: 0}

_ECL_BITS = 0b00  # уровень M
_MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


# --- арифметика поля Галуа GF(256) ---------------------------------------
_EXP = [0] * 512
_LOG = [0] * 256


def _init_tables() -> None:
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_init_tables()


def _mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(degree: int) -> list[int]:
    poly = [1]
    for i in range(degree):
        nxt = [0] * (len(poly) + 1)
        for j, coef in enumerate(poly):
            nxt[j] ^= _mul(coef, 1)
            nxt[j + 1] ^= _mul(coef, _EXP[i])
        poly = nxt
    return poly


def _rs_encode(data: list[int], ec_len: int) -> list[int]:
    gen = _rs_generator(ec_len)
    rem = [0] * ec_len
    for byte in data:
        factor = byte ^ rem[0]
        rem = rem[1:] + [0]
        for i, g in enumerate(gen[1:]):
            rem[i] ^= _mul(g, factor)
    return rem


# --- сборка потока данных -------------------------------------------------
def _capacity(version: int) -> int:
    _, ec_per, groups = _SPEC[version]
    return sum(n * d for n, d in groups)


def _pick_version(length: int) -> int:
    for version in sorted(_SPEC):
        count_bits = 8 if version < 10 else 16
        needed = (4 + count_bits + length * 8 + 7) // 8
        if needed <= _capacity(version):
            return version
    raise ValueError("строка слишком длинная для версий 1-10")


def _bitstream(data: bytes, version: int) -> list[int]:
    total = _capacity(version) * 8
    bits: list[int] = []

    def put(value: int, width: int) -> None:
        for i in range(width - 1, -1, -1):
            bits.append((value >> i) & 1)

    put(0b0100, 4)  # байтовый режим
    put(len(data), 8 if version < 10 else 16)
    for byte in data:
        put(byte, 8)
    put(0, min(4, total - len(bits)))  # терминатор
    while len(bits) % 8:
        bits.append(0)
    pad = [0xEC, 0x11]
    i = 0
    while len(bits) < total:
        put(pad[i % 2], 8)
        i += 1
    return bits


def _codewords(data: bytes, version: int) -> list[int]:
    bits = _bitstream(data, version)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]

    _, ec_len, groups = _SPEC[version]
    blocks: list[list[int]] = []
    pos = 0
    for count, size in groups:
        for _ in range(count):
            blocks.append(words[pos:pos + size])
            pos += size
    ec_blocks = [_rs_encode(b, ec_len) for b in blocks]

    out: list[int] = []
    for i in range(max(len(b) for b in blocks)):
        for block in blocks:
            if i < len(block):
                out.append(block[i])
    for i in range(ec_len):
        for block in ec_blocks:
            out.append(block[i])
    return out


# --- построение матрицы ---------------------------------------------------
class _Matrix:
    def __init__(self, version: int):
        self.version = version
        self.size = version * 4 + 17
        self.mods = [[0] * self.size for _ in range(self.size)]
        self.reserved = [[False] * self.size for _ in range(self.size)]

    def set(self, r: int, c: int, val: int, reserve: bool = True) -> None:
        self.mods[r][c] = val
        if reserve:
            self.reserved[r][c] = True

    def _finder(self, row: int, col: int) -> None:
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                r, c = row + dr, col + dc
                if not (0 <= r < self.size and 0 <= c < self.size):
                    continue
                inner = 2 <= dr <= 4 and 2 <= dc <= 4
                ring = dr in (0, 6) or dc in (0, 6)
                border = 0 <= dr <= 6 and 0 <= dc <= 6
                self.set(r, c, 1 if (border and (ring or inner)) else 0)

    def _alignment(self) -> None:
        centers = _ALIGN[self.version]
        for r in centers:
            for c in centers:
                if self.reserved[r][c]:
                    continue  # не накладываемся на видоискатели
                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        edge = max(abs(dr), abs(dc))
                        self.set(r + dr, c + dc, 1 if edge != 1 else 0)

    def _timing(self) -> None:
        for i in range(8, self.size - 8):
            val = 1 if i % 2 == 0 else 0
            if not self.reserved[6][i]:
                self.set(6, i, val)
            if not self.reserved[i][6]:
                self.set(i, 6, val)

    def _reserve_format(self) -> None:
        self.set(self.size - 8, 8, 1)  # тёмный модуль
        for i in range(9):
            if not self.reserved[8][i]:
                self.set(8, i, 0)
            if not self.reserved[i][8]:
                self.set(i, 8, 0)
        for i in range(8):
            self.set(8, self.size - 1 - i, 0)
            self.set(self.size - 1 - i, 8, 0)
        if self.version >= 7:
            for i in range(6):
                for j in range(3):
                    self.set(self.size - 11 + j, i, 0)
                    self.set(i, self.size - 11 + j, 0)

    def build_function_patterns(self) -> None:
        self._finder(0, 0)
        self._finder(0, self.size - 7)
        self._finder(self.size - 7, 0)
        self._alignment()
        self._timing()
        self._reserve_format()

    def place_data(self, words: list[int]) -> None:
        bits = []
        for w in words:
            bits.extend((w >> i) & 1 for i in range(7, -1, -1))
        bits.extend([0] * _REMAINDER[self.version])

        idx = 0
        col = self.size - 1
        upward = True
        while col > 0:
            if col == 6:  # вертикальная синхрополоса
                col -= 1
            rows = range(self.size - 1, -1, -1) if upward else range(self.size)
            for row in rows:
                for c in (col, col - 1):
                    if self.reserved[row][c]:
                        continue
                    self.mods[row][c] = bits[idx] if idx < len(bits) else 0
                    idx += 1
            upward = not upward
            col -= 2

    def apply_mask(self, mask: int) -> "_Matrix":
        out = _Matrix(self.version)
        out.reserved = self.reserved
        fn = _MASKS[mask]
        for r in range(self.size):
            for c in range(self.size):
                bit = self.mods[r][c]
                if not self.reserved[r][c] and fn(r, c):
                    bit ^= 1
                out.mods[r][c] = bit
        return out

    def write_format(self, mask: int) -> None:
        value = (_ECL_BITS << 3) | mask
        rem = value
        for _ in range(10):
            rem = (rem << 1) ^ (0x537 if (rem << 1) >> 10 else 0)
        bits = ((value << 10) | rem) ^ 0x5412

        # Позиция 0 несёт старший бит — так их читают сканеры.
        for i in range(15):
            bit = (bits >> (14 - i)) & 1
            # вокруг левого верхнего видоискателя
            if i < 6:
                self.mods[8][i] = bit
            elif i == 6:
                self.mods[8][7] = bit
            elif i == 7:
                self.mods[8][8] = bit
            elif i == 8:
                self.mods[7][8] = bit
            else:
                self.mods[14 - i][8] = bit
            # дублирующая копия: 7 модулей в столбце, 8 в строке
            if i < 7:
                self.mods[self.size - 1 - i][8] = bit
            else:
                self.mods[8][self.size - 15 + i] = bit
        self.mods[self.size - 8][8] = 1

        if self.version >= 7:
            rem = self.version
            for _ in range(12):
                rem = (rem << 1) ^ (0x1F25 if (rem << 1) >> 12 else 0)
            vbits = (self.version << 12) | rem
            for i in range(18):
                bit = (vbits >> i) & 1
                r, c = i // 3, self.size - 11 + i % 3
                self.mods[r][c] = bit
                self.mods[c][r] = bit

    def penalty(self) -> int:
        size, m = self.size, self.mods
        score = 0
        # Правило 1: серии одинаковых модулей длиной 5+
        for line in list(m) + [list(col) for col in zip(*m)]:
            run, prev = 1, line[0]
            for val in line[1:]:
                if val == prev:
                    run += 1
                else:
                    if run >= 5:
                        score += 3 + (run - 5)
                    run, prev = 1, val
            if run >= 5:
                score += 3 + (run - 5)
        # Правило 2: блоки 2x2
        for r in range(size - 1):
            for c in range(size - 1):
                if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                    score += 3
        # Правило 3: шаблон, похожий на видоискатель
        pat_a = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
        pat_b = list(reversed(pat_a))
        for line in list(m) + [list(col) for col in zip(*m)]:
            for i in range(size - 10):
                window = list(line[i:i + 11])
                if window == pat_a or window == pat_b:
                    score += 40
        # Правило 4: перекос баланса чёрного и белого
        dark = sum(sum(row) for row in m)
        ratio = dark * 100 // (size * size)
        score += 10 * (abs(ratio - 50) // 5)
        return score


def encode(text: str) -> list[list[int]]:
    """Возвращает матрицу модулей (1 — тёмный)."""
    data = text.encode("utf-8")
    version = _pick_version(len(data))
    words = _codewords(data, version)

    base = _Matrix(version)
    base.build_function_patterns()
    base.place_data(words)

    best, best_score = None, None
    for mask in range(8):
        candidate = base.apply_mask(mask)
        candidate.write_format(mask)
        score = candidate.penalty()
        if best_score is None or score < best_score:
            best, best_score = candidate, score
    return best.mods


def to_svg(text: str, quiet: int = 4, dark: str = "#111", light: str = "none") -> str:
    """Отдаёт QR как SVG — вставляется прямо в страницу, без файлов и картинок."""
    mods = encode(text)
    size = len(mods) + quiet * 2
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'shape-rendering="crispEdges" role="img" aria-label="QR">'
    ]
    if light != "none":
        parts.append(f'<rect width="{size}" height="{size}" fill="{light}"/>')
    for r, row in enumerate(mods):
        c = 0
        while c < len(row):
            if row[c]:
                run = 1
                while c + run < len(row) and row[c + run]:
                    run += 1
                parts.append(
                    f'<rect x="{c + quiet}" y="{r + quiet}" width="{run}" height="1" fill="{dark}"/>'
                )
                c += run
            else:
                c += 1
    parts.append("</svg>")
    return "".join(parts)


def to_text(text: str) -> str:
    """ASCII-вид — для отладки и сравнения с эталоном."""
    return "\n".join("".join("##" if v else "  " for v in row) for row in encode(text))
