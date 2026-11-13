"""Ресурсы приложения и личный рантайм готовой сборки."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def prepare_runtime() -> None:
    """Используем вложенные программы, не меняя системные PATH и установки."""
    runtime = ROOT / "runtime"
    bins = [runtime / "dotnet", runtime / "piper", runtime / "espeak", runtime / "python"]
    present = [str(p) for p in bins if p.is_dir()]
    if present:
        os.environ["PATH"] = os.pathsep.join(present + [os.environ.get("PATH", "")])
    if (runtime / "dotnet").is_dir():
        os.environ["DOTNET_ROOT"] = str(runtime / "dotnet")
        os.environ["DOTNET_MULTILEVEL_LOOKUP"] = "0"

