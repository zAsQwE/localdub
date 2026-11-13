"""Синтез речи для реплик игроков. Всё локально, без обращений в сеть."""
from __future__ import annotations

import hashlib
import json
import re
import os
import shutil
import site
import subprocess
import sys
import sysconfig
import threading
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path

from .paths import ROOT

MAX_CHARS = 220


def run_process(*args, **kwargs):
    # pythonw не должен открывать консоль при каждом синтезе или опросе.
    kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    return subprocess.run(*args, **kwargs)

# Установщик espeak-ng в Windows не прописывает себя в PATH, а установщик
# piper кладёт бинарь рядом с собой. Поэтому кроме PATH смотрим и по месту.
_WINDOWS_HINTS = {
    "espeak-ng": [
        r"C:\Program Files\eSpeak NG\espeak-ng.exe",
        r"C:\Program Files (x86)\eSpeak NG\espeak-ng.exe",
    ],
    "espeak": [
        r"C:\Program Files\eSpeak\command_line\espeak.exe",
        r"C:\Program Files (x86)\eSpeak\command_line\espeak.exe",
    ],
    "piper": [
        r"C:\Program Files\piper\piper.exe",
        r"%LOCALAPPDATA%\Programs\piper\piper.exe",
        r"%LOCALAPPDATA%\piper\piper.exe",
    ],
}


def find_binary(name: str, windows: bool | None = None,
                hints: dict | None = None) -> str | None:
    """Ищет программу в PATH, а в Windows — ещё и по обычным местам установки.

    Параметры нужны проверкам: подменять `os.name` целиком нельзя, от этого
    ломается работа с путями.
    """
    found = shutil.which(name)
    if found:
        return found
    if not (os.name == "nt" if windows is None else windows):
        return None
    for hint in (hints or _WINDOWS_HINTS).get(name, []):
        path = Path(os.path.expandvars(hint))
        if path.is_file():
            return str(path)
    return None
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_WS_RE = re.compile(r"\s+")

# Голоса espeak-ng: базовый язык + вариант тембра.
_ESPEAK_VOICES = {
    ("ru", "male"): "ru+m3",
    ("ru", "female"): "ru+f4",
    ("en", "male"): "en-us+m3",
    ("en", "female"): "en-us+f4",
}


def clean(text: str) -> str:
    """Приводит пользовательский ввод к виду, безопасному для синтезатора."""
    text = _CONTROL_RE.sub(" ", text or "")
    text = _WS_RE.sub(" ", text).strip()
    return text[:MAX_CHARS]


@dataclass
class Voiced:
    """Результат синтеза: файл и его длительность."""
    key: str
    path: Path
    duration: float
    engine: str


class Engine:
    name = "none"
    available = False

    def synth(self, text: str, out: Path, lang: str, gender: str) -> bool:
        return False


class EspeakEngine(Engine):
    name = "espeak"

    def __init__(self, speed: int = 165):
        self.speed = speed
        self.binary = find_binary("espeak-ng") or find_binary("espeak")
        self.available = bool(self.binary)

    def synth(self, text: str, out: Path, lang: str, gender: str) -> bool:
        voice = _ESPEAK_VOICES.get((lang, gender)) or _ESPEAK_VOICES[("en", "male")]
        cmd = [self.binary, "-v", voice, "-s", str(self.speed), "-w", str(out)]
        try:
            # Текст идёт через stdin: так он никогда не будет принят за флаг.
            res = run_process(
                cmd, input=text.encode("utf-8"),
                capture_output=True, timeout=20, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return res.returncode == 0 and out.is_file() and out.stat().st_size > 44


class PiperEngine(Engine):
    """Нейросетевой синтез. Ставится как `pip install piper-tts` плюс голосовая модель."""

    name = "piper"

    # Голоса по умолчанию — их же и скачиваем, если моделей не нашлось.
    DEFAULT_VOICES = {"ru": "ru_RU-dmitri-medium", "en": "en_US-lessac-medium"}

    @staticmethod
    def voice_dirs() -> list[Path]:
        """Все места, где обычно оказываются модели .onnx."""
        home = Path.home()
        dirs = [
            ROOT / "runtime/voices",
            home / ".local/share/piper-voices",
            home / ".local/share/piper",
            home / ".local/share/piper_tts",
            home / ".cache/piper",
            home / "piper",
            home / "piper-voices",
            Path("/usr/share/piper-voices"),
            Path("/usr/local/share/piper-voices"),
            Path("/opt/piper"),
        ]
        for env in ("APPDATA", "LOCALAPPDATA", "ProgramData"):
            base = os.environ.get(env)
            if base:
                dirs += [Path(base) / "piper", Path(base) / "piper-voices"]
        return dirs

    @staticmethod
    def script_dirs() -> list[Path]:
        """Куда pip кладёт консольные команды.

        При `pip install --user` команда `piper` оказывается в каталоге, которого
        обычно нет в PATH, — поэтому мало искать только по PATH.
        """
        dirs: list[Path] = []
        bin_name = "Scripts" if os.name == "nt" else "bin"

        for getter in (lambda: sysconfig.get_path("scripts"),
                       lambda: sysconfig.get_path(
                           "scripts", sysconfig.get_preferred_scheme("user"))):
            try:
                path = getter()
            except (KeyError, AttributeError, TypeError, ValueError):
                continue
            if path:
                dirs.append(Path(path))

        try:
            dirs.append(Path(site.getuserbase()) / bin_name)
        except (AttributeError, OSError):
            pass

        dirs.append(Path(sys.prefix) / bin_name)
        dirs.append(Path.home() / ".local/bin")
        if os.name == "nt":
            appdata = os.environ.get("APPDATA")
            if appdata:
                # %APPDATA%\Python\Python312\Scripts и соседние версии.
                root = Path(appdata) / "Python"
                if root.is_dir():
                    try:
                        dirs += [child / "Scripts" for child in root.iterdir()]
                    except OSError:
                        pass

        unique, seen = [], set()
        for folder in dirs:
            key = str(folder)
            if key not in seen:
                seen.add(key)
                unique.append(folder)
        return unique

    @classmethod
    def find_script(cls, name: str) -> str | None:
        """Ищет команду в PATH, а затем в каталогах pip."""
        found = find_binary(name)
        if found:
            return found
        suffixes = (".exe", ".cmd", ".bat", "") if os.name == "nt" else ("",)
        for folder in cls.script_dirs():
            for suffix in suffixes:
                candidate = folder / (name + suffix)
                try:
                    if candidate.is_file():
                        return str(candidate)
                except OSError:
                    continue
        return None

    @staticmethod
    def python_candidates() -> list[list[str]]:
        """Интерпретаторы, в которых мог оказаться пакет.

        pip мог поставить piper не в тот Python, из которого запущено приложение,
        поэтому перебираем все, что видно в системе.
        """
        seen: list[list[str]] = [[sys.executable]]
        known = {sys.executable}
        for name in ("python3", "python"):
            found = shutil.which(name)
            if found and found not in known:
                known.add(found)
                seen.append([found])
        if os.name == "nt":
            launcher = shutil.which("py")
            if launcher:
                seen += [[launcher, "-3"], [launcher]]
        return seen

    # Разбор занимает несколько запусков процессов, поэтому результат помним.
    _probe_cache: tuple | None = None

    @classmethod
    def _probe(cls, refresh: bool = False) -> tuple:
        if cls._probe_cache is not None and not refresh:
            return cls._probe_cache
        binary = cls.find_script("piper")
        python = None
        for candidate in cls.python_candidates():
            try:
                res = run_process(
                    candidate + ["-c", "import piper"],
                    capture_output=True, timeout=20, check=False,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if res.returncode == 0:
                python = candidate
                break
        cls._probe_cache = (binary, python)
        return cls._probe_cache

    def __init__(self, models: dict[str, str] | None = None, extra_dirs=None,
                 refresh: bool = False):
        self.models = {k: v for k, v in (models or {}).items() if v}
        self.extra_dirs = [Path(d) for d in (extra_dirs or []) if d]
        self.binary, self.python = self._probe(refresh)
        # Установлен и пригоден — разные вещи: модель может быть ещё не скачана.
        self.installed = bool(self.binary or self.python)
        self._voices: list[Path] | None = None
        self._extra_files: list[Path] = []

    @property
    def module(self) -> bool:
        """Виден ли пакет piper хоть какому-нибудь интерпретатору."""
        return self.python is not None

    def run_prefix(self) -> list[str]:
        """Чем запускать синтез: командой или модулем."""
        if self.binary:
            return [self.binary]
        return list(self.python or [sys.executable]) + ["-m", "piper"]

    # --- поиск голосовых моделей -----------------------------------------
    def _package_dirs(self) -> list[Path]:
        """Где лежит сам пакет piper — модели часто складывают рядом с ним."""
        if not self.python:
            return []
        try:
            res = run_process(
                self.python + ["-c",
                               "import piper, os; print(os.path.dirname(piper.__file__))"],
                capture_output=True, text=True, timeout=15, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        root = (res.stdout or "").strip()
        if not root:
            return []
        base = Path(root)
        return [base, base / "voices", base / "data", base.parent]

    def voices(self, refresh: bool = False) -> list[Path]:
        if self._voices is not None and not refresh:
            return self._voices
        found: list[Path] = []
        seen: set[str] = set()
        roots = self.extra_dirs + self.voice_dirs() + self._package_dirs()
        if self.binary:
            roots.append(Path(self.binary).resolve().parent)
        # Папка загрузок — туда модель чаще всего и кладут руками.
        downloads = Path.home() / "Downloads"
        if downloads.is_dir():
            try:
                found_here = sorted(downloads.glob("*.onnx"))
            except OSError:
                found_here = []
            for path in found_here:
                if path.is_file() and self.is_voice(path):
                    self._extra_files.append(path)
        for root in roots:
            try:
                if not root.is_dir():
                    continue
                for path in sorted(root.rglob("*.onnx")):
                    key = str(path.resolve())
                    if key not in seen and self.is_voice(path):
                        seen.add(key)
                        found.append(path)
            except OSError:
                continue
        for path in self._extra_files:
            key = str(path.resolve())
            if key not in seen and path.is_file() and self.is_voice(path):
                seen.add(key)
                found.append(path)
        self._voices = found
        return found

    # --- скачивание голоса --------------------------------------------------
    def download(self, lang: str, into: Path) -> tuple[bool, str]:
        """Скачивает голос из официального хранилища, в том числе для Piper CLI."""
        if not self.installed:
            return False, "piper не установлен"
        voice = self.DEFAULT_VOICES.get(lang) or self.DEFAULT_VOICES["en"]
        into = Path(into)
        into.mkdir(parents=True, exist_ok=True)
        # Куда скачали — там и ищем, независимо от того, кто нас позвал.
        if into not in self.extra_dirs:
            self.extra_dirs.append(into)

        locale, speaker, quality = voice.split("-")
        language = locale.split("_")[0]
        base = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{language}/{locale}/{speaker}/{quality}/{voice}"
        try:
            for suffix in (".onnx.json", ".onnx"):
                target = into / (voice+suffix)
                if target.is_file() and target.stat().st_size:
                    continue
                part = target.with_suffix(target.suffix+".part")
                try:
                    request = urllib.request.Request(base+suffix, headers={"User-Agent": "LocalDub"})
                    with urllib.request.urlopen(request, timeout=60) as source, part.open("wb") as output:
                        length = source.headers.get("Content-Length")
                        shutil.copyfileobj(source, output, 1 << 20)
                    if not part.stat().st_size:
                        raise ValueError("скачан пустой файл")
                    if length is not None and part.stat().st_size != int(length):
                        raise ValueError("голос скачался не полностью, попробуйте ещё раз")
                    if suffix.endswith(".json"):
                        json.loads(part.read_text("utf-8"))
                    part.replace(target)
                finally:
                    part.unlink(missing_ok=True)
            self.voices(refresh=True)
            return True, voice
        except (OSError, ValueError) as error:
            return False, str(error)[:300]

    @staticmethod
    def is_voice(path: Path) -> bool:
        """Настоящий голос piper всегда идёт с файлом настроек рядом.

        Без этой проверки за голоса принимаются посторонние `.onnx` —
        например тестовые модели из других пакетов в том же каталоге.
        """
        try:
            return path.with_suffix(path.suffix + ".json").is_file()
        except OSError:
            return False

    @staticmethod
    def voice_language(path: Path) -> str:
        """`ru_RU-dmitri-medium.onnx` -> `ru`."""
        return path.name.split("_", 1)[0].split("-", 1)[0].lower()

    def model_for(self, lang: str) -> str | None:
        """Явно заданная модель важнее найденной автоматически."""
        chosen = self.models.get(lang) or self.models.get("default")
        if chosen and Path(chosen).is_file():
            return chosen  # указали руками — верим на слово
        for path in self.voices():
            if self.voice_language(path) == lang:
                return str(path)
        voices = self.voices()
        return str(voices[0]) if voices else None

    @property
    def available(self) -> bool:
        return self.installed and bool(self.model_for("en") or self.voices())

    @available.setter
    def available(self, _value) -> None:  # базовый класс держит это полем
        pass

    def status(self, lang: str = "en") -> str:
        if not self.installed:
            return "not_installed"
        if not self.model_for(lang):
            return "no_voice"
        return "ok"

    # --- синтез ------------------------------------------------------------
    def synth(self, text: str, out: Path, lang: str, gender: str) -> bool:
        model = self.model_for(lang)
        if not model:
            return False
        # Короткие ключи -m/-f понимают и старые, и новые версии piper.
        cmd = self.run_prefix() + ["-m", model, "-f", str(out)]
        try:
            res = run_process(
                cmd, input=text.encode("utf-8"),
                capture_output=True, timeout=90, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return res.returncode == 0 and out.is_file() and out.stat().st_size > 44


def wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as fh:
            rate = fh.getframerate()
            return fh.getnframes() / rate if rate else 0.0
    except (OSError, wave.Error):
        return 0.0


class Speech:
    """Фасад над движками: выбирает доступный, синтезирует и кэширует."""

    def __init__(self, cache: Path, engine: str = "auto", speed: int = 165,
                 piper_models: dict[str, str] | None = None,
                 piper_dirs=None):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.speed = speed
        # Скачанные голоса кладём к себе — и сразу же там их и ищем.
        self.voice_store = self.cache.parent / "piper-voices"
        self.espeak = EspeakEngine(speed)
        self.piper = PiperEngine(
            piper_models, extra_dirs=list(piper_dirs or []) + [self.voice_store])
        self.requested = engine
        self._lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    # --- выбор движка ---------------------------------------------------
    @property
    def engines(self) -> dict[str, bool]:
        return {
            "espeak": self.espeak.available,
            "piper": self.piper.available,
            "none": True,
        }

    def engine_details(self, lang: str = "en") -> dict:
        """Подробности для настроек: чего не хватает и что нашлось."""
        voices = self.piper.voices()
        return {
            "espeak": {
                "available": self.espeak.available,
                "status": "ok" if self.espeak.available else "not_installed",
            },
            "piper": {
                "available": self.piper.available,
                "installed": self.piper.installed,
                "status": self.piper.status(lang),
                "voices": [
                    {"name": v.name, "lang": PiperEngine.voice_language(v), "path": str(v)}
                    for v in voices[:40]
                ],
                "using": self.piper.model_for(lang) or "",
            },
            "none": {"available": True, "status": "ok"},
        }

    def active(self) -> Engine | None:
        if self.requested == "none":
            return None
        if self.requested == "piper" and self.piper.available:
            return self.piper
        if self.requested == "espeak" and self.espeak.available:
            return self.espeak
        if self.requested == "auto":
            if self.piper.available:
                return self.piper
            if self.espeak.available:
                return self.espeak
        # Запрошенный движок недоступен — откатываемся на любой рабочий.
        if self.espeak.available:
            return self.espeak
        if self.piper.available:
            return self.piper
        return None

    @property
    def active_name(self) -> str:
        engine = self.active()
        return engine.name if engine else "none"

    # --- синтез ----------------------------------------------------------
    def key_for(self, text: str, lang: str, gender: str) -> str:
        raw = f"{self.active_name}|{self.speed}|{lang}|{gender}|{text}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def path_for(self, key: str) -> Path:
        return self.cache / f"{key}.wav"

    def _lock_for(self, key: str) -> threading.Lock:
        with self._lock:
            return self._locks.setdefault(key, threading.Lock())

    def speak(self, text: str, lang: str = "ru", gender: str = "male") -> Voiced | None:
        """Синтезирует (или достаёт из кэша) реплику. None — если озвучки нет."""
        text = clean(text)
        engine = self.active()
        if not text or engine is None:
            return None
        key = self.key_for(text, lang, gender)
        out = self.path_for(key)

        with self._lock_for(key):
            if not out.is_file():
                tmp = out.with_suffix(".part")
                if not engine.synth(text, tmp, lang, gender):
                    tmp.unlink(missing_ok=True)
                    return None
                tmp.replace(out)
        return Voiced(key, out, wav_duration(out), engine.name)

    def download_voice(self, lang: str) -> tuple[bool, str]:
        """Тянет голос для языка, чтобы не искать модель руками."""
        ok, note = self.piper.download(lang, self.voice_store)
        self.piper.voices(refresh=True)
        return ok, note

    def prune(self, keep: set[str] | None = None) -> int:
        """Чистит кэш от файлов прошлых партий."""
        removed = 0
        for path in self.cache.glob("*.wav"):
            if keep and path.stem in keep:
                continue
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        return removed
