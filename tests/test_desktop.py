"""Проверка настоящих Tk-виджетов на временной игре, без Steam и патча игры.

python3 tests/test_desktop.py  (нужен дисплей; в CI: xvfb-run -a ...)
"""
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ld import config, wtd
from ld.server import App
from ld import desktop
from ld.desktop import Button, DesktopWindow


class WindowCase(unittest.TestCase):
    """Окно лаунчера на временной игре."""

    setup_done = True

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="localdub-native-test-")
        self.folder = Path(self.temp.name)
        self.game = self.folder / "Игра с пробелами"
        self.managed = config.managed_dir(self.game)
        self.managed.mkdir(parents=True)
        (self.managed / "Assembly-CSharp.dll").write_bytes(b"MZ fixture original")
        self.cfg = config.Config(self.folder / "config.json")
        self.cfg.update({"game_dir": str(self.game), "setup_done": self.setup_done})
        speech = Mock(active_name="none")
        speech.engine_details.return_value = {}
        self.app = App(self.cfg, speech)
        self.app.on_settings_change = Mock()
        self.patches = [patch("ld.wtd.game_running", return_value=False),
                        patch("ld.wtd.launch_game", return_value=(True, "fixture launch")),
                        patch("ld.wtd.stop_game", return_value=True),
                        patch("ld.net.addresses", return_value=[("fixture", "192.168.1.10")]),
                        patch("ld.desktop.messagebox.showerror")]
        for mock in self.patches:
            mock.start()
        self.win = DesktopWindow(self.app)
        self.wait(lambda: self.win.snapshot is not None)

    def tearDown(self):
        self.wait(lambda: not self.win.busy)
        self.win.close()
        self.win.worker.shutdown(wait=True)
        for mock in reversed(self.patches):
            mock.stop()
        self.temp.cleanup()

    def wait(self, condition, timeout=8):
        until = time.monotonic()+timeout
        while time.monotonic() < until:
            self.win.root.update()
            if condition():
                return
            time.sleep(.02)
        self.fail("Native UI timed out")

    def finish_action(self):
        self.wait(lambda: not self.win.busy)

    @staticmethod
    def children(widget):
        for child in widget.winfo_children():
            yield child
            yield from WindowCase.children(child)

    def screenshot(self, name):
        import os
        output = os.environ.get("LOCALDUB_TEST_SCREENSHOTS")
        if not output:
            return
        from PIL import ImageGrab
        self.win.root.update()
        target = self.win.root
        target.update()
        x, y = target.winfo_rootx(), target.winfo_rooty()
        folder = Path(output)
        folder.mkdir(parents=True, exist_ok=True)
        try:
            ImageGrab.grab(bbox=(x, y, x+target.winfo_width(), y+target.winfo_height())).save(folder/(name+".png"))
        except (OSError, subprocess.CalledProcessError):
            # Под Wayland экран целиком не снять — пробуем само окно через ImageMagick.
            window = int(target.wm_frame(), 16) if target.wm_frame() else target.winfo_id()
            subprocess.run(["import", "-window", hex(window), str(folder/(name+".png"))], check=False)



class NativeFlow(WindowCase):
    def test_patch_start_settings_restore(self):
        self.assertTrue(self.win.patch_button.enabled)
        self.assertFalse(self.win.copy_button.enabled)
        self.screenshot("native-idle")
        def install(game, port, dotnet):
            self.assertEqual(game, str(self.game))
            self.assertEqual(port, self.app.port)
            wtd._refresh_backup(self.managed)
            (self.managed / "Assembly-CSharp.dll").write_bytes(b"MZ LocalDubBridge fixture")
            return True, "fixture patched"
        with patch("ld.wtd.apply_patch", side_effect=install) as installer:
            self.win.patch_button.invoke()
            self.assertTrue(self.win.busy)
            self.assertFalse(self.win.start_button.enabled)
            self.finish_action()
            self.assertEqual(installer.call_count, 1)
        self.assertTrue(self.win.restore_button.enabled)
        self.win.start_button.invoke()
        self.finish_action()
        self.assertTrue(self.app.session_on)
        self.assertFalse(self.win.browse_button.enabled)
        self.assertTrue(self.win.copy_button.enabled)
        self.assertGreater(len(self.win.qr_canvas.find_all()), 20)
        handshake = json.loads((self.managed / "bridge.json").read_text())
        self.assertEqual(handshake["port"], self.app.port)
        self.screenshot("native-running")
        self.win.start_button.invoke()
        self.finish_action()
        self.assertFalse(self.app.session_on)
        self.assertFalse(self.win.copy_button.enabled)
        self.assertEqual(len(self.win.qr_canvas.find_all()), 0)
        self.win.show(desktop.SETTINGS)
        self.assertFalse(any(w.winfo_class() == "Toplevel" for w in self.children(self.win.root)),
                         "настройки должны быть в том же окне")
        self.screenshot("native-settings")
        self.win.language.current(1)
        self.win.fields["room_code"].set("кино")
        self.assertEqual(self.win.fields["room_code"].get(), "КИНО")
        self.win.save_button.invoke()
        self.finish_action()
        self.assertEqual(self.win.page, desktop.HOME)
        self.assertEqual(self.cfg["room_code"], "КИНО")
        self.assertEqual(self.win.room.cget("text"), "КИНО")
        self.assertEqual(self.cfg["lang"], "en")
        self.assertEqual(self.win.patch_button.text, "Update patch")
        self.app.on_settings_change.assert_called_once()
        self.assertEqual(json.loads(self.cfg.path.read_text())["lang"], "en")
        with patch("ld.wtd.remove_patch", return_value=(True, "fixture restored")) as restore:
            self.win.restore_button.invoke()
            self.finish_action()
            restore.assert_called_once_with(str(self.game), self.cfg["dotnet_path"])
        self.win.root.geometry("380x480")
        self.win.root.update()
        self.screenshot("native-compact")
        self.assertLessEqual(self.win.title.winfo_width(), 380)

    def test_failed_patch_and_running_game(self):
        with patch("ld.wtd.apply_patch", return_value=(False, "fixture patch error")):
            self.win.patch_button.invoke()
            self.finish_action()
            self.assertEqual(self.win.feedback.get(), "fixture patch error")
            self.assertTrue(self.win.patch_button.enabled)
        with patch("ld.wtd.game_running", return_value=True), patch("ld.wtd.apply_patch") as installer:
            self.win.install_patch()
            self.finish_action()
            installer.assert_not_called()
            self.assertIn("закройте", self.win.feedback.get())

    def test_bad_room_code_is_rejected(self):
        self.win.show(desktop.SETTINGS)
        self.win.fields["room_code"].set("ab!")
        self.win.save_button.invoke()
        self.assertEqual(self.win.page, desktop.SETTINGS)
        self.assertEqual(self.cfg["room_code"], "")
        desktop.messagebox.showerror.assert_called()


class FirstRun(WindowCase):
    setup_done = False

    def test_skip_setup(self):
        self.assertEqual(self.win.page, desktop.SETUP)
        self.screenshot("native-setup")
        skip = next(w for w in self.children(self.win.root)
                    if isinstance(w, Button) and w.text == "Пропустить настройку")
        skip.invoke()
        self.assertEqual(self.win.page, desktop.HOME)
        self.assertTrue(self.cfg["setup_done"])
        self.assertTrue(json.loads(self.cfg.path.read_text())["setup_done"])

    def test_walk_through_setup(self):
        self.win.next_button.invoke()                  # приветствие
        self.assertEqual(self.win.step, 1)
        self.win.game_path.set(str(self.game))
        self.win.next_button.invoke()                  # папка игры
        self.win.setup_room.set("ки")
        self.win.next_button.invoke()                  # слишком короткий код
        self.assertEqual(self.win.step, 2)
        self.win.setup_room.set("Ёлка")
        self.win.next_button.invoke()
        self.assertEqual(self.win.step, 3)
        self.assertEqual(self.cfg["room_code"], "ЕЛКА")
        self.screenshot("native-setup-done")
        self.win.install_after_setup.set(False)
        self.win.next_button.invoke()
        self.assertEqual(self.win.page, desktop.HOME)
        self.assertTrue(self.cfg["setup_done"])


if __name__ == "__main__":
    unittest.main()
