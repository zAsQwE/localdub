"""Реле между телефонами и игрой.

Своей игровой логики здесь нет и быть не должно: партию ведёт сама игра.
Реле лишь зеркалит её состояние для телефонов и складывает действия игроков
в очередь, из которой их забирает мост внутри игры.

Видео на телефон не уходит вообще — оно идёт на экране хоста, в окне игры.
Телефон получает только текст.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
import uuid

from . import proto

CONNECTED_SECONDS = 25.0
MAX_QUEUE = 400


def _now() -> float:
    return time.monotonic()


class Player:
    """Телефон, зашедший в комнату. `pid` — то же, что playerID в протоколе."""

    def __init__(self, pid: str, token: str, name: str, actor: int):
        self.pid = pid
        self.token = token
        self.name = name
        self.actor = actor
        self.last_seen = _now()
        self.kicked = ""
        self.submitted: str | None = None
        self.votes: list[str] = []

    @property
    def connected(self) -> bool:
        return _now() - self.last_seen < CONNECTED_SECONDS

    def public(self) -> dict:
        return {"pid": self.pid, "name": self.name, "actor": self.actor,
                "connected": self.connected}


class Relay:
    def __init__(self, lang: str = "ru"):
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.version = 0
        self.lang = lang

        self.props = dict(proto.DEFAULT_PROPS)
        self.players: dict[str, Player] = {}
        self.order: list[str] = []
        self._actor_seq = 1

        self.queue: list[dict] = []      # инструкции для моста
        self.bridge_seen = 0.0

        self.script = ""
        self.options: list[dict] = []
        self.round = None
        self.winner: str | None = None
        self.scores: dict[str, dict] = {}
        # Идёт ли отсчёт перед стартом: хост в это время может его отменить.
        self.countdown = False
        # Код комнаты игра показывает на общем экране; телефон должен его ввести.
        self.room_code = ""
        self.room_id = ""

    # --- служебное --------------------------------------------------------
    def _bump(self) -> None:
        self.version += 1
        self.changed.notify_all()

    def wait_for_change(self, seen: int, timeout: float = 15.0) -> int:
        with self.changed:
            if self.version != seen:
                return self.version
            self.changed.wait(timeout)
            return self.version

    @property
    def bridge_alive(self) -> bool:
        return self.bridge_seen > 0 and _now() - self.bridge_seen < 15.0

    def _push(self, item: dict) -> None:
        self.queue.append(item)
        if len(self.queue) > MAX_QUEUE:
            del self.queue[:-MAX_QUEUE]
        self._bump()

    def _by_token(self, token: str) -> Player | None:
        if not token:
            return None
        probe = token.encode("utf-8", "surrogatepass")
        for player in self.players.values():
            if secrets.compare_digest(
                    player.token.encode("utf-8", "surrogatepass"), probe):
                return player
        return None

    def touch(self, token: str) -> Player | None:
        with self.lock:
            player = self._by_token(token)
            if player:
                player.last_seen = _now()
            return player

    # --- вход телефонов ----------------------------------------------------
    @staticmethod
    def normalize_code(code: str) -> str:
        return "".join(ch for ch in str(code or "") if ch.isalnum()).upper().replace("Ё", "Е")

    @classmethod
    def fold_code(cls, code: str) -> str:
        """Код для сравнения: кириллица, похожая на латиницу, считается ею.

        Иначе «КОТ» с экрана, набранный в английской раскладке, не подошёл бы.
        """
        return cls.normalize_code(code).translate(_LOOKALIKES)

    def set_room(self, code: str, room_id: str = "") -> None:
        """Игра открыла или закрыла комнату.

        Новая комната — новый состав: старые телефоны игре уже незнакомы,
        поэтому их приходится отпустить, чтобы они зашли заново по новому коду.
        Код, заданный в лаунчере, повторяется, поэтому новую комнату узнаём
        ещё и по её номеру от моста.
        """
        with self.lock:
            self.bridge_seen = _now()
            fresh = self.normalize_code(code)
            room_id = str(room_id or "")
            if fresh == self.room_code and room_id == self.room_id:
                return
            self.room_code = fresh
            self.room_id = room_id
            self.players.clear()
            self.order.clear()
            self.queue.clear()
            self._reset_round()
            self._bump()

    def join(self, name: str, code: str = "", token: str = "") -> tuple[dict | None, str]:
        with self.lock:
            if token:
                existing = self._by_token(token)
                if existing:
                    existing.last_seen = _now()
                    self._bump()
                    return self._identity(existing), "ok"

            if not self.room_code:
                return None, "no_room"
            if self.fold_code(code) != self.fold_code(self.room_code):
                return None, "bad_code"

            name = " ".join((name or "").split())[:16]
            if not name:
                return None, "bad_name"
            if any(p.name.lower() == name.lower() for p in self.players.values()):
                return None, "taken"

            pid = uuid.uuid4().hex
            actor = self._actor_seq + 1
            self._actor_seq = actor
            player = Player(pid, secrets.token_urlsafe(16), name, actor)
            self.players[pid] = player
            self.order.append(pid)
            # Игра узнаёт об игроке через собственный обратный вызов Photon,
            # поэтому мосту уходит отдельная инструкция.
            self._push({"kind": "join", "actor": actor, "nick": name, "userId": pid})
            return self._identity(player), "ok"

    def _identity(self, player: Player) -> dict:
        return {"pid": player.pid, "token": player.token,
                "name": player.name, "actor": player.actor}

    def leave(self, token: str) -> bool:
        with self.lock:
            player = self._by_token(token)
            if not player:
                return False
            self.players.pop(player.pid, None)
            if player.pid in self.order:
                self.order.remove(player.pid)
            self._push({"kind": "leave", "actor": player.actor})
            return True

    # --- действия телефонов ------------------------------------------------
    def submit_dub(self, token: str, text: str) -> tuple[bool, str]:
        with self.lock:
            player = self.touch(token)
            if not player:
                return False, "unknown_player"
            if proto.phase_for(self.props.get("game_state")) != "write":
                return False, "wrong_phase"
            limit = int(self.props.get("max_dub_length") or 150)
            text = " ".join(str(text or "").split())[:limit]
            if not text:
                return False, "empty"
            player.submitted = text
            self._push({"kind": "event", "code": proto.DUB_SUBMITTED,
                        "content": json.dumps({
                            "playerID": player.pid,
                            "dub": text,
                            "id": uuid.uuid4().hex,
                            "languageCode": self._language_code(),
                        }, ensure_ascii=False)})
            return True, "ok"

    def _language_code(self) -> str:
        """Код языка в том виде, в каком его ждёт игра."""
        return "ru-RU" if self.lang == "ru" else "en-US"

    def submit_vote(self, token: str, ids: list[str]) -> tuple[bool, str]:
        with self.lock:
            player = self.touch(token)
            if not player:
                return False, "unknown_player"
            if proto.phase_for(self.props.get("game_state")) != "vote":
                return False, "wrong_phase"
            allowed = max(1, int(self.props.get("allowed_vote_count") or 1))
            valid = {o["id"] for o in self.options}
            picked = [str(i) for i in ids if str(i) in valid][:allowed]
            if not picked:
                return False, "empty"
            if any(player.pid in o["owners"] for o in self.options if o["id"] in picked):
                return False, "own"
            player.votes = picked
            self._push({"kind": "event", "code": proto.VOTE_SUBMITTED,
                        "content": json.dumps({
                            "votingPlayerID": player.pid,
                            "dubID": ",".join(picked),
                        }, ensure_ascii=False)})
            return True, "ok"

    CONTROLS = {
        "start": proto.CLIENT_TRIGGERED_START,
        "cancel": proto.CLIENT_CANCELLED_START,
        "skip_intro": proto.SKIP_INTRO,
        "next_game": proto.CLIENT_READY_NEXT_GAME,
        "new_room": proto.CLIENT_TRIGGERED_NEW_ROOM,
    }

    def control(self, token: str, action: str) -> tuple[bool, str]:
        with self.lock:
            player = self.touch(token)
            if not player:
                return False, "unknown_player"
            code = self.CONTROLS.get(action)
            if code is None:
                return False, "unknown_action"
            # Игра принимает эти коды от кого угодно, поэтому права проверяем
            # здесь: начинать партию и пропускать вступление может только хост.
            if action == "skip_intro":
                if not self.is_host(player):
                    return False, "not_host"
            elif not self.can_manage(player):
                return False, "not_manager"
            self._push({"kind": "event", "code": code, "content": None})
            return True, "ok"

    def players_needed(self) -> int:
        """Скольких игроков не хватает, чтобы игра согласилась начать.

        Обычно нужно трое, но при меньшем числе игра умеет играть «без очков»:
        реплики показываются, а голосования и счёта нет. Тогда хватит и одного.
        """
        count = len(self.players)
        least = int(self.props.get("min_players") or 3)
        if self.props.get("allow_no_score_mode"):
            least = 1
        return max(0, least - count)

    def no_score_mode(self) -> bool:
        """Игроков меньше обычного минимума — партия пойдёт без голосования."""
        count = len(self.players)
        return bool(self.props.get("allow_no_score_mode")) and \
            0 < count < int(self.props.get("min_players") or 3)

    def host_pid(self) -> str | None:
        """Хост — ведущий, которого назначила игра, а пока его нет — первый вошедший."""
        manager = str(self.props.get("game_manager_id") or "")
        if manager and manager in self.players:
            return manager
        for pid in self.order:
            if pid in self.players:
                return pid
        return None

    def is_host(self, player: Player) -> bool:
        return player.pid == self.host_pid()

    def can_manage(self, player: Player) -> bool:
        """Управлять партией с телефона может только хост, и то если игра разрешает."""
        return bool(self.props.get("client_can_manage")) and self.is_host(player)

    def _public_players(self) -> list[dict]:
        host = self.host_pid()
        return [dict(self.players[p].public(), host=p == host)
                for p in self.order if p in self.players]

    # --- то, что приходит из игры -------------------------------------------
    def take_queue(self) -> list[dict]:
        with self.lock:
            self.bridge_seen = _now()
            items, self.queue = self.queue, []
            if items:
                self._bump()
            return items

    def set_props(self, props: dict) -> None:
        with self.lock:
            self.bridge_seen = _now()
            if not isinstance(props, dict):
                return
            before = self.props.get("game_state")
            for key, value in props.items():
                self.props[key] = value
            if self.props.get("game_state") != before:
                self._on_phase_change()
            self._bump()

    def _on_phase_change(self) -> None:
        self.countdown = False
        phase = proto.phase_for(self.props.get("game_state"))
        if phase == "write":
            for player in self.players.values():
                player.submitted = None
        elif phase == "vote":
            for player in self.players.values():
                player.votes = []

    def from_game(self, code: int, content) -> None:
        """Событие из игры. Разбираем только то, что нужно телефону."""
        with self.lock:
            self.bridge_seen = _now()
            payload = _decode(content)

            if code == proto.ENTER_DUB_TIME:
                self.script = str((payload or {}).get("script", "")) if isinstance(payload, dict) else ""
                for player in self.players.values():
                    player.submitted = None
            elif code == proto.VOTE_TIME:
                self.options = proto.parse_vote_options(
                    payload, int(self.props.get("js_version") or 1))
                for player in self.players.values():
                    player.votes = []
            elif code == proto.NEXT_ROUND and isinstance(payload, dict):
                self.round = payload.get("roundNumber")
            elif code == proto.INDIVIDUAL_ROUND_SCORE and isinstance(payload, dict):
                pid = str(payload.get("userId", ""))
                if pid:
                    self.scores[pid] = payload
            elif code == proto.GAME_OVER_WINNER:
                self.winner = str(payload) if payload is not None else None
            elif code == proto.PLAYER_NAME_CHANGE and isinstance(payload, dict):
                target = self.players.get(str(payload.get("playerID", "")))
                if target:
                    target.name = str(payload.get("name", target.name))[:16]
            elif code == proto.SERVER_TRIGGERED_START:
                self.countdown = True
            elif code == proto.SERVER_CANCELLED_START:
                self.countdown = False
            elif code == proto.DISCONNECT_GAME_OVER:
                self._reset_round()
            elif code in _KICK_CODES:
                self._apply_kick(code, payload)

            self._bump()

    def _reset_round(self) -> None:
        self.countdown = False
        self.script = ""
        self.options = []
        self.winner = None
        self.scores = {}
        for player in self.players.values():
            player.submitted = None
            player.votes = []

    def _apply_kick(self, code: int, payload) -> None:
        reason = _KICK_CODES[code]
        targets: list[str] = []
        if code == proto.KICK_PLAYER and isinstance(payload, dict):
            targets = [str(payload.get("id", ""))]
        elif code == proto.AUDIENCE_DID_NOT_VOTE_KICK and isinstance(payload, dict):
            targets = [str(x) for x in (payload.get("ids") or [])]
        elif code == proto.ALREADY_JOINED_KICK:
            actor = payload
            targets = [p.pid for p in self.players.values() if str(p.actor) == str(actor)]
        elif payload is not None:
            targets = [str(payload)]

        for pid in targets:
            player = self.players.get(pid)
            if player:
                player.kicked = reason

    # --- состояние для телефона ---------------------------------------------
    def phone_state(self, token: str) -> dict:
        with self.lock:
            player = self._by_token(token)
            if not player:
                return {"version": self.version, "known": False,
                        "bridge": self.bridge_alive}

            player.last_seen = _now()
            phase = proto.phase_for(self.props.get("game_state"))
            if player.kicked:
                phase = "kicked"

            # Свой вариант в списке на голосование не показываем.
            options = [
                {"id": o["id"], "dub": o["dub"]}
                for o in self.options if player.pid not in o["owners"]
            ]

            return {
                "version": self.version,
                "known": True,
                "bridge": self.bridge_alive,
                "phase": phase,
                "game_state": self.props.get("game_state"),
                "me": player.public(),
                "players": self._public_players(),
                "script": self.script if phase == "write" else "",
                "submitted": player.submitted,
                "options": options if phase == "vote" else [],
                "voted": player.votes,
                "votes_allowed": max(1, int(self.props.get("allowed_vote_count") or 1)),
                "max_length": int(self.props.get("max_dub_length") or 150),
                "sfx": proto.parse_sfx(self.props.get("inline_sfx")) if phase == "write" else [],
                "max_sfx": _as_int(self.props.get("max_inline_sfx"), -1),
                "round": self.round,
                "winner": self.winner,
                "my_score": self.scores.get(player.pid),
                "can_manage": self.can_manage(player),
                "is_host": self.is_host(player),
                "min_players": int(self.props.get("min_players") or 3),
                "players_needed": self.players_needed(),
                "no_score": self.no_score_mode(),
                "countdown": self.countdown,
                "kicked": player.kicked,
            }

    def host_view(self) -> dict:
        """Короткая сводка для лаунчера."""
        with self.lock:
            return {
                "bridge": self.bridge_alive,
                "players": self._public_players(),
                "game_state": self.props.get("game_state"),
                "phase": proto.phase_for(self.props.get("game_state")),
                "round": self.round,
                "code": self.room_code,
                "pending": len(self.queue),
            }

    def reset(self) -> None:
        with self.lock:
            self.props = dict(proto.DEFAULT_PROPS)
            self.players.clear()
            self.order.clear()
            self._actor_seq = 1
            self.queue.clear()
            self.room_code = ""
            self.room_id = ""
            self._reset_round()
            self._bump()


_LOOKALIKES = str.maketrans("АВЕКМНОРСТУХ", "ABEKMHOPCTYX")

_KICK_CODES = {
    proto.KICK_PLAYER: "kicked_by_host",
    proto.AUDIENCE_DID_NOT_VOTE_KICK: "kicked_inactive",
    proto.ALREADY_JOINED_KICK: "kicked_duplicate",
    proto.AUDIENCE_NOT_ENABLED_KICK: "kicked_no_audience",
    proto.AUDIENCE_FULL_KICK: "kicked_audience_full",
    proto.KICK_INVALID_TWITCH: "kicked_twitch",
    proto.KICK_AUDIENCE_TWITCH: "kicked_twitch_audience",
}


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _decode(content):
    """Полезная нагрузка приходит строкой; JSON разбираем, остальное как есть."""
    if content is None:
        return None
    if isinstance(content, (dict, list, int, float)):
        return content
    text = str(content).strip()
    if not text:
        return None
    if text[0] in "{[":
        try:
            return json.loads(text)
        except ValueError:
            return None
    return text
