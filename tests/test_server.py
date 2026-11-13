"""Проверка сервера лаунчера: страница, состояние, настройки, озвучка, границы.

Запуск игры здесь намеренно не проверяется: `game_dir` указывает в пустую
папку, поэтому /api/start обязан отказать, а не поднимать Steam.
"""
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ld import net  # noqa: E402
from ld.config import Config  # noqa: E402
from ld.server import App, Server  # noqa: E402
from ld.tts import Speech  # noqa: E402

FAILS = []
BASE = ""


def check(label, cond, extra=""):
    print(f"{'OK      ' if cond else 'ПРОВАЛ  '} {label}{(' — ' + str(extra)) if extra else ''}")
    if not cond:
        FAILS.append(label)


def req(path, data=None, headers=None, raw=False):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(BASE + path, data=body,
                               method="POST" if body is not None else "GET")
    r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=15) as res:
            payload = res.read()
            return res.status, dict(res.headers), payload if raw else payload.decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")


def jreq(path, data=None, headers=None):
    status, _, body = req(path, data, headers)
    try:
        return status, json.loads(body)
    except ValueError:
        return status, {}


busy_sock = None


def _occupy() -> int:
    """Занимает свободный порт, чтобы проверить отказ при переносе."""
    global busy_sock
    import socket
    busy_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    busy_sock.bind(("127.0.0.1", 0))
    busy_sock.listen(1)
    return busy_sock.getsockname()[1]


def check_windows_paths():
    """В Windows нужные программы часто не в PATH — проверяем запасной поиск."""
    from ld import tts

    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "чего-точно-нет.exe"
        fake.write_bytes(b"MZ")
        # Имя заведомо не из PATH: иначе проверка зависела бы от машины.
        name = "localdub-нет-такой-программы"
        hints = {name: [str(fake)], "пусто": [str(Path(td) / "нет.exe")]}
        check("в Windows программа находится не только по PATH",
              tts.find_binary(name, windows=True, hints=hints) == str(fake))
        check("несуществующий путь не выдаётся за найденный",
              tts.find_binary("пусто", windows=True, hints=hints) is None)
        check("вне Windows запасной поиск не применяется",
              tts.find_binary(name, windows=False, hints=hints) is None)
        check("подсказки для espeak и piper заданы",
              {"espeak-ng", "piper"} <= set(tts._WINDOWS_HINTS), sorted(tts._WINDOWS_HINTS))


def main():
    global BASE
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cfg = Config(tmp / "cfg.json")
        # Пустая папка вместо игры: так /api/start точно ничего не запустит.
        cfg.data.update({"game_dir": str(tmp / "нет-игры"), "lang": "ru",
                         "tts_engine": "espeak"})
        app = App(cfg, Speech(tmp / "tts", engine="espeak"))
        app.port = net.pick_port(8399)
        logged = []
        app.log_sink = logged.append
        # Слушаем все интерфейсы: так в одном прогоне проверяются и запросы
        # с этой машины, и запросы «снаружи» — по локальному адресу.
        server = Server(app, host="0.0.0.0").start()
        # Как в приложении: без этой связи перенос порта невозможен.
        app.server = server
        BASE = f"http://127.0.0.1:{app.port}"
        time.sleep(0.3)
        try:
            run_checks(app, logged)
        finally:
            server.stop()

    print(f"\nпровалов: {len(FAILS)}")
    for f in FAILS:
        print("  -", f)
    return 1 if FAILS else 0


def run_checks(app, logged):
    # --- страница и статика ---
    # Корень — всегда страница игрока: через пробросчики портов (PortHole)
    # запрос гостя приходит с 127.0.0.1, и по адресу клиента отличить нельзя.
    status, _, body = req("/")
    check("корень отдаёт страницу игрока", status == 200 and "phone.js" in body, status)
    check("ключ управления в корне не отдаётся", app.host_key not in body)

    status, _, _ = req("/launcher")
    check("лаунчер без ключа закрыт", status == 403, status)
    status, _, body2 = req(f"/launcher?key={urllib.parse.quote(app.host_key)}")
    check("лаунчер по ключу открывается", status == 200 and 'id="startBtn"' in body2, status)
    check("ключ подставлен в страницу",
          "{{HOST_KEY}}" not in body2 and app.host_key in body2)
    status, _, _ = req("/launcher?key=wrong-key-000000")
    check("чужой ключ не подходит", status == 403, status)

    status, _, css = req("/static/css/launcher.css")
    check("статика отдаётся", status == 200 and ".start" in css, status)
    status, _, _ = req("/static/../ld/server.py")
    check("выход за пределы web/ закрыт", status == 404, status)

    # --- состояние ---
    status, s = jreq("/api/state")
    check("состояние отдаётся", status == 200 and "settings" in s, status)
    for key in ("strings", "engines", "game_ok", "patch", "game", "system", "join_url", "port",
                "session", "game_running", "port_note", "tools_missing", "addresses"):
        check(f"в состоянии есть {key}", key in s, list(s)[:6])
    check("игра не найдена (как и задумано)", s["game_ok"] is False, s["game_ok"])
    check("сводка по сборке отдаётся", s["game"]["ok"] is False, s["game"])
    check("система в состоянии есть",
          s["system"]["os"] in ("Linux", "Windows", "macOS"), s["system"])
    check("строки на русском", any("а" <= c.lower() <= "я" for c in s["strings"].get("start", "")), s["strings"].get("start"))
    check("адрес для телефонов похож на локальный",
          s["join_url"].startswith("http://") and str(app.port) in s["join_url"], s["join_url"])

    # --- выбор адреса: в виртуальных сетях нужный не совпадает с домашним ---
    from ld import net as netmod
    real = netmod.addresses
    try:
        netmod.addresses = lambda: [("enp8s0", "192.168.1.5"), ("Radmin VPN", "26.144.3.7")]
        check("по умолчанию берётся первый адрес", app.announce_ip() == "192.168.1.5",
              app.announce_ip())
        app.cfg.data["announce_ip"] = "26.144.3.7"
        check("выбранный адрес используется", app.announce_ip() == "26.144.3.7", app.announce_ip())
        check("он же попадает в ссылку", "26.144.3.7" in app.state()["join_url"],
              app.state()["join_url"])
        # Адрес виртуальной сети или туннеля системе не виден, но он верный:
        # вписанное руками значение уважаем как есть.
        app.cfg.data["announce_ip"] = "10.0.0.9"
        check("вписанный руками адрес уважается", app.announce_ip() == "10.0.0.9",
              app.announce_ip())
        app.cfg.data["announce_ip"] = "sunny-cat.trycloudflare.com:7777"
        check("адрес со своим портом не удваивается",
              app.state()["join_url"] == "http://sunny-cat.trycloudflare.com:7777/",
              app.state()["join_url"])
    finally:
        netmod.addresses = real
        app.cfg.data["announce_ip"] = ""

    check("адрес виртуальной сети не отбрасывается", netmod._is_useful("26.144.3.7"))
    check("адрес Hamachi тоже", netmod._is_useful("25.0.0.5"))
    check("петля и link-local отбрасываются",
          not netmod._is_useful("127.0.0.1") and not netmod._is_useful("169.254.1.1"))

    # --- настройки ---
    status, _ = jreq("/api/settings", {"lang": "en"})
    check("настройки без ключа закрыты", status == 403, status)
    status, s = jreq("/api/settings", {"lang": "en"}, {"X-Host-Key": app.host_key})
    st = s.get("state", {})
    check("язык переключается", st.get("settings", {}).get("lang") == "en", st.get("settings"))
    check("строки переехали на английский",
          bool(st["strings"].get("join_here")) and st["strings"]["join_here"].isascii(), st["strings"].get("join_here"))
    status, s = jreq("/api/settings", {"lang": "ru", "tts_speed": 9000},
                     {"X-Host-Key": app.host_key})
    got = s.get("state", {}).get("settings", {})
    check("значения ограничиваются", got.get("tts_speed") == 400, f"speed={got.get('tts_speed')}")
    status, s = jreq("/api/settings", {"tts_engine": "не-такой"}, {"X-Host-Key": app.host_key})
    check("неизвестный движок отклонён",
          s["state"]["settings"]["tts_engine"] == "auto", s["state"]["settings"]["tts_engine"])

    # --- запуск без игры ---
    status, s = jreq("/api/start", {}, {"X-Host-Key": app.host_key})
    check("без игры запуск отказывает", s.get("ok") is False and s.get("error") == "err_no_game", s)
    check("сессия не считается запущенной", app.session_on is False, app.session_on)
    status, _ = jreq("/api/start", {})
    check("запуск без ключа закрыт", status == 403, status)

    status, s = jreq("/api/patch", {"action": "apply"}, {"X-Host-Key": app.host_key})
    check("патч без игры отказывает", s.get("ok") is False, s.get("note"))

    # --- перенос на другой порт ---
    global BASE
    free = net.pick_port(app.port + 7)
    status, s = jreq("/api/settings", {"port": free}, {"X-Host-Key": app.host_key})
    check("порт меняется", s.get("ok") is True and f":{free}/launcher?key=" in s.get("redirect", ""),
          f"{s.get('ok')} {s.get('redirect')}")
    check("сервер знает новый порт", app.port == free, app.port)
    BASE = f"http://127.0.0.1:{free}"
    time.sleep(0.4)
    status, fresh = jreq("/api/state")
    check("новый порт отвечает", status == 200 and fresh.get("port") == free,
          f"{status} {fresh.get('port')}")

    busy = _occupy()
    try:
        status, s = jreq("/api/settings", {"port": busy}, {"X-Host-Key": app.host_key})
        check("занятый порт отклонён", s.get("ok") is False and "занят" in (s.get("note") or ""),
              f"{s.get('ok')} {s.get('note')}")
        check("остались на прежнем порту", app.port == free, app.port)
        status, _ = jreq("/api/state")
        check("сервер после отказа жив", status == 200, status)
    finally:
        busy_sock.close()

    # --- QR ---
    status, _, svg = req("/qr.svg")
    check("QR отдаётся как SVG", status == 200 and svg.startswith("<svg"), status)
    status, _, svg = req("/qr.svg?d=http%3A%2F%2F10.0.0.5%3A9000%2F")
    check("QR для заданного адреса", status == 200 and svg.startswith("<svg"), status)

    # --- озвучка ---
    voiced = app.speech.speak("проверка звука", "ru", "male")
    if voiced:
        status, _, body = req(f"/tts/{voiced.key}.wav", raw=True)
        check("wav озвучки отдаётся", status == 200 and body[:4] == b"RIFF", f"{status} {body[:4]}")
    else:
        print("OK       озвучка пропущена — espeak-ng не установлен")
    for bad in ("/tts/../../etc/passwd", "/tts/%D0%B4%D0%B5%D0%B0%D0%B4.wav", "/tts/zzzz.wav"):
        status, _, _ = req(bad)
        check(f"плохой ключ озвучки отклонён: {bad[:28]}", status == 404, status)

    # --- подмена облачного синтеза ---
    body = {"input": {"text": "проверка озвучки"},
            "voice": {"languageCode": "ru-RU", "ssmlGender": 1},
            "audioConfig": {"audioEncoding": 1, "sampleRateHertz": 24000}}
    status, s = jreq("/v1/text:synthesize", body)
    if app.speech.active_name == "none":
        check("без синтезатора отвечаем ошибкой, а не падаем",
              status == 200 and "error" in s, s)
    else:
        import base64
        audio = base64.b64decode(s.get("audioContent", ""))
        check("озвучка отдаётся как WAV в base64",
              status == 200 and audio[:4] == b"RIFF", f"{status} {audio[:4]}")
    status, s = jreq("/v1/text:synthesize", {"text": "и так тоже"})
    check("текст принимается и без обёртки input", status == 200, status)

    status, s = jreq("/v1/voices")
    check("список голосов в форме Google",
          status == 200 and isinstance(s.get("voices"), list) and s["voices"]
          and {"languageCodes", "name", "ssmlGender"} <= set(s["voices"][0]),
          str(s)[:80])

    # --- диагностика со страниц ---
    status, s = jreq("/api/log", {"where": "launcher", "text": "проверка"})
    check("лог со страницы принят без ключа", s.get("ok") is True, s)
    check("сообщение доехало", any("проверка" in x for x in logged), logged[-1:])

    # Отчёт об ошибках не должен захлёбываться: сетевые сбои он не шлёт вовсе,
    # повторы отбрасывает, число сообщений ограничено.
    common = (Path(__file__).resolve().parents[1] / "web/js/common.js").read_text("utf-8")
    check("сетевые ошибки не отправляются", "NETWORK_NOISE" in common)
    check("отказ самого fetch погашен", ".catch(() => {})" in common, "нет .catch")
    check("повторы отбрасываются", "text === previous" in common)
    check("число сообщений ограничено", "REPORT_LIMIT" in common)

    status, _, _ = req("/api/%D0%BD%D0%B5%D1%82")
    check("несуществующий маршрут -> 404", status == 404, status)

    check_remote_access(app)
    check_windows_paths()


def check_remote_access(app):
    """Телефон в сети не должен получать лаунчер и не должен им управлять."""
    global BASE
    lan = net.best_address()
    if lan.startswith("127."):
        print("OK       проверка доступа извне пропущена — нет локального адреса")
        return

    was, BASE = BASE, f"http://{lan}:{app.port}"
    try:
        status, _, body = req("/")
        check("телефон получает свою страницу", status == 200 and "LocalDub" in body, status)
        check("ключ управления не уехал на телефон", app.host_key not in body)
        status, _, _ = req("/launcher")
        check("лаунчер снаружи закрыт", status == 403, status)
        status, _, body = req(f"/launcher?key={urllib.parse.quote(app.host_key)}")
        check("лаунчер снаружи закрыт даже с ключом", status == 403, status)
        status, s = jreq("/api/settings", {"lang": "en"}, {"X-Host-Key": app.host_key})
        check("управление снаружи закрыто даже с ключом", status == 403, status)
        status, _, _ = req("/static/css/base.css")
        check("статика телефону доступна", status == 200, status)
        status, _ = jreq("/v1/text:synthesize", {"text": "снаружи"})
        check("подмена озвучки снаружи закрыта", status == 403, status)
    finally:
        BASE = was


if __name__ == "__main__":
    raise SystemExit(main())
