"""Всё, что касается настоящей игры: сборка моста, патч, запуск, связь.

Патч меняет `Assembly-CSharp.dll`, но всегда от резервной копии оригинала,
поэтому его можно накатывать сколько угодно раз — в том числе после каждого
обновления игры в Steam.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import config

ROOT = Path(__file__).resolve().parents[1]
WTD_DIR = ROOT / "wtd"
BUILD_DIR = WTD_DIR / "build"
# Готовые файлы лежат в проекте: на машине игрока ничего собирать не нужно.
PREBUILT_DIR = WTD_DIR / "prebuilt"
BRIDGE_DLL = "LocalDubBridge.dll"
PATCHER_DLL_NAME = "wtd-patcher.dll"
BACKUP_NAME = "Assembly-CSharp.dll.localdub-orig"
BUILT_PATCHER = WTD_DIR / "patcher/bin/Release/net8.0" / PATCHER_DLL_NAME

ON, OFF, UNKNOWN = "on", "off", "unknown"

ORIGINAL, RUSSIAN = "orig", "ru"

# Русские фразы: два и больше слов кириллицей подряд. В оригинальной сборке
# таких нет вовсе — отдельные кириллические символы там встречаются только
# в названиях языков. В переводе их десятки.
_PHRASE = re.compile(r"[А-Яа-яЁё]{3,}[ \-,.:!?«»]+[А-Яа-яЁё]{3,}")
_MARKERS = re.compile(r"tolma|tdot|перевод|локализац", re.IGNORECASE)
_PHRASE_THRESHOLD = 5


# Куда .NET ставится, если его нет в PATH.
_DOTNET_HINTS = [
    r"%ProgramFiles%\dotnet\dotnet.exe",
    r"%ProgramFiles(x86)%\dotnet\dotnet.exe",
    r"%LOCALAPPDATA%\Microsoft\dotnet\dotnet.exe",
    "/usr/share/dotnet/dotnet",
    "/usr/lib/dotnet/dotnet",
    "/usr/local/share/dotnet/dotnet",
    "~/.dotnet/dotnet",
]


def dotnet_exe(configured: str = "") -> str | None:
    """Где взять `dotnet`: сначала указанный вручную, потом PATH, потом обычные места."""
    if configured:
        path = Path(os.path.expandvars(os.path.expanduser(configured)))
        if path.is_file():
            return str(path)
        # Могли указать папку, а не сам файл.
        for name in ("dotnet.exe", "dotnet"):
            if (path / name).is_file():
                return str(path / name)

    found = shutil.which("dotnet")
    if found:
        return found

    for hint in _DOTNET_HINTS:
        candidate = Path(os.path.expandvars(os.path.expanduser(hint)))
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return None


def system_info(configured: str = "") -> dict:
    """Что за система и чем на ней работать."""
    names = {"nt": "Windows", "posix": "Linux"}
    label = names.get(os.name, os.name)
    if sys.platform == "darwin":
        label = "macOS"
    return {
        "os": label,
        "platform": sys.platform,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "dotnet": dotnet_exe(configured) or "",
    }


def bridge_source() -> Path | None:
    """Готовая библиотека моста: сначала из проекта, потом из локальной сборки."""
    for candidate in (PREBUILT_DIR / BRIDGE_DLL, BUILD_DIR / BRIDGE_DLL):
        if candidate.is_file():
            return candidate
    return None


def patcher_path() -> Path | None:
    for candidate in (PREBUILT_DIR / PATCHER_DLL_NAME, BUILT_PATCHER):
        if candidate.is_file():
            return candidate
    return None


@dataclass
class Tools:
    """Чего не хватает, чтобы встроить связь с игрой."""
    dotnet: str | None
    have_bridge: bool
    have_patcher: bool
    csharp: str | None

    @property
    def ready(self) -> bool:
        return not self.missing

    @property
    def missing(self) -> list[str]:
        gaps = []
        # dotnet нужен всегда: патчер — программа на .NET.
        if not self.dotnet:
            gaps.append("dotnet")
        if not self.have_patcher and not self.dotnet:
            gaps.append("dotnet-sdk")
        if not self.have_bridge and not self.csharp:
            gaps.append("mono")
        return gaps


def tools(dotnet: str = "") -> Tools:
    return Tools(
        dotnet=dotnet_exe(dotnet),
        have_bridge=bridge_source() is not None,
        have_patcher=patcher_path() is not None,
        csharp=shutil.which("mcs") or shutil.which("csc"),
    )


def sha256(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return ""


def inspect_assembly(managed: Path) -> dict:
    """Что за сборка сейчас лежит у игры."""
    return inspect_file(Path(managed) / "Assembly-CSharp.dll")


def inspect_file(target: Path) -> dict:
    """Оригинал или перевод, с нашим патчем или без.

    Наш патч меняет только цели вызовов, строки остаются на месте, поэтому язык
    определяется одинаково хорошо и до, и после встраивания.
    """
    info = {"exists": False, "size": 0, "sha256": "", "patched": False,
            "flavour": ORIGINAL, "phrases": 0, "markers": 0}
    try:
        blob = target.read_bytes()
    except OSError:
        return info

    text = blob.decode("utf-16-le", "ignore")
    info.update(
        exists=True,
        size=len(blob),
        sha256=sha256(target),
        patched=b"LocalDubBridge" in blob,
        phrases=len(set(m.group() for m in _PHRASE.finditer(text))),
        markers=len(_MARKERS.findall(text)),
    )
    if info["phrases"] >= _PHRASE_THRESHOLD or info["markers"] > 0:
        info["flavour"] = RUSSIAN
    return info


def _meta_path(managed: Path) -> Path:
    return Path(managed) / "Assembly-CSharp.dll.localdub-meta.json"


def read_meta(managed: Path) -> dict:
    try:
        return json.loads(_meta_path(managed).read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _write_meta(managed: Path, data: dict) -> None:
    try:
        _meta_path(managed).write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    except OSError:
        pass


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 600):
    return subprocess.run(
        cmd, cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


# --- сводка -----------------------------------------------------------------
def game_report(game_dir, port: int | None = None, dotnet: str = "") -> dict:
    """Всё, что нужно знать о сборке игры перед запуском."""
    report = {"ok": False, "patch": UNKNOWN, "flavour": ORIGINAL, "phrases": 0,
              "backup": False, "backup_flavour": "", "base_flavour": "",
              "redirects": None, "tts_port": None, "tts_redirects": None,
              "notice": ""}
    if not game_dir or not config.is_game_dir(game_dir):
        return report

    managed = config.managed_dir(game_dir)
    live = inspect_assembly(managed)
    meta = read_meta(managed)
    backup = managed / BACKUP_NAME
    backup_info = inspect_file(backup) if backup.is_file() else None

    report.update(
        ok=True,
        patch=ON if live["patched"] else OFF,
        flavour=live["flavour"],
        phrases=live["phrases"],
        backup=bool(backup_info),
        backup_flavour=backup_info["flavour"] if backup_info else "",
        base_flavour=meta.get("base_flavour", ""),
        redirects=meta.get("redirects"),
        tts_port=meta.get("tts_port"),
        tts_redirects=meta.get("tts_redirects"),
    )

    # Что стоит сказать вслух.
    if live["patched"] and not backup_info:
        report["notice"] = "no_backup"
    elif report["base_flavour"] and report["base_flavour"] != live["flavour"]:
        # Сборку подменили: поставили перевод или Steam вернул оригинал.
        report["notice"] = "flavour_changed"
    elif not live["patched"]:
        report["notice"] = "needs_patch"
    elif report["redirects"] is not None and report["redirects"] < 100:
        report["notice"] = "low_redirects"
    elif port and report["tts_port"] and report["tts_port"] != port:
        # В сборку зашит старый порт — озвучка ушла бы в пустоту.
        report["notice"] = "tts_port_changed"
    return report


# --- состояние -------------------------------------------------------------
def patch_status(game_dir, dotnet: str = "") -> str:
    """`on`, если связь с игрой встроена; `off`, если сборка оригинальная."""
    if not game_dir or not config.is_game_dir(game_dir):
        return UNKNOWN
    patcher = patcher_path()
    if not patcher:
        # Без патчера полагаемся на прямой признак: ссылку на мост в сборке.
        return _reference_present(config.managed_dir(game_dir))
    runner = dotnet_exe(dotnet)
    if not runner:
        return _reference_present(config.managed_dir(game_dir))
    res = _run([runner, str(patcher), str(config.managed_dir(game_dir)), "--status"])
    if "пропатчена" in res.stdout:
        return ON
    if "оригинальная" in res.stdout:
        return OFF
    return UNKNOWN


def _reference_present(managed: Path) -> str:
    """Грубая, но надёжная проверка: имя моста встречается в сборке."""
    target = managed / "Assembly-CSharp.dll"
    try:
        blob = target.read_bytes()
    except OSError:
        return UNKNOWN
    return ON if b"LocalDubBridge" in blob else OFF


# --- сборка ----------------------------------------------------------------
def build_bridge(game_dir) -> tuple[bool, str]:
    script = WTD_DIR / "bridge/build.sh"
    if not script.is_file():
        return False, "нет wtd/bridge/build.sh"
    res = _run([str(script), str(config.managed_dir(game_dir))], cwd=WTD_DIR)
    if res.returncode != 0:
        return False, (res.stderr or res.stdout).strip()[:400]
    return True, "мост собран"


def build_patcher() -> tuple[bool, str]:
    if patcher_path():
        return True, "патчер готов"
    project = WTD_DIR / "patcher"
    if not (project / "Patcher.csproj").is_file():
        return False, "нет wtd/patcher"
    runner = dotnet_exe()
    if not runner:
        return False, "не найден dotnet"
    res = _run([runner, "build", "-c", "Release", "--nologo", "-v", "q"], cwd=project)
    if res.returncode != 0 or not patcher_path():
        return False, (res.stderr or res.stdout).strip()[:400]
    return True, "патчер собран"


# --- встраивание и откат ----------------------------------------------------
def apply_patch(game_dir, tts_port: int | None = None, dotnet: str = "") -> tuple[bool, str]:
    """Кладёт мост к игре и правит сборку.

    `tts_port` — порт нашего сервера. Адрес облачного синтеза Google в игре
    заменяется на него, поэтому порт запекается в сборку: если он потом
    изменится, патч надо накатить заново.
    """
    if not config.is_game_dir(game_dir):
        return False, "игра не найдена"

    kit = tools(dotnet)
    if not kit.ready:
        return False, "не хватает: " + ", ".join(kit.missing)

    # Готовую библиотеку просто копируем; собираем, только если её нет.
    if not kit.have_bridge:
        ok, note = build_bridge(game_dir)
        if not ok:
            return False, f"сборка моста: {note}"

    ok, note = build_patcher()
    if not ok:
        return False, f"сборка патчера: {note}"

    source = bridge_source()
    if not source:
        return False, "не найдена библиотека моста"

    managed = config.managed_dir(game_dir)
    try:
        shutil.copy2(source, managed / BRIDGE_DLL)
    except OSError as e:
        return False, f"не удалось положить мост к игре: {e}"

    ok, note = _refresh_backup(managed)
    if not ok:
        return False, note

    cmd = [kit.dotnet, str(patcher_path()), str(managed)]
    if tts_port:
        cmd += ["--tts-url", f"http://127.0.0.1:{tts_port}"]
    res = _run(cmd)
    if res.returncode != 0:
        return False, (res.stderr or res.stdout).strip()[:400]

    redirects = _redirect_count(res.stdout)
    base = inspect_file(managed / BACKUP_NAME)
    _write_meta(managed, {
        "base_flavour": base["flavour"],
        "base_sha256": base["sha256"],
        "base_size": base["size"],
        "redirects": redirects,
        "tts_port": tts_port,
        "tts_redirects": _tts_count(res.stdout),
        "patched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    if redirects is not None and redirects < 100:
        return False, f"перенаправлено всего {redirects} вызовов — сборка не та, что ожидалась"
    where = "поверх русской версии" if base["flavour"] == RUSSIAN else "поверх оригинала"
    return True, f"встроено {where}, вызовов: {redirects}"


def _refresh_backup(managed: Path) -> tuple[bool, str]:
    """Обновляет копию оригинала, если текущая сборка ещё не тронута нами.

    Это важно: перевод от TDoT тоже подменяет `Assembly-CSharp.dll`. Если брать
    копию только один раз, то после установки перевода патч накатился бы на
    старую основу и перевод бы пропал. Поэтому копию всегда снимаем с того, что
    лежит сейчас, — и патч ложится поверх перевода.
    """
    live = inspect_assembly(managed)
    target = Path(managed) / "Assembly-CSharp.dll"
    backup = Path(managed) / BACKUP_NAME

    if not live["exists"]:
        return False, "сборка игры не найдена"

    if live["patched"]:
        if not backup.is_file():
            return False, ("сборка уже изменена, а копии оригинала нет — "
                           "проверьте целостность файлов в Steam")
        return True, "копия оригинала уже есть"

    try:
        shutil.copy2(target, backup)
    except OSError as e:
        return False, f"не удалось сохранить копию оригинала: {e}"
    return True, "копия оригинала обновлена"


def _redirect_count(text: str):
    match = re.search(r"перенаправлено вызовов:\s*(\d+)", text or "")
    return int(match.group(1)) if match else None


def _tts_count(text: str):
    match = re.search(r"перенаправлено адресов озвучки:\s*(\d+)", text or "")
    return int(match.group(1)) if match else None


def remove_patch(game_dir, dotnet: str = "") -> tuple[bool, str]:
    """Возвращает оригинальную сборку игры."""
    if not config.is_game_dir(game_dir):
        return False, "игра не найдена"
    patcher = patcher_path()
    if not patcher:
        return False, "патчер не найден"
    managed = config.managed_dir(game_dir)
    runner = dotnet_exe(dotnet)
    if not runner:
        return False, "не найден dotnet"
    res = _run([runner, str(patcher), str(managed), "--restore"])
    if res.returncode != 0:
        return False, (res.stderr or res.stdout).strip()[:400]
    (managed / BRIDGE_DLL).unlink(missing_ok=True)
    return True, "оригинальная сборка возвращена"


# --- связь с мостом ----------------------------------------------------------
def write_handshake(managed, port: int, lang: str, token: str,
                    verbose: bool = False, room_code: str = "",
                    join_text: str = "", join_qr: list[str] | None = None) -> Path:
    """Кладёт рядом с мостом файл, из которого он узнаёт, куда стучаться.

    Файл лежит в Managed игры — там же, где сама библиотека. Мост находит его
    по своему расположению, поэтому никаких путей в него зашивать не нужно.
    Код комнаты, адрес и QR-код мост перечитывает на ходу.
    """
    path = Path(managed) / "bridge.json"
    payload = {"port": port, "lang": lang, "token": token, "verbose": bool(verbose),
               "room_code": room_code or "", "join_text": join_text or "",
               "join_qr": list(join_qr or [])}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), "utf-8")
    tmp.replace(path)
    return path


def sync_bridge(managed) -> bool:
    """Обновляет библиотеку моста в игре, если она отстала от нашей.

    Сборку игры это не трогает: она ссылается на мост по имени, поэтому
    новая версия подхватится без переустановки патча.
    """
    source = bridge_source()
    target = Path(managed) / BRIDGE_DLL
    if not source or not target.is_file():
        return False
    try:
        if sha256(source) == sha256(target):
            return False
        shutil.copy2(source, target)
        return True
    except OSError:
        return False


def bridge_log(managed) -> str:
    """Последние строки лога моста — по ним видно, ожил ли он в игре."""
    path = Path(managed) / "bridge.log"
    try:
        lines = path.read_text("utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-40:])


def clear_bridge_log(managed) -> None:
    try:
        (Path(managed) / "bridge.log").unlink()
    except OSError:
        pass


# --- запуск игры --------------------------------------------------------------
GAME_EXE = "WhatTheDub.exe"
WINDOWS = os.name == "nt"


def launch_game(appid: int = config.STEAM_APPID) -> tuple[bool, str]:
    """Просит Steam запустить игру — так подхватывается нужный Proton или Windows-запуск."""
    if WINDOWS:
        try:
            os.startfile(f"steam://rungameid/{appid}")  # noqa: S606
            return True, "через steam://"
        except OSError as e:
            return False, f"не удалось открыть steam:// ({e})"

    steam = shutil.which("steam")
    if steam:
        try:
            subprocess.Popen(
                [steam, "-applaunch", str(appid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True, "через steam"
        except OSError:
            pass
    opener = shutil.which("xdg-open")
    if opener:
        try:
            subprocess.Popen(
                [opener, f"steam://rungameid/{appid}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True, "через steam://"
        except OSError:
            pass
    return False, "не найден Steam"


def game_running() -> bool:
    try:
        if WINDOWS:
            res = _run(["tasklist", "/FI", f"IMAGENAME eq {GAME_EXE}"], timeout=15)
            return GAME_EXE.lower() in (res.stdout or "").lower()
        # pgrep находит и обёртки Proton, поэтому достаточно любого совпадения.
        res = _run(["pgrep", "-f", GAME_EXE], timeout=10)
        return bool(res.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def stop_game() -> bool:
    try:
        if WINDOWS:
            res = _run(["taskkill", "/IM", GAME_EXE, "/F"], timeout=15)
            return res.returncode in (0, 128)
        res = _run(["pkill", "-f", GAME_EXE], timeout=10)
        return res.returncode in (0, 1)
    except (OSError, subprocess.SubprocessError):
        return False
