"""Запасной веб-пульт: окно браузера в режиме приложения (`--browser`).

Обычно LocalDub открывает собственное окно на Tkinter (`ld/desktop.py`).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import webbrowser
from pathlib import Path


# Браузеры на движке Chromium умеют режим приложения: окно без адресной строки.
_APP_MODE_BROWSERS = [
    "msedge", "chrome", "google-chrome", "google-chrome-stable",
    "chromium", "chromium-browser", "brave", "brave-browser", "vivaldi",
]

_WINDOWS_BROWSER_PATHS = [
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
]


def _app_mode_browser() -> str | None:
    """Ищет браузер, который умеет открыть страницу отдельным окном."""
    for name in _APP_MODE_BROWSERS:
        found = shutil.which(name)
        if found:
            return found
    if os.name == "nt":
        for path in _WINDOWS_BROWSER_PATHS:
            expanded = os.path.expandvars(path)
            if Path(expanded).is_file():
                return expanded
    return None


class BrowserWindow:
    """Запасной вариант: окно браузера в режиме приложения, иначе обычная вкладка."""

    available = True

    def __init__(self, url: str, title: str = "LocalDub"):
        self.url = url
        self._stop = threading.Event()

    @property
    def has_picker(self) -> bool:
        return os.name == "nt"

    def folder_picker(self):
        """В Windows диалог выбора папки открываем через PowerShell."""
        if not self.has_picker:
            return None
        script = (
            "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$d.Description = 'Папка игры What The Dub'; "
            "if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath }"
        )
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-Command", script],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        path = (res.stdout or "").strip()
        return path or None

    def run(self) -> None:
        browser = _app_mode_browser()
        if browser:
            try:
                subprocess.Popen(
                    [browser, f"--app={self.url}", "--window-size=660,780"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                print(f"Открыто окном браузера: {self.url}")
            except OSError:
                browser = None
        if not browser:
            webbrowser.open(self.url)
            print(f"Открыто в браузере: {self.url}")
        print("Ctrl+C — выход.")
        try:
            while not self._stop.wait(1.0):
                pass
        except KeyboardInterrupt:
            pass

    def quit(self) -> None:
        self._stop.set()
