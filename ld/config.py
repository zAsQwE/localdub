"""Настройки лаунчера и поиск установленной игры."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

APP_NAME = "localdub"

# Steam AppID What The Dub? — по нему игра и запускается.
STEAM_APPID = 1495860


def _steam_roots() -> list[Path]:
    """Где Steam обычно держит библиотеку игр — на всех системах сразу."""
    roots = [
        Path.home() / ".local/share/Steam",
        Path.home() / ".steam/steam",
        Path.home() / ".steam/root",
        Path.home() / ".var/app/com.valvesoftware.Steam/data/Steam",
        Path("/usr/share/steam"),
        Path.home() / "Library/Application Support/Steam",
    ]
    if os.name == "nt":
        for env in ("ProgramFiles(x86)", "ProgramFiles", "SystemDrive"):
            base = os.environ.get(env)
            if base:
                roots.append(Path(base) / "Steam")
        roots.append(Path("C:/Steam"))
    return roots


_GAME_FOLDER = "WhatTheDub"

DEFAULTS = {
    "game_dir": "",
    "port": 8080,
    "announce_ip": "",       # какой адрес показывать друзьям; пусто — сам
    "lang": "ru",            # язык интерфейса и синтеза речи
    "tts_engine": "auto",    # auto | espeak | piper | none
    "tts_speed": 165,        # слов в минуту для espeak-ng
    "piper_model": "",       # конкретный .onnx, если хочется задать вручную
    "piper_dir": "",         # где ещё искать голосовые модели
    "dotnet_path": "",       # путь к dotnet, если его нет в PATH
    "room_code": "",         # свой код комнаты; пусто — игра выберет сама
    "setup_done": False,     # пройден ли (или пропущен) первый запуск
}

ROOM_CODE_MIN, ROOM_CODE_MAX = 3, 8
_ROOM_CODE_JUNK = re.compile(r"[^0-9A-ZА-Я]")


def _upper(text) -> str:
    return "".join(str(text or "").split()).upper().replace("Ё", "Е")


def clean_room_code(text) -> str:
    """Код комнаты: латиница, кириллица и цифры, заглавными, без Ё."""
    return _ROOM_CODE_JUNK.sub("", _upper(text))[:ROOM_CODE_MAX]


def room_code_problem(text) -> str:
    """Пусто, если код подходит; иначе ключ ошибки для интерфейса."""
    raw = _upper(text)
    if not raw:
        return ""
    if _ROOM_CODE_JUNK.search(raw):
        return "room_code_chars"
    if not ROOM_CODE_MIN <= len(raw) <= ROOM_CODE_MAX:
        return "room_code_length"
    return ""


def config_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData/Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / APP_NAME


def cache_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData/Local")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / APP_NAME


def _library_dirs() -> list[Path]:
    """Каталоги steamapps/common, включая дополнительные библиотеки Steam."""
    found: list[Path] = []
    for root in _steam_roots():
        common = root / "steamapps/common"
        if common.is_dir():
            found.append(common)
        # Игра может лежать на другом диске — Steam перечисляет это в vdf.
        vdf = root / "steamapps/libraryfolders.vdf"
        try:
            text = vdf.read_text("utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if '"path"' not in line:
                continue
            parts = line.split('"')
            if len(parts) >= 4:
                extra = Path(parts[3]) / "steamapps/common"
                if extra.is_dir():
                    found.append(extra)
    return found


def find_game_dir() -> str:
    """Ищет установленную What The Dub."""
    env = os.environ.get("LOCALDUB_GAME")
    if env and is_game_dir(env):
        return env
    for common in _library_dirs():
        candidate = common / _GAME_FOLDER
        if is_game_dir(candidate):
            return str(candidate)
    return ""


def is_game_dir(path) -> bool:
    """Похоже ли это на корень установленной игры."""
    root = Path(path)
    return (root / "WhatTheDub_Data/Managed/Assembly-CSharp.dll").is_file()


def managed_dir(game_dir) -> Path:
    return Path(game_dir) / "WhatTheDub_Data/Managed"


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or (config_dir() / "config.json")
        self.data = dict(DEFAULTS)
        self.load()

    def load(self) -> "Config":
        try:
            stored = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            stored = {}
        for key, value in stored.items():
            if key in DEFAULTS:
                self.data[key] = value
        # Тем, кто пользовался лаунчером до мастера, он уже не нужен.
        if stored and "setup_done" not in stored:
            self.data["setup_done"] = True
        if not self.data["game_dir"]:
            self.data["game_dir"] = find_game_dir()
        return self

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), "utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def update(self, changes: dict) -> "Config":
        for key, value in changes.items():
            if key not in DEFAULTS:
                continue
            default = DEFAULTS[key]
            try:
                if isinstance(default, bool):
                    value = bool(value)
                elif isinstance(default, int):
                    value = int(value)
                elif isinstance(default, str):
                    value = str(value)
            except (TypeError, ValueError):
                continue
            self.data[key] = value
        self._clamp()
        self.save()
        return self

    def _clamp(self) -> None:
        d = self.data
        d["tts_speed"] = max(80, min(400, d["tts_speed"]))
        d["port"] = max(1024, min(65535, d["port"]))
        if d["lang"] not in ("ru", "en"):
            d["lang"] = "ru"
        if d["tts_engine"] not in ("auto", "espeak", "piper", "none"):
            d["tts_engine"] = "auto"
        d["room_code"] = clean_room_code(d["room_code"])
        if len(d["room_code"]) < ROOM_CODE_MIN:
            d["room_code"] = ""

    def __getitem__(self, key: str):
        return self.data[key]

    def __setitem__(self, key: str, value) -> None:
        self.data[key] = value
