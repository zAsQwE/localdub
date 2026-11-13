"""Проверяет распакованную готовую программу, не трогая установленную игру."""
import argparse
import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("root", type=Path)
parser.add_argument("--gui", action="store_true")
args = parser.parse_args()
root = args.root.resolve()
sys.path.insert(0, str(root))
from ld.paths import prepare_runtime
prepare_runtime()
from ld import config, wtd
from ld.server import App, Server
from ld.tts import Speech

assert Path(sys.executable).resolve().is_relative_to(root), "Проверка должна запускаться комплектным Python"
assert wtd.tools().ready, wtd.tools().missing
runtimes = subprocess.run([wtd.dotnet_exe(), "--list-runtimes"], capture_output=True, text=True, check=True).stdout
assert "Microsoft.NETCore.App 8." in runtimes, runtimes
with tempfile.TemporaryDirectory(prefix="localdub-bundle-check-") as temp:
    temp = Path(temp)
    speech = Speech(temp / "tts", engine="piper")
    assert speech.active_name == "piper", speech.engine_details("ru")
    voiced = speech.speak("Проверка готовой программы", "ru", "male")
    assert voiced and voiced.path.is_file() and voiced.duration > 0, "Комплектный Piper не синтезирует речь"
    cfg = config.Config(temp / "config.json")
    # Мастер первого запуска проверяет tests/test_desktop.py; здесь нужен главный экран.
    cfg.update({"game_dir": "", "port": 8080, "setup_done": True})
    app = App(cfg, speech)
    app.port = 0
    server = Server(app, host="127.0.0.1").start()
    app.port, app.server = server.port, server
    try:
        base = f"http://127.0.0.1:{server.port}"
        with urllib.request.urlopen(base) as response:
            assert b"LocalDub" in response.read()
        with urllib.request.urlopen(base+"/static/css/phone.css") as response:
            assert response.status == 200
        if args.gui:
            from ld.desktop import DesktopWindow
            window = DesktopWindow(app)
            window.root.update()
            assert window.root.title() == "LocalDub"
            assert window.patch_button.text == "Установить патч"
            window.close()
            window.worker.shutdown(wait=True)
    finally:
        server.stop()
print(json.dumps({"python": sys.version.split()[0], "tts": "piper", "voice_seconds": voiced.duration, "dotnet": runtimes.strip(), "gui": args.gui}, ensure_ascii=False))
