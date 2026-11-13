"""Точка входа готовой программы; журнал доступен и без консоли Windows."""
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ["PYTHONUTF8"] = "1"

from ld.config import cache_dir

log_dir = cache_dir()
log_dir.mkdir(parents=True, exist_ok=True)
# Windows pythonw не имеет stdout/stderr; в Linux сохраняем вывод терминала.
if sys.stdout is None or sys.stderr is None:
    sys.stdout = sys.stderr = (log_dir / "localdub.log").open("a", encoding="utf-8", buffering=1)

try:
    from localdub import main
    raise SystemExit(main())
except Exception:
    error = traceback.format_exc()
    (log_dir / "error.log").write_text(error, encoding="utf-8")
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("LocalDub", f"Не удалось запустить LocalDub.\n\n{error.splitlines()[-1]}\n\nЖурнал: {log_dir / 'error.log'}")
        root.destroy()
    except Exception:
        print(error, file=sys.stderr)
    raise SystemExit(1)
