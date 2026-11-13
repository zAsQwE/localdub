"""Проверка поиска синтезаторов речи.

Главное здесь — отбор настоящих голосов piper. Посторонние `.onnx` попадаются
в каталогах пакетов постоянно, и без проверки приложение считало их голосами.
"""
import os
import io
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ld import tts  # noqa: E402

FAILS = []


def check(label, cond, extra=""):
    print(f"{'OK      ' if cond else 'ПРОВАЛ  '} {label}{(' — ' + str(extra)) if extra else ''}")
    if not cond:
        FAILS.append(label)


def voice(folder: Path, name: str, with_config: bool = True) -> Path:
    path = folder / f"{name}.onnx"
    path.write_bytes(b"onnx")
    if with_config:
        (folder / f"{name}.onnx.json").write_text("{}")
    return path


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        room = Path(td)

        # --- что считается голосом ---
        good = voice(room, "ru_RU-dmitri-medium")
        stray = voice(room, "logreg_iris", with_config=False)
        check("голос с файлом настроек распознан", tts.PiperEngine.is_voice(good))
        check("посторонний .onnx голосом не считается", not tts.PiperEngine.is_voice(stray))

        engine = tts.PiperEngine(extra_dirs=[room])
        names = [v.name for v in engine.voices(refresh=True)]
        check("в списке только настоящие голоса", names == ["ru_RU-dmitri-medium.onnx"], names)

        # --- подбор по языку ---
        voice(room, "en_US-lessac-medium")
        engine = tts.PiperEngine(extra_dirs=[room])
        engine.voices(refresh=True)
        check("русский голос подбирается по имени файла",
              Path(engine.model_for("ru")).name == "ru_RU-dmitri-medium.onnx", engine.model_for("ru"))
        check("английский тоже",
              Path(engine.model_for("en")).name == "en_US-lessac-medium.onnx", engine.model_for("en"))
        check("язык из имени файла", tts.PiperEngine.voice_language(good) == "ru")

        # --- заданная вручную модель важнее найденной ---
        manual = voice(room, "мой-голос")
        engine = tts.PiperEngine(models={"ru": str(manual)}, extra_dirs=[room])
        check("указанная руками модель побеждает",
              engine.model_for("ru") == str(manual), engine.model_for("ru"))

        # --- где ищем команду pip ---
        dirs = tts.PiperEngine.script_dirs()
        check("каталоги pip перечислены без повторов",
              len(dirs) == len({str(d) for d in dirs}), [str(d) for d in dirs])
        check("пользовательский каталог входит в список",
              any(str(d).endswith(("bin", "Scripts")) for d in dirs), [str(d) for d in dirs][:3])

        # Команда не в PATH, но лежит в каталоге pip — должна найтись.
        scripts = room / "scripts"
        scripts.mkdir()
        (scripts / "localdub-проба").write_bytes(b"#!/bin/sh\n")
        saved = tts.PiperEngine.script_dirs
        try:
            tts.PiperEngine.script_dirs = staticmethod(lambda: [scripts])
            check("команда находится вне PATH",
                  tts.PiperEngine.find_script("localdub-проба") is not None)
            check("несуществующая команда не выдумывается",
                  tts.PiperEngine.find_script("localdub-нет-такой") is None)
        finally:
            tts.PiperEngine.script_dirs = saved

        # --- перебор интерпретаторов ---
        cands = tts.PiperEngine.python_candidates()
        check("текущий интерпретатор проверяется первым",
              cands and cands[0] == [sys.executable], cands[:2])
        check("интерпретаторы не повторяются",
              len(cands) == len({tuple(c) for c in cands}), cands)

        # Готовый Piper CLI скачивает модель без Python-модуля piper.
        downloads = room / "downloaded"
        engine.installed = True
        engine.python = None
        def response(body, length=None):
            stream = io.BytesIO(body)
            stream.headers = {"Content-Length": str(len(body) if length is None else length)}
            return stream
        with patch("ld.tts.urllib.request.urlopen", side_effect=[response(b"{}"), response(b"cut", 100)]):
            ok, note = engine.download("ru", downloads)
        check("оборванная загрузка голоса отклоняется", not ok, note)
        target = downloads / "ru_RU-dmitri-medium.onnx"
        check("оборванная модель не появляется в списке", not target.exists() and not list(downloads.glob("*.part")))
        with patch("ld.tts.urllib.request.urlopen", return_value=response(b"model")) as request:
            ok, note = engine.download("ru", downloads)
        check("повторная загрузка докачивает только отсутствующую модель", ok and request.call_count == 1 and target.read_bytes() == b"model", note)
        with patch("ld.tts.urllib.request.urlopen") as request:
            ok, note = engine.download("ru", downloads)
        check("готовый голос повторно не скачивается", ok and not request.called, note)

        # --- espeak ---
        speech = tts.Speech(room / "cache", engine="none")
        check("без озвучки движок не выбирается", speech.active_name == "none")
        check("папка для скачанных голосов задана",
              speech.voice_store.name == "piper-voices", speech.voice_store)
        check("очистка текста убирает управляющие символы",
              tts.clean("привет\x07\x00  мир") == "привет мир", repr(tts.clean("привет\x07\x00  мир")))
        check("длина ограничена", len(tts.clean("я" * 500)) == tts.MAX_CHARS)

    print(f"\nпровалов: {len(FAILS)}")
    for f in FAILS:
        print("  -", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
