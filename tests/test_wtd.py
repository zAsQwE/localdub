"""Проверка слоя связи с игрой.

Файлы установленной игры здесь не трогаются: патч и запуск проверяются только
на том, что они корректно отказывают, когда игры нет.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ld import config, wtd  # noqa: E402

FAILS = []


def check(label, cond, extra=""):
    print(f"{'OK      ' if cond else 'ПРОВАЛ  '} {label}{(' — ' + str(extra)) if extra else ''}")
    if not cond:
        FAILS.append(label)


def check_batch_files() -> None:
    """Файлы .bat должны быть с CRLF и без BOM, иначе cmd ломается на метках."""
    root = Path(__file__).resolve().parents[1]
    for name in ("run.bat", "install-windows.bat"):
        path = root / name
        if not path.is_file():
            check(f"{name} на месте", False)
            continue
        raw = path.read_bytes()
        lone = raw.count(b"\n") - raw.count(b"\r\n")
        check(f"{name}: переводы строк CRLF", lone == 0, f"одиночных LF: {lone}")
        check(f"{name}: без BOM", not raw.startswith(b"\xef\xbb\xbf"))
        check(f"{name}: кодировка объявлена", b"chcp 65001" in raw)


def main() -> int:
    check_batch_files()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # --- распознавание папки игры ---
        check("пустая папка — не игра", config.is_game_dir(tmp) is False)
        fake = tmp / "WhatTheDub/WhatTheDub_Data/Managed"
        fake.mkdir(parents=True)
        check("папка без сборки — не игра", config.is_game_dir(tmp / "WhatTheDub") is False)
        (fake / "Assembly-CSharp.dll").write_bytes(b"MZ" + b"\0" * 64)
        check("папка со сборкой — игра", config.is_game_dir(tmp / "WhatTheDub") is True)
        check("путь к Managed собирается верно",
              config.managed_dir(tmp / "WhatTheDub") == fake, config.managed_dir(tmp / "WhatTheDub"))

        # --- состояние патча по прямому признаку ---
        check("оригинальная сборка -> off",
              wtd._reference_present(fake) == wtd.OFF, wtd._reference_present(fake))
        (fake / "Assembly-CSharp.dll").write_bytes(b"MZ...LocalDubBridge...")
        check("сборка со ссылкой на мост -> on",
              wtd._reference_present(fake) == wtd.ON, wtd._reference_present(fake))
        check("несуществующая папка -> unknown",
              wtd.patch_status(tmp / "нет") == wtd.UNKNOWN, wtd.patch_status(tmp / "нет"))
        check("пустой путь -> unknown", wtd.patch_status("") == wtd.UNKNOWN)

        # --- отказы без игры ---
        ok, note = wtd.apply_patch(tmp / "нет-игры")
        check("патч без игры отказывает", ok is False and "не найдена" in note, note)
        ok, note = wtd.remove_patch(tmp / "нет-игры")
        check("откат без игры отказывает", ok is False, note)

        # --- инструменты и готовые файлы ---
        kit = wtd.tools()
        check("состав инструментов определяется",
              isinstance(kit.missing, list) and kit.ready == (not kit.missing),
              f"ready={kit.ready} missing={kit.missing}")
        check("мост лежит готовым в проекте", kit.have_bridge, wtd.bridge_source())
        check("патчер лежит готовым в проекте", kit.have_patcher, wtd.patcher_path())
        check("сборка на машине игрока не нужна",
              "mono" not in kit.missing and "dotnet-sdk" not in kit.missing, kit.missing)

        # --- какая сборка установлена ---
        def write(path, russian=False, patched=False):
            body = b"MZ" + b"\0" * 64
            if russian:
                phrases = ("Ожидаем, когда хост начнёт игру. Всего голосов. "
                           "Выберите следующий клип, введя номер. Перевод: Tolma4 Team.")
                body += phrases.encode("utf-16-le")
            else:
                # В оригинале кириллица есть, но только отдельными словами.
                body += "Русский Latvietis".encode("utf-16-le")
            if patched:
                body += b"LocalDubBridge"
            path.write_bytes(body)

        probe = tmp / "probe"
        probe.mkdir()
        write(probe / "Assembly-CSharp.dll")
        info = wtd.inspect_file(probe / "Assembly-CSharp.dll")
        check("оригинал распознан", info["flavour"] == wtd.ORIGINAL,
              f'{info["flavour"]} фраз={info["phrases"]}')
        check("оригинал без патча", info["patched"] is False)

        write(probe / "Assembly-CSharp.dll", russian=True)
        info = wtd.inspect_file(probe / "Assembly-CSharp.dll")
        check("русская версия распознана", info["flavour"] == wtd.RUSSIAN,
              f'{info["flavour"]} фраз={info["phrases"]} маркеров={info["markers"]}')

        write(probe / "Assembly-CSharp.dll", russian=True, patched=True)
        info = wtd.inspect_file(probe / "Assembly-CSharp.dll")
        check("патч поверх русской виден", info["patched"] and info["flavour"] == wtd.RUSSIAN,
              f'patched={info["patched"]} {info["flavour"]}')
        check("несуществующий файл не роняет",
              wtd.inspect_file(probe / "нет.dll")["exists"] is False)

        # --- копия оригинала обновляется, а не берётся один раз ---
        game = tmp / "WhatTheDub"
        managed = config.managed_dir(game)
        write(managed / "Assembly-CSharp.dll")               # сначала оригинал
        ok, note = wtd._refresh_backup(managed)
        check("копия снята с оригинала", ok, note)
        check("копия — оригинальная",
              wtd.inspect_file(managed / wtd.BACKUP_NAME)["flavour"] == wtd.ORIGINAL)

        write(managed / "Assembly-CSharp.dll", russian=True)  # поставили перевод
        ok, note = wtd._refresh_backup(managed)
        check("копия обновилась под перевод", ok, note)
        check("теперь копия русская — перевод не потеряется",
              wtd.inspect_file(managed / wtd.BACKUP_NAME)["flavour"] == wtd.RUSSIAN)

        write(managed / "Assembly-CSharp.dll", russian=True, patched=True)
        before = wtd.inspect_file(managed / wtd.BACKUP_NAME)["sha256"]
        ok, _ = wtd._refresh_backup(managed)
        after = wtd.inspect_file(managed / wtd.BACKUP_NAME)["sha256"]
        check("пропатченную сборку в копию не пишем", ok and before == after)

        report = wtd.game_report(game)
        check("сводка: патч виден и версия русская",
              report["patch"] == wtd.ON and report["flavour"] == wtd.RUSSIAN, report)

        (managed / wtd.BACKUP_NAME).unlink()
        ok, note = wtd._refresh_backup(managed)
        check("патч без копии — отказ", ok is False and "копии оригинала нет" in note, note)
        check("сводка предупреждает про пропавшую копию",
              wtd.game_report(game)["notice"] == "no_backup", wtd.game_report(game)["notice"])

        # --- где брать dotnet ---
        info = wtd.system_info()
        check("система определяется", info["os"] in ("Linux", "Windows", "macOS"), info)
        check("версия Python в сводке", info["python"].count(".") == 2, info["python"])

        fake_dotnet = tmp / "runtime" / ("dotnet.exe" if os.name == "nt" else "dotnet")
        fake_dotnet.parent.mkdir(parents=True, exist_ok=True)
        fake_dotnet.write_bytes(b"MZ")
        check("указанный вручную путь используется",
              wtd.dotnet_exe(str(fake_dotnet)) == str(fake_dotnet), wtd.dotnet_exe(str(fake_dotnet)))
        check("можно указать папку, а не файл",
              wtd.dotnet_exe(str(fake_dotnet.parent)) == str(fake_dotnet),
              wtd.dotnet_exe(str(fake_dotnet.parent)))
        check("несуществующий путь не мешает найти обычный",
              wtd.dotnet_exe(str(tmp / "нет")) == wtd.dotnet_exe(""),
              wtd.dotnet_exe(str(tmp / "нет")))
        check("в сводке виден заданный dotnet",
              wtd.system_info(str(fake_dotnet))["dotnet"] == str(fake_dotnet))

        # --- порт озвучки запекается в патч ---
        # Копию вернули на место: без неё сводка говорит только про её пропажу.
        write(managed / wtd.BACKUP_NAME, russian=True)
        check("сводка знает про порт озвучки", "tts_port" in wtd.game_report(game),
              sorted(wtd.game_report(game))[:4])
        wtd._write_meta(managed, {"base_flavour": wtd.RUSSIAN, "redirects": 147,
                                  "tts_port": 8080})
        check("тот же порт — замечаний нет",
              wtd.game_report(game, 8080)["notice"] == "", wtd.game_report(game, 8080)["notice"])
        check("порт сменился — просим встроить заново",
              wtd.game_report(game, 9000)["notice"] == "tts_port_changed",
              wtd.game_report(game, 9000)["notice"])

        # --- рукопожатие лежит рядом с мостом, а не в проекте ---
        path = wtd.write_handshake(managed, 9123, "ru", "секрет-токен")
        payload = json.loads(path.read_text("utf-8"))
        check("рукопожатие записано в папку игры", path.parent == managed, path)
        check("в нём порт, язык и токен",
              payload == {"port": 9123, "lang": "ru", "token": "секрет-токен",
                          "verbose": False, "room_code": "", "join_text": "",
                          "join_qr": []}, payload)
        payload = json.loads(wtd.write_handshake(managed, 9123, "ru", "т",
                                                 room_code="КИНО").read_text("utf-8"))
        check("свой код комнаты передан мосту", payload["room_code"] == "КИНО", payload)
        payload = json.loads(wtd.write_handshake(managed, 9123, "ru", "т", join_text="192.168.1.5:8080",
                                                 join_qr=["101", "010"]).read_text("utf-8"))
        check("адрес и QR для экрана игры переданы мосту",
              payload["join_text"] == "192.168.1.5:8080" and payload["join_qr"] == ["101", "010"], payload)

        check("без моста в игре обновлять нечего", wtd.sync_bridge(managed) is False)
        (managed / wtd.BRIDGE_DLL).write_bytes(b"old bridge")
        check("устаревший мост обновляется", wtd.sync_bridge(managed) is True)
        check("теперь он совпадает с нашим",
              wtd.sha256(managed / wtd.BRIDGE_DLL) == wtd.sha256(wtd.bridge_source()))
        check("свежий мост не трогаем", wtd.sync_bridge(managed) is False)
        payload = json.loads(
            wtd.write_handshake(managed, 1, "en", "t", verbose=True).read_text("utf-8"))
        check("подробный лог включается через рукопожатие", payload["verbose"] is True, payload)
        path.unlink(missing_ok=True)

        # --- лог моста ---
        wtd.clear_bridge_log(managed)
        check("лог моста пуст, когда игра не запускалась", wtd.bridge_log(managed) == "",
              repr(wtd.bridge_log(managed))[:40])
        (managed / "bridge.log").write_text("\n".join(f"строка {i}" for i in range(60)))
        tail = wtd.bridge_log(managed).splitlines()
        check("лог обрезается до последних строк", len(tail) == 40 and tail[-1] == "строка 59",
              f"{len(tail)} строк")
        wtd.clear_bridge_log(managed)
        check("лог убирается", not (managed / "bridge.log").exists())

    print(f"\nпровалов: {len(FAILS)}")
    for f in FAILS:
        print("  -", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
