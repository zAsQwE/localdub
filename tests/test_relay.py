"""Полный раунд через реле с поддельным мостом.

Настоящий мост живёт внутри игры; здесь его роль играет тест. Проверяется вся
цепочка, кроме C#: телефоны заходят, пишут реплики, голосуют, видят свой счёт,
а игра получает ровно те коды и поля, которые разбирает её `OnEvent`.
"""
import json
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ld import net, proto  # noqa: E402
from ld.config import Config  # noqa: E402
from ld.server import App, Server  # noqa: E402
from ld.tts import Speech  # noqa: E402

FAILS = []
BASE = ""
BRIDGE = "тестовый-токен-моста"


def check(label, cond, extra=""):
    print(f"{'OK      ' if cond else 'ПРОВАЛ  '} {label}{(' — ' + str(extra)) if extra else ''}")
    if not cond:
        FAILS.append(label)


def req(path, data=None, raw=False):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(BASE + path, data=body,
                               method="POST" if body is not None else "GET")
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=25) as res:
            payload = res.read()
            return res.status, payload if raw else payload.decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def jreq(path, data=None):
    status, body = req(path, data)
    try:
        return status, json.loads(body)
    except ValueError:
        return status, {}


# --- поддельный мост -------------------------------------------------------
def bridge_props(**props):
    return jreq("/bridge/props", {"token": BRIDGE, "props": props})[1]


def bridge_event(code, content=None):
    payload = content if content is None or isinstance(content, str) else json.dumps(content)
    return jreq("/bridge/event", {"token": BRIDGE, "code": code, "content": payload})[1]


def bridge_room(code, room=""):
    return jreq("/bridge/room", {"token": BRIDGE, "code": code, "room": room})[1]


def bridge_take():
    """Забирает всё, что игроки успели отправить в игру."""
    status, data = jreq(f"/bridge/poll?token={urllib.parse.quote(BRIDGE)}")
    return data.get("items", [])


# --- телефон ----------------------------------------------------------------
def phone_join(name, code="XATE"):
    status, data = jreq("/api/phone/join", {"name": name, "code": code})
    return data.get("player"), data.get("error")


def phone_state(token):
    """Первый кадр из потока состояния — то же, что увидит страница."""
    url = f"{BASE}/api/phone/events?token={urllib.parse.quote(token)}"
    with urllib.request.urlopen(url, timeout=15) as res:
        buf = b""
        while b"\n\n" not in buf:
            chunk = res.read(1)
            if not chunk:
                break
            buf += chunk
    for line in buf.decode("utf-8", "replace").splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    return {}


def main():
    global BASE
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cfg = Config(tmp / "cfg.json")
        cfg.data.update({"game_dir": str(tmp / "нет-игры"), "lang": "ru"})
        app = App(cfg, Speech(tmp / "tts", engine="none"))
        app.port = net.pick_port(8500)
        app.session_token = BRIDGE       # как после нажатия Start
        server = Server(app, host="127.0.0.1").start()
        app.server = server
        BASE = f"http://127.0.0.1:{app.port}"
        time.sleep(0.3)
        try:
            run(app)
        finally:
            server.stop()

    print(f"\nпровалов: {len(FAILS)}")
    for f in FAILS:
        print("  -", f)
    return 1 if FAILS else 0


def run(app):
    # --- страница телефона ---
    status, body = req("/phone")
    check("страница телефона отдаётся", status == 200 and "phone.js" in body, status)
    check("на странице нет видео", "<video" not in body and "/media/" not in body)
    status, _ = req("/static/js/phone.js")
    check("скрипт телефона доступен", status == 200, status)
    status, strings = jreq("/api/phone/strings")
    check("строки для телефона", status == 200 and "phone_send" in strings, status)

    # --- защита моста ---
    status, _ = jreq("/bridge/props", {"token": "не тот", "props": {}})
    check("чужой токен моста отклонён", status == 403, status)
    status, _ = jreq("/bridge/poll")
    check("опрос без токена отклонён", status == 403, status)
    check("мост опознан", bridge_props(game_state=proto.WAITING_FOR_PLAYERS).get("ok") is True)
    check("реле видит мост живым", app.relay.bridge_alive is True)

    # --- код комнаты ---
    check("без комнаты не пускает", phone_join("Аня")[1] == "no_room")
    check("код комнаты принят", bridge_room("xate").get("ok") is True)
    check("код приведён к верхнему регистру", app.relay.room_code == "XATE", app.relay.room_code)
    check("неверный код отклонён", phone_join("Аня", "ZZZZ")[1] == "bad_code")
    spare, _ = phone_join("Проверка", " x a t e ")
    check("код нечувствителен к регистру и пробелам", spare is not None)
    jreq("/api/phone/leave", {"token": spare["token"]})
    bridge_take()   # очищаем очередь, чтобы дальше считать только нужное

    # --- вход двух телефонов ---
    anya, _ = phone_join("Аня")
    borya, _ = phone_join("Боря")
    check("два телефона вошли", bool(anya and borya))
    check("имена не повторяются", phone_join("аня")[1] == "taken")
    check("пустое имя отклонено", phone_join("   ")[1] == "bad_name")

    items = bridge_take()
    joins = [i for i in items if i["kind"] == "join"]
    check("игра узнала про обоих", len(joins) == 2, [j["nick"] for j in joins])
    check("у игроков разные номера актёров",
          len({j["actor"] for j in joins}) == 2, [j["actor"] for j in joins])
    check("в инструкции есть userId, он же playerID",
          all(j["userId"] for j in joins) and {j["userId"] for j in joins} == {anya["pid"], borya["pid"]})

    st = phone_state(anya["token"])
    check("телефон в лобби", st["phase"] == "lobby", st["phase"])
    check("телефон видит список игроков", len(st["players"]) == 2, st["players"])

    # --- старт с телефона ---
    bridge_props(client_can_manage=True, game_manager_id=anya["pid"], min_players=2)
    status, res = jreq("/api/phone/control", {"token": borya["token"], "action": "start"})
    check("не-ведущему старт запрещён", res.get("ok") is False and res.get("error") == "not_manager", res)
    status, res = jreq("/api/phone/control", {"token": borya["token"], "action": "skip_intro"})
    check("не-хосту пропуск вступления запрещён",
          res.get("ok") is False and res.get("error") == "not_host", res)
    check("код пропуска от не-хоста не ушёл",
          not any(i.get("code") == proto.SKIP_INTRO for i in bridge_take()), "ушёл")
    status, res = jreq("/api/phone/control", {"token": anya["token"], "action": "skip_intro"})
    check("хост может пропустить вступление", res.get("ok") is True, res)
    check("игре ушёл код пропуска",
          any(i.get("code") == proto.SKIP_INTRO for i in bridge_take()), "нет")
    check("телефон знает, кто хост",
          phone_state(anya["token"])["is_host"] is True
          and phone_state(borya["token"])["is_host"] is False)
    status, res = jreq("/api/phone/control", {"token": anya["token"], "action": "start"})
    check("ведущий может начать", res.get("ok") is True, res)
    items = bridge_take()
    check("игре ушёл код старта",
          any(i["kind"] == "event" and i["code"] == proto.CLIENT_TRIGGERED_START for i in items), items)

    # --- отсчёт перед стартом: хост может передумать ---
    st = phone_state(anya["token"])
    check("при минимуме в двух игроков хватает", st["players_needed"] == 0, st)
    bridge_props(min_players=3)
    check("при минимуме в трёх хост видит, что не хватает одного",
          phone_state(anya["token"])["players_needed"] == 1)
    bridge_props(allow_no_score_mode=True)
    st = phone_state(anya["token"])
    check("вдвоём можно играть без очков",
          st["players_needed"] == 0 and st["no_score"] is True, st)
    bridge_props(min_players=2, allow_no_score_mode=False)
    check("при обычном минимуме режим без очков не нужен",
          phone_state(anya["token"])["no_score"] is False)
    bridge_event(proto.SERVER_TRIGGERED_START, None)
    check("телефон видит отсчёт", phone_state(borya["token"])["countdown"] is True)
    status, res = jreq("/api/phone/control", {"token": borya["token"], "action": "cancel"})
    check("не-хосту отмена старта запрещена", res.get("ok") is False, res)
    status, res = jreq("/api/phone/control", {"token": anya["token"], "action": "cancel"})
    check("хост может отменить старт", res.get("ok") is True, res)
    check("игре ушёл код отмены",
          any(i.get("code") == proto.CLIENT_CANCELLED_START for i in bridge_take()), "нет")
    bridge_event(proto.SERVER_CANCELLED_START, None)
    check("после отмены отсчёта нет", phone_state(anya["token"])["countdown"] is False)
    bridge_event(proto.SERVER_TRIGGERED_START, None)

    # --- вступление и раунд ---
    bridge_props(game_state=proto.PLAYING_INTRO)
    bridge_event(proto.NEXT_ROUND, {"roundNumber": 1})
    st = phone_state(anya["token"])
    check("телефон показывает вступление", st["phase"] == "intro", st["phase"])
    check("с началом игры отсчёт закончился", st["countdown"] is False)
    check("номер раунда доехал", st["round"] == 1, st["round"])

    bridge_props(game_state=proto.PLAYING_VIDEO)
    st = phone_state(borya["token"])
    check("во время сцены телефон отправляет смотреть на экран", st["phase"] == "watch", st["phase"])
    check("видео на телефон не уходит", "script" in st and st["script"] == "", st.get("script"))

    # --- реплики ---
    scene = "Anchor: и в итоге...<br><mark>ваша реплика</mark>"
    bridge_event(proto.ENTER_DUB_TIME, {"script": scene})
    bridge_props(game_state=proto.SUBMITTING_DUBS, max_dub_length=150)
    st = phone_state(anya["token"])
    check("телефон перешёл к вводу", st["phase"] == "write", st["phase"])
    check("текст сцены доехал", st["script"] == scene, st["script"][:40])
    check("предел длины доехал", st["max_length"] == 150, st["max_length"])
    check("без звуков список пуст", st["sfx"] == [], st["sfx"])

    # --- звуки из игры ---
    bridge_props(inline_sfx=[
        {"display": "Toilet Flushed", "replacement": "(Toilet Flushes)"},
        {"display": "Robot", "replacement": "(Robot Noise)"},
        {"display": "Пусто", "replacement": ""},
    ], max_inline_sfx=2)
    st = phone_state(anya["token"])
    names = [x["display"] for x in st["sfx"]]
    check("звуки доехали до телефона", names == ["Toilet Flushes", "Robot"], names)
    check("опечатка в подписи исправлена", names[0] == "Toilet Flushes", names)
    check("опечатка в замене исправлена",
          st["sfx"][1]["replacement"] == "(Robotic Noise)", st["sfx"][1])
    check("предел звуков доехал", st["max_sfx"] == 2, st["max_sfx"])

    status, res = jreq("/api/phone/dub",
                       {"token": anya["token"], "text": "Ой (Toilet Flushes) простите"})
    check("реплика со звуком принимается", res.get("ok"), res)
    sent = [i for i in bridge_take() if i.get("code") == proto.DUB_SUBMITTED]
    check("звук ушёл в игру внутри текста",
          "(Toilet Flushes)" in json.loads(sent[0]["content"])["dub"], sent[:1])

    status, res = jreq("/api/phone/dub", {"token": anya["token"], "text": "   "})
    check("пустая реплика отклонена", res.get("ok") is False and res.get("error") == "empty", res)
    bridge_props(inline_sfx=[], max_inline_sfx=-1)
    jreq("/api/phone/dub", {"token": anya["token"], "text": "Я забыл выключить утюг"})
    jreq("/api/phone/dub", {"token": borya["token"], "text": "Это не мои штаны"})

    items = [i for i in bridge_take() if i["kind"] == "event"]
    dubs = [i for i in items if i["code"] == proto.DUB_SUBMITTED]
    check("игре ушли обе реплики", len(dubs) == 2, len(dubs))
    fields = json.loads(dubs[0]["content"])
    check("поля реплики точно как в игре",
          set(fields) == {"playerID", "dub", "id", "languageCode"}, sorted(fields))
    check("язык передан в нужном виде", fields["languageCode"] == "ru-RU", fields["languageCode"])
    check("playerID — это pid телефона",
          {json.loads(d["content"])["playerID"] for d in dubs} == {anya["pid"], borya["pid"]})

    st = phone_state(anya["token"])
    check("после отправки телефон показывает отправленное",
          st["submitted"] == "Я забыл выключить утюг", st["submitted"])

    # --- голосование: данные приходят вложенным словарём, как их шлёт игра ---
    bridge_props(game_state=proto.PLAYING_BACK)
    check("во время проигрывания — на экран", phone_state(anya["token"])["phase"] == "watch")

    options = {
        "dub-a": {"id": "dub-a", "playerIDs": [anya["pid"]], "dub": "Я забыл выключить утюг"},
        "dub-b": {"id": "dub-b", "playerIDs": [borya["pid"]], "dub": "Это не мои штаны"},
    }
    bridge_event(proto.VOTE_TIME, options)
    bridge_props(game_state=proto.VOTING, js_version=2, allowed_vote_count=1)

    st = phone_state(anya["token"])
    check("телефон перешёл к голосованию", st["phase"] == "vote", st["phase"])
    check("свой вариант в списке не показан",
          [o["id"] for o in st["options"]] == ["dub-b"], st["options"])

    status, res = jreq("/api/phone/vote", {"token": anya["token"], "ids": ["dub-a"]})
    check("голос за себя отклонён", res.get("ok") is False and res.get("error") == "own", res)
    status, res = jreq("/api/phone/vote", {"token": anya["token"], "ids": ["dub-b"]})
    check("голос принят", res.get("ok") is True, res)

    votes = [i for i in bridge_take() if i.get("code") == proto.VOTE_SUBMITTED]
    check("игре ушёл голос", len(votes) == 1, votes)
    vfields = json.loads(votes[0]["content"])
    check("поля голоса точно как в игре",
          set(vfields) == {"votingPlayerID", "dubID"}, sorted(vfields))
    check("в голосе pid и id реплики",
          vfields == {"votingPlayerID": anya["pid"], "dubID": "dub-b"}, vfields)

    st = phone_state(anya["token"])
    check("после голоса телефон ждёт остальных", st["voted"] == ["dub-b"], st["voted"])

    # --- итоги ---
    bridge_event(proto.INDIVIDUAL_ROUND_SCORE,
                 {"userId": borya["pid"], "rank0Votes": 1, "rank1Votes": 0,
                  "rank2Votes": 0, "audienceVotes": 0})
    bridge_props(game_state=proto.SHOWING_RESULTS)
    st = phone_state(borya["token"])
    check("телефон видит свой счёт", st["phase"] == "scores" and st["my_score"], st.get("my_score"))
    check("чужой счёт не подсунут", phone_state(anya["token"])["my_score"] is None)

    bridge_event(proto.GAME_OVER_WINNER, borya["pid"])
    bridge_props(game_state=proto.WAITING_NEXT_GAME)
    check("победитель доехал", phone_state(borya["token"])["winner"] == borya["pid"])

    # --- исключение из игры ---
    bridge_event(proto.KICK_PLAYER, {"id": anya["pid"]})
    st = phone_state(anya["token"])
    check("исключённый телефон это видит",
          st["phase"] == "kicked" and st["kicked"] == "kicked_by_host", st.get("kicked"))
    check("остальных это не задело", phone_state(borya["token"])["phase"] != "kicked")

    # --- сводка для лаунчера ---
    status, s = jreq("/api/state")
    check("лаунчер видит сводку реле",
          s.get("relay", {}).get("bridge") is True and len(s["relay"]["players"]) == 2,
          s.get("relay"))
    check("лаунчер знает код комнаты", s["relay"]["code"] == "XATE", s["relay"].get("code"))

    # --- выход ---
    jreq("/api/phone/leave", {"token": borya["token"]})
    items = bridge_take()
    check("игра узнала про выход",
          any(i["kind"] == "leave" and i["actor"] == borya["actor"] for i in items), items)
    check("неизвестный токен состояния не даёт", phone_state("мусор").get("known") is False)

    # --- свой код комнаты, в том числе по-русски ---
    bridge_room("Кот", "комната-1")
    check("русский код принят", app.relay.room_code == "КОТ", app.relay.room_code)
    vasya, why = phone_join("Вася", "кот")
    check("русский код вводится строчными", vasya is not None, why)
    petya, why = phone_join("Петя", "KOT")
    check("похожие латинские буквы тоже подходят", petya is not None, why)
    check("чужой код не подходит", phone_join("Гоша", "КИТ")[1] == "bad_code")
    bridge_room("КОТ", "комната-1")
    check("повтор той же комнаты состав не сбрасывает", len(app.relay.players) == 2,
          len(app.relay.players))
    bridge_room("КОТ", "комната-2")
    check("новая комната с тем же кодом начинается с чистого листа",
          len(app.relay.players) == 0, len(app.relay.players))


if __name__ == "__main__":
    raise SystemExit(main())
