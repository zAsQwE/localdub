#!/usr/bin/env python3
"""LocalDub — лаунчер для What The Dub без интернета.

Запуск: ./run.sh
"""
from __future__ import annotations

import argparse
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ld.paths import prepare_runtime  # noqa: E402

prepare_runtime()

from ld import config, i18n, net, wtd  # noqa: E402
from ld.server import App, Server  # noqa: E402
from ld.tts import Speech  # noqa: E402


def build_speech(cfg) -> Speech:
    return Speech(
        config.cache_dir() / "tts",
        engine=cfg["tts_engine"],
        speed=cfg["tts_speed"],
        piper_models={cfg["lang"]: cfg["piper_model"]} if cfg["piper_model"] else None,
        piper_dirs=[cfg["piper_dir"]] if cfg["piper_dir"] else None,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="LocalDub")
    parser.add_argument("--port", type=int, help="порт сервера")
    parser.add_argument("--game", help="путь к папке установленной игры")
    parser.add_argument("--browser", action="store_true", help="открыть запасной веб-пульт в браузере")
    parser.add_argument("--headless", action="store_true", help="только сервер, без интерфейса")
    parser.add_argument("--diag", action="store_true", help="подробный лог моста")
    args = parser.parse_args()

    cfg = config.Config()
    if args.game:
        cfg["game_dir"] = args.game
    if args.port:
        cfg["port"] = args.port
    cfg.save()

    app = App(cfg, build_speech(cfg))
    # Порт из настроек уважаем; если занят — берём свободный, но говорим об этом.
    wanted = cfg["port"]
    app.port = wanted if net.port_is_free(wanted) else net.pick_port(wanted)
    if app.port != wanted:
        app.port_note = i18n.t(cfg["lang"], "port_moved", wanted, app.port)
    app.log_sink = lambda text: print(text, flush=True)
    app.verbose = args.diag

    def reload_settings():
        """Настройки изменились — пересобираем синтезатор."""
        app.speech = build_speech(cfg)

    app.on_settings_change = reload_settings

    server = Server(app).start()
    app.server = server

    game_ok = config.is_game_dir(cfg["game_dir"])
    info = wtd.system_info(cfg["dotnet_path"])
    print(f"LocalDub на порту {app.port}")
    print(f"  игра    : {cfg['game_dir'] or 'НЕ НАЙДЕНА'}")
    print(f"  связь   : {wtd.patch_status(cfg['game_dir'], cfg['dotnet_path']) if game_ok else '—'}")
    print(f"  система : {info['os']}, Python {info['python']}, "
          f".NET {info['dotnet'] or 'не найден'}")
    print(f"  голос   : {app.speech.active_name}")
    print(f"  телефоны: {net.join_url(app.port, app.announce_ip())}")
    if app.port_note:
        print(f"  порт    : {app.port_note}")

    # Лаунчер закрыт ключом: по одному адресу телефон получает страницу игрока,
    # а хозяин — пульт. Без окна приложения ссылку надо назвать вслух.
    launcher_url = f"http://127.0.0.1:{app.port}/launcher?key={urllib.parse.quote(app.host_key)}"
    if args.headless:
        print(f"  лаунчер : {launcher_url}")
        try:
            server.thread.join()
        except KeyboardInterrupt:
            pass
        server.stop()
        return 0

    try:
        if args.browser:
            from ld.window import BrowserWindow
            win = BrowserWindow(launcher_url)
            app.folder_picker = win.folder_picker if win.has_picker else None
        else:
            from ld.desktop import create
            win = create(app)
        win.run()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
