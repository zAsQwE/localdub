"""Протокол обмена между игрой и телефонами.

Коды и имена сняты с enum `LocalPlayWebGameManager/LocalPlayEventType` в
`Assembly-CSharp.dll` и сверены с клиентом playwtd.com. Полезная нагрузка —
строка JSON, которую игра разбирает Unity-шным `JsonUtility`: он берёт только
публичные поля и молча выбрасывает незнакомые ключи, поэтому имена важны.
"""
from __future__ import annotations

# --- события, которые игра ОТПРАВЛЯЕТ телефонам ---------------------------
PLAYING_VIDEO_TO_DUB = 0
ENTER_DUB_TIME = 1            # {"script": "<html>"}
PLAYING_BACK_DUBS = 3
VOTE_TIME = 4                 # {id: {"id", "playerIDs", "dub"}} либо {pid: "текст"}
SHOWING_ROUND_SCORES = 6
DISCONNECT_GAME_OVER = 7
PLAYER_NAME_CHANGE = 8        # {"playerID", "name"}
GAME_OVER_WINNER = 9          # playerID строкой
AUDIENCE_NOT_ENABLED_KICK = 10
READY_FOR_NEXT_GAME = 13
ALREADY_JOINED_KICK = 15      # номер актёра
AUDIENCE_FULL_KICK = 16
PLAYING_INSTRUCTIONS = 17
SERVER_TRIGGERED_START = 19
SERVER_CANCELLED_START = 20
INDIVIDUAL_ROUND_SCORE = 21   # {"userId", "rank0Votes", "rank1Votes", "rank2Votes", "audienceVotes"}
NEXT_ROUND = 22               # {"roundNumber": N}
INTRO_NO_INSTRUCTIONS = 23
KICK_PLAYER = 100             # {"id": playerID}
AUDIENCE_DID_NOT_VOTE_KICK = 101   # {"ids": [...]}
KICK_INVALID_TWITCH = 102
KICK_AUDIENCE_TWITCH = 103
MODERATION = 151

# --- события, которые игра ПРИНИМАЕТ от телефонов --------------------------
# Именно эти восемь кодов разбирает `LocalPlayWebGameManager.OnEvent`.
DUB_SUBMITTED = 2             # {"playerID", "dub", "id", "languageCode"}
VOTE_SUBMITTED = 5            # {"votingPlayerID", "dubID"}
CLIENT_TRIGGERED_START = 11   # null
CLIENT_CANCELLED_START = 12   # null
CLIENT_READY_NEXT_GAME = 13   # null
CLIENT_TRIGGERED_NEW_ROOM = 14  # null
SKIP_INTRO = 18               # null

ACCEPTED_BY_GAME = frozenset({
    DUB_SUBMITTED, VOTE_SUBMITTED, CLIENT_TRIGGERED_START, CLIENT_CANCELLED_START,
    CLIENT_READY_NEXT_GAME, CLIENT_TRIGGERED_NEW_ROOM, SKIP_INTRO, MODERATION,
})

# --- состояния партии (свойство комнаты `game_state`) ----------------------
WAITING_FOR_PLAYERS = 0
PLAYING_VIDEO = 1
SUBMITTING_DUBS = 2
PLAYING_BACK = 3
VOTING = 4
SHOWING_RESULTS = 5
WAITING_NEXT_GAME = 6
PLAYING_INTRO = 7
NEXT_ROUND_STATE = 8

# Во что превращается состояние на экране телефона.
PHASE_BY_STATE = {
    WAITING_FOR_PLAYERS: "lobby",
    PLAYING_VIDEO: "watch",
    SUBMITTING_DUBS: "write",
    PLAYING_BACK: "watch",
    VOTING: "vote",
    SHOWING_RESULTS: "scores",
    WAITING_NEXT_GAME: "next",
    PLAYING_INTRO: "intro",
    NEXT_ROUND_STATE: "intro",
}

# --- свойства комнаты, которые публикует игра ------------------------------
DEFAULT_PROPS = {
    "game_state": WAITING_FOR_PLAYERS,
    "script": "",
    "current_round": None,
    "current_round_dubs": {},
    "player_ids": [],
    "game_manager_id": None,
    "client_can_manage": False,
    "min_players": 3,
    "max_dub_length": 150,
    "allowed_vote_count": 1,
    "inline_sfx": [],
    "max_inline_sfx": -1,
    "js_version": 1,
    "requires_twitch": False,
    "allow_no_score_mode": False,
}


def phase_for(game_state) -> str:
    try:
        return PHASE_BY_STATE.get(int(game_state), "wait")
    except (TypeError, ValueError):
        return "wait"


# В сборке игры две подписи разъезжаются с тем, что она потом ждёт обратно.
# Официальный клиент правит их у себя — делаем так же, иначе эти два звука
# просто не сработают.
_SFX_FIXES = {
    "(Robot Noise)": "(Robotic Noise)",
    "(Baby Crying)": "(Baby Cries)",
}
_SFX_TITLE_FIXES = {"Toilet Flushed": "Toilet Flushes"}


def parse_sfx(payload) -> list[dict]:
    """Приводит список звуков из свойств комнаты к виду для телефона."""
    if not isinstance(payload, (list, tuple)):
        return []
    sounds = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        replacement = str(item.get("replacement") or "").strip()
        display = str(item.get("display") or replacement).strip()
        if not replacement:
            continue
        sounds.append({
            "display": _SFX_TITLE_FIXES.get(display, display),
            "replacement": _SFX_FIXES.get(replacement, replacement),
        })
    return sounds


def parse_vote_options(payload, js_version: int = 1) -> list[dict]:
    """Приводит оба формата списка на голосование к одному виду.

    В старом формате это `{playerID: "текст"}`, в новом —
    `{dubId: {"id", "playerIDs", "dub"}}`.
    """
    if not isinstance(payload, dict):
        return []
    options = []
    for key, value in payload.items():
        if isinstance(value, dict):
            options.append({
                "id": str(value.get("id") or key),
                "dub": str(value.get("dub") or ""),
                "owners": [str(x) for x in (value.get("playerIDs") or [])],
            })
        else:
            options.append({"id": str(key), "dub": str(value), "owners": [str(key)]})
    return options
