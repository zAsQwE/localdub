"""HTTP-сервер лаунчера. Только стандартная библиотека — никаких зависимостей."""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import posixpath
import re
import secrets
import socket
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config, i18n, net, qr, wtd
from .relay import Relay

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
MAX_BODY = 64 * 1024
SSE_HEARTBEAT = 10.0


def display_address(url: str) -> str:
    """Адрес для экрана игры: без http:// и косой черты в конце — его набирают руками."""
    return re.sub(r"^https?://", "", url.strip(), flags=re.I).rstrip("/")


def _same_secret(given: str, expected: str) -> bool:
    """Сравнение без утечки времени и без падений на любых символах."""
    if not given or not expected:
        return False
    return secrets.compare_digest(
        given.encode("utf-8", "surrogatepass"),
        expected.encode("utf-8", "surrogatepass"),
    )


class App:
    """Общее состояние: настройки, синтез речи, текущая сессия."""

    def __init__(self, cfg, speech):
        self.cfg = cfg
        self.speech = speech
        self.relay = Relay(cfg["lang"])
        self.port = cfg["port"]
        # Ключ защищает изменение настроек и запуск: страницу лаунчера
        # открывает окно приложения, а не телефон в комнате.
        self.host_key = os.environ.get("LOCALDUB_HOST_KEY") or secrets.token_urlsafe(16)
        # Переменная окружения нужна автотестам и отладке моста без запуска игры.
        self.session_token = os.environ.get("LOCALDUB_SESSION_TOKEN", "")
        # Сессия — это поднятый адрес для телефонов. Она держится, пока её
        # не остановят: закрытая игра не должна уносить с экрана адрес.
        self.session_on = False
        self.last_error = ""
        self.port_note = ""
        self.log_sink = None
        self.folder_picker = None
        self.on_settings_change = None
        self.server = None
        self.verbose = False

    # --- сессия ---------------------------------------------------------
    def start_session(self) -> tuple[bool, str]:
        game = self.cfg["game_dir"]
        if not config.is_game_dir(game):
            return False, "err_no_game"

        report = wtd.game_report(game, self.port, self.cfg["dotnet_path"])
        # Патч встраивается заново и когда его нет, и когда в нём зашит
        # прежний порт: иначе игра пошла бы за озвучкой не туда.
        if report["patch"] != wtd.ON or report["notice"] == "tts_port_changed":
            ok, note = wtd.apply_patch(game, self.port, self.cfg["dotnet_path"])
            if not ok:
                self.last_error = note
                return False, "err_no_patch"

        self.session_token = os.environ.get("LOCALDUB_SESSION_TOKEN") or secrets.token_urlsafe(12)
        self.relay.reset()
        self.relay.lang = self.cfg["lang"]
        managed = config.managed_dir(game)
        # Мост мог устареть с прошлой установки патча — подкладываем свежий.
        wtd.sync_bridge(managed)
        wtd.clear_bridge_log(managed)
        self.write_handshake()

        # Адрес поднимаем до запуска игры: он нужен сразу, а игра
        # добирается до экрана лобби только через несколько секунд.
        self.session_on = True
        self.last_error = ""

        ok, note = wtd.launch_game()
        if not ok:
            self.last_error = note
            return False, "err_launch"
        return True, "ok"

    def stop_session(self) -> None:
        wtd.stop_game()
        self.session_on = False
        self.session_token = ""

    def set_port(self, port: int) -> tuple[bool, str]:
        """Переносит сервер на заданный порт, если он свободен."""
        if port == self.port:
            return True, ""
        if not self.server:
            return False, "сервер недоступен"
        ok, note = self.server.rebind(port)
        if ok:
            self.port_note = ""
            if self.session_on:
                self.write_handshake()
        return ok, note

    def write_handshake(self) -> None:
        """Пишет мосту, куда стучаться; во время игры — заодно новые настройки."""
        game = self.cfg["game_dir"]
        if not config.is_game_dir(game):
            return
        url = net.join_url(self.port, self.announce_ip())
        try:
            matrix = ["".join("1" if dark else "0" for dark in row) for row in qr.encode(url)]
        except ValueError:
            matrix = []  # слишком длинный адрес — обойдёмся надписью
        wtd.write_handshake(config.managed_dir(game), self.port, self.cfg["lang"],
                            self.session_token, self.verbose,
                            room_code=self.cfg["room_code"],
                            join_text=display_address(url), join_qr=matrix)

    def announce_ip(self) -> str:
        """Адрес, который показываем друзьям.

        Обычно подходит тот, что система выбрала бы сама. Но в виртуальных
        сетях (Radmin) и туннелях адрес другой и системе не известен, поэтому
        в настройках его можно вписать руками — хоть с портом, хоть именем.
        """
        wanted = (self.cfg["announce_ip"] or "").strip()
        if wanted:
            return wanted
        found = [ip for _, ip in net.addresses()]
        return found[0] if found else "127.0.0.1"

    def state(self) -> dict:
        game = self.cfg["game_dir"]
        dotnet = self.cfg["dotnet_path"]
        report = wtd.game_report(game, self.port, dotnet)
        return {
            "game": report,
            "system": wtd.system_info(dotnet),
            "strings": i18n.strings(self.cfg["lang"]),
            "settings": self.cfg.data,
            "engines": self.speech.engine_details(self.cfg["lang"]),
            "active_voice": self.speech.active_name,
            "game_ok": report["ok"],
            "patch": report["patch"],
            "tools_missing": wtd.tools(dotnet).missing,
            "session": self.session_on,
            "game_running": wtd.game_running(),
            "join_url": net.join_url(self.port, self.announce_ip()),
            "addresses": [{"iface": i, "ip": a} for i, a in net.addresses()],
            "port": self.port,
            "can_pick_folder": self.folder_picker is not None,
            "error": self.last_error,
            "port_note": self.port_note,
            "bridge_log": (wtd.bridge_log(config.managed_dir(game))[-1200:]
                           if report["ok"] else ""),
            "relay": self.relay.host_view(),
        }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "LocalDub"
    sys_version = ""

    app: App  # проставляется фабрикой

    def log_message(self, fmt, *args):  # тише в консоли
        pass

    # --- ответы ----------------------------------------------------------
    def _send(self, code: int, body: bytes = b"", ctype: str = "text/plain; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD" and body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(self, data, code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _error(self, code: int, why: str = "") -> None:
        self._json({"ok": False, "error": why or HTTPStatus(code).phrase}, code)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0 or length > MAX_BODY:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return {}

    def _query(self) -> dict:
        parsed = urllib.parse.urlparse(self.path)
        return {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}

    @property
    def route(self) -> str:
        return urllib.parse.unquote(urllib.parse.urlparse(self.path).path)

    def _is_local(self) -> bool:
        """Лаунчер открывает окно приложения; телефонам он не полагается."""
        return self.client_address[0] in ("127.0.0.1", "::1", "localhost")

    def _is_host(self) -> bool:
        return _same_secret(self.headers.get("X-Host-Key") or self._query().get("key", ""),
                            self.app.host_key)

    # --- маршруты ---------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        route = self.route
        try:
            if route == "/":
                # Корень — всегда страница игрока. Раньше здесь смотрели на адрес
                # клиента, но через пробросчики портов (PortHole и подобные)
                # запрос гостя приходит с 127.0.0.1, и ключ управления уехал бы
                # чужому человеку. Поэтому лаунчер выдаётся только по ключу.
                return self._page("phone.html", with_key=False)
            if route == "/launcher":
                # Два замка: ключ (его знает только окно приложения) и адрес.
                if not self._is_host() or not self._is_local():
                    return self._error(403, "лаунчер открывается самим приложением")
                return self._page("launcher.html")
            if route == "/phone":
                return self._page("phone.html", with_key=False)
            if route.startswith("/static/"):
                return self._static(route[len("/static/"):])
            if route == "/api/state":
                return self._json(self.app.state())
            if route == "/api/phone/strings":
                # Телефону нужны строки, но не нужен ключ управления.
                return self._json(i18n.strings(self.app.cfg["lang"]))
            if route == "/api/phone/events":
                return self._phone_events()
            if route == "/bridge/poll":
                return self._bridge_poll()
            if route == "/qr.svg":
                return self._qr()
            if route.startswith("/tts/"):
                return self._tts(route[len("/tts/"):])
            if route == "/v1/voices":
                return self._google_voices()
            return self._error(404)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        route = self.route
        try:
            data = self._body()
            if route == "/api/log":
                return self._client_log(data)
            if route.startswith("/api/phone/"):
                return self._phone_action(route[len("/api/phone/"):], data)
            if route.startswith("/bridge/"):
                return self._bridge_post(route[len("/bridge/"):], data)
            if route == "/v1/text:synthesize":
                return self._google_synthesize(data)
            if not self._is_local():
                return self._error(403, "управление только с этого компьютера")
            if not self._is_host():
                return self._error(403, "нужен ключ лаунчера")
            if route == "/api/settings":
                return self._settings(data if isinstance(data, dict) else {})
            if route == "/api/start":
                ok, why = self.app.start_session()
                return self._json({"ok": ok, "error": None if ok else why,
                                   "state": self.app.state()})
            if route == "/api/stop":
                self.app.stop_session()
                return self._json({"ok": True, "state": self.app.state()})
            if route == "/api/patch":
                return self._patch(data)
            if route == "/api/pick_folder":
                return self._pick_folder()
            if route == "/api/piper/download":
                ok, note = self.app.speech.download_voice(self.app.cfg["lang"])
                return self._json({"ok": ok, "note": note, "state": self.app.state()})
            return self._error(404)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # --- обработчики -------------------------------------------------------
    def _page(self, name: str, with_key: bool = True) -> None:
        path = WEB_DIR / name
        if not path.is_file():
            return self._error(404, f"нет файла {name}")
        html = path.read_text("utf-8")
        html = html.replace("{{HOST_KEY}}", self.app.host_key if with_key else "")
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def _static(self, rel: str) -> None:
        clean = posixpath.normpath("/" + rel).lstrip("/")
        path = (WEB_DIR / clean).resolve()
        if not str(path).startswith(str(WEB_DIR.resolve())) or not path.is_file():
            return self._error(404)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith(("javascript", "json")):
            ctype += "; charset=utf-8"
        self._send(200, path.read_bytes(), ctype)

    def _settings(self, data: dict) -> None:
        app = self.app
        if "room_code" in data:
            problem = config.room_code_problem(data["room_code"])
            if problem:
                return self._json({"ok": False, "note": i18n.t(app.cfg["lang"], problem),
                                   "state": app.state()})
        wanted = data.get("port")
        app.cfg.update(data)
        if app.on_settings_change:
            app.on_settings_change()
        app.relay.lang = app.cfg["lang"]
        if app.session_on:
            app.write_handshake()

        payload = {"ok": True, "note": ""}
        if wanted is not None and app.cfg["port"] != app.port:
            ok, note = app.set_port(app.cfg["port"])
            if ok:
                # Ответ уходит по старому соединению, поэтому адрес для
                # перехода страница получает прямо здесь. Ключ обязателен:
                # без него по новому адресу откроется страница игрока.
                key = urllib.parse.quote(app.host_key)
                payload["redirect"] = f"http://127.0.0.1:{app.port}/launcher?key={key}"
            else:
                payload["ok"] = False
                payload["note"] = note
                app.cfg.update({"port": app.port})
        payload["state"] = app.state()
        self._json(payload)

    def _patch(self, data: dict) -> None:
        action = str(data.get("action", "apply"))
        game = self.app.cfg["game_dir"]
        dotnet = self.app.cfg["dotnet_path"]
        if action == "remove":
            ok, note = wtd.remove_patch(game, dotnet)
        else:
            ok, note = wtd.apply_patch(game, self.app.port, dotnet)
        self.app.last_error = "" if ok else note
        self._json({"ok": ok, "note": note, "state": self.app.state()})

    def _pick_folder(self) -> None:
        picker = self.app.folder_picker
        if not picker:
            return self._json({"ok": False, "error": "нет диалога выбора"})
        chosen = picker()
        self._json({"ok": bool(chosen), "path": chosen or ""})

    # --- телефоны ------------------------------------------------------------
    def _phone_action(self, action: str, data: dict) -> None:
        relay = self.app.relay
        token = str(data.get("token", ""))

        if action == "join":
            ident, why = relay.join(str(data.get("name", "")),
                                    str(data.get("code", "")), token or "")
            if not ident:
                return self._json({"ok": False, "error": why})
            return self._json({"ok": True, "player": ident})
        if action == "dub":
            ok, why = relay.submit_dub(token, str(data.get("text", "")))
            return self._json({"ok": ok, "error": None if ok else why})
        if action == "vote":
            ids = data.get("ids")
            if not isinstance(ids, list):
                ids = [data.get("id")] if data.get("id") else []
            ok, why = relay.submit_vote(token, [str(i) for i in ids])
            return self._json({"ok": ok, "error": None if ok else why})
        if action == "control":
            ok, why = relay.control(token, str(data.get("action", "")))
            return self._json({"ok": ok, "error": None if ok else why})
        if action == "leave":
            return self._json({"ok": relay.leave(token)})
        return self._error(404)

    def _phone_events(self) -> None:
        """Состояние телефона потоком: сервер шлёт снимок при каждом изменении."""
        token = self._query().get("token", "")
        relay = self.app.relay

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        seen = -1
        beat = time.monotonic()
        try:
            while True:
                state = relay.phone_state(token)
                if state["version"] != seen:
                    seen = state["version"]
                    payload = json.dumps(state, ensure_ascii=False)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    beat = time.monotonic()
                relay.wait_for_change(seen, timeout=1.0)
                if time.monotonic() - beat > SSE_HEARTBEAT:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    beat = time.monotonic()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    # --- мост внутри игры ------------------------------------------------------
    def _bridge_ok(self, data: dict) -> bool:
        """Мост живёт на этой же машине и знает токен сессии."""
        if not self._is_local():
            return False
        return _same_secret(str(data.get("token") or self._query().get("token", "")),
                            self.app.session_token)

    def _bridge_post(self, action: str, data: dict) -> None:
        if not self._bridge_ok(data):
            return self._error(403, "мост не опознан")
        relay = self.app.relay

        if action == "hello":
            relay.bridge_seen = time.monotonic()
            if self.app.log_sink:
                self.app.log_sink("[мост] подключился")
            return self._json({"ok": True, "lang": self.app.cfg["lang"]})
        if action == "event":
            try:
                code = int(data.get("code"))
            except (TypeError, ValueError):
                return self._json({"ok": False, "error": "bad_code"})
            relay.from_game(code, data.get("content"))
            return self._json({"ok": True})
        if action == "props":
            relay.set_props(data.get("props") or {})
            return self._json({"ok": True})
        if action == "room":
            code = str(data.get("code", ""))
            relay.set_room(code, str(data.get("room", "")))
            if self.app.log_sink:
                self.app.log_sink(f"[мост] комната {relay.room_code}")
            return self._json({"ok": True})
        return self._error(404)

    def _bridge_poll(self) -> None:
        """Долгий опрос: мост ждёт здесь, пока не появятся действия игроков."""
        if not self._bridge_ok({}):
            return self._error(403, "мост не опознан")
        relay = self.app.relay
        deadline = time.monotonic() + 20.0
        while True:
            items = relay.take_queue()
            if items or time.monotonic() >= deadline:
                return self._json({"ok": True, "items": items})
            relay.wait_for_change(relay.version, timeout=1.0)

    # --- подмена облачного синтеза Google ------------------------------------
    # Игра ходит за озвучкой в texttospeech.googleapis.com. Патч подменяет этот
    # адрес на наш, а здесь мы отвечаем в том же виде, но озвучиваем локально.
    _GENDERS = {0: "male", 1: "male", 2: "female", 3: "male",
                "MALE": "male", "FEMALE": "female"}

    def _google_synthesize(self, data: dict) -> None:
        if not self._is_local():
            return self._error(403, "только с этого компьютера")

        text = ""
        source = data.get("input")
        if isinstance(source, dict):
            text = str(source.get("text") or source.get("ssml") or "")
        if not text:
            text = str(data.get("text", ""))

        voice = data.get("voice") if isinstance(data.get("voice"), dict) else {}
        lang = str(voice.get("languageCode") or self.app.cfg["lang"])[:2].lower()
        gender = self._GENDERS.get(voice.get("ssmlGender"), "male")

        voiced = self.app.speech.speak(text, lang if lang in ("ru", "en") else "en", gender)
        if not voiced:
            if self.app.log_sink:
                self.app.log_sink(f"[озвучка] нечем синтезировать: {text[:40]!r}")
            return self._json({"error": "нет доступного синтезатора"}, 200)

        try:
            audio = voiced.path.read_bytes()
        except OSError as e:
            return self._json({"error": str(e)}, 200)

        if self.app.log_sink:
            self.app.log_sink(
                f"[озвучка] {lang}/{gender} {voiced.duration:.1f}с: {text[:40]!r}")
        self._json({"audioContent": base64.b64encode(audio).decode("ascii")})

    def _google_voices(self) -> None:
        """Список голосов в форме, которую ждёт игра."""
        if not self._is_local():
            return self._error(403, "только с этого компьютера")
        voices = []
        for code in ("ru-RU", "en-US", "en-GB"):
            for gender, tag in ((1, "Male"), (2, "Female")):
                voices.append({
                    "languageCodes": [code],
                    "name": f"{code}-LocalDub-{tag}",
                    "ssmlGender": gender,
                    "naturalSampleRateHertz": 22050,
                })
        self._json({"voices": voices})

    def _client_log(self, data: dict) -> None:
        """Ошибки со страниц и из моста — иначе их негде увидеть."""
        if self.app.log_sink:
            where = str(data.get("where", "?"))[:40]
            text = str(data.get("text", ""))[:500]
            self.app.log_sink(f"[{where}] {text}")
        self._json({"ok": True})

    def _qr(self) -> None:
        data = self._query().get("d") or net.join_url(self.app.port)
        try:
            svg = qr.to_svg(data[:400], quiet=2)
        except ValueError:
            return self._error(400, "слишком длинная строка")
        self._send(200, svg.encode("utf-8"), "image/svg+xml; charset=utf-8")

    def _tts(self, rel: str) -> None:
        key = rel[:-4] if rel.endswith(".wav") else rel
        if not re.fullmatch(r"[0-9a-f]{8,64}", key):
            return self._error(404)
        path = self.app.speech.path_for(key)
        if not path.is_file():
            return self._error(404)
        try:
            body = path.read_bytes()
        except OSError:
            return self._error(404)
        self._send(200, body, "audio/wav")


class Server:
    def __init__(self, app: App, host: str = "0.0.0.0"):
        self.app = app
        self.host = host
        handler = type("BoundHandler", (Handler,), {"app": app})
        self.httpd = ThreadingHTTPServer((host, app.port), handler)
        self.httpd.daemon_threads = True
        self.httpd.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    def start(self) -> "Server":
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def rebind(self, port: int) -> tuple[bool, str]:
        """Поднимает новый сокет и только потом гасит старый."""
        handler = type("BoundHandler", (Handler,), {"app": self.app})
        try:
            fresh = ThreadingHTTPServer((self.host, port), handler)
        except OSError:
            return False, "порт занят"
        fresh.daemon_threads = True
        fresh.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        old = self.httpd
        self.httpd = fresh
        self.app.port = port
        self.thread = threading.Thread(target=fresh.serve_forever, daemon=True)
        self.thread.start()

        def retire():
            # Даём ответу уйти по старому соединению, прежде чем его закрыть.
            time.sleep(1.0)
            try:
                old.shutdown()
                old.server_close()
            except OSError:
                pass

        threading.Thread(target=retire, daemon=True).start()
        return True, ""

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
