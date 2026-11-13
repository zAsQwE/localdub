"""Окно LocalDub на Tkinter в стиле LocalBox.

Всё живёт в одном окне: главный экран, настройки и мастер первого запуска
подменяют друг друга, как экраны в LocalBox. HTTP нужен только телефонам
и мосту в игре.
"""
from __future__ import annotations

import queue
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import filedialog, font, messagebox, ttk

from . import config, i18n, qr, wtd

# --- палитра LocalBox -----------------------------------------------------
BG, CARD, FIELD = "#090b0f", "#10141b", "#191f29"
FG, MUTED, ACC, ACC_HOVER = "#f0f2f6", "#929bab", "#deb45c", "#edc878"
GREEN, BORDER, DANGER, SHADOW = "#75cba1", "#242c39", "#e0533f", "#58492e"

BODY_FACES = ("Inter", "Segoe UI", "SF Pro Display", "DejaVu Sans")
MONO_FACES = ("Cascadia Code", "Consolas", "DejaVu Sans Mono")

HOME, SETTINGS, SETUP = "home", "settings", "setup"
SETUP_STEPS = 4

VOICE_KEYS = ("auto", "piper", "espeak", "none")


class Button(tk.Canvas):
    """Скруглённая кнопка на Canvas — одинаковая на Windows и Linux, как в LocalBox."""

    def __init__(self, parent, text, command, *, width=None, height=40, radius=12,
                 fill=FIELD, hover=BORDER, foreground=FG, face=("TkDefaultFont", 10, "bold"),
                 shadow=False):
        self.face = face
        if width is None:
            width = font.Font(font=face).measure(text) + 40
        self._height, self._radius, self._shadow = height, radius, shadow
        self._fill, self._hover, self._foreground = fill, hover, foreground
        super().__init__(parent, width=width, height=height + (8 if shadow else 0),
                         bg=parent.cget("bg"), highlightthickness=0, bd=0,
                         takefocus=True, cursor="hand2")
        self.text, self.command = text, command
        self.enabled, self.inside = True, False
        self.bind("<Configure>", self.draw)
        self.bind("<Enter>", lambda _: self._hovered(True))
        self.bind("<Leave>", lambda _: self._hovered(False))
        self.bind("<FocusIn>", self.draw)
        self.bind("<FocusOut>", self.draw)
        for event in ("<ButtonRelease-1>", "<Return>", "<space>"):
            self.bind(event, self.invoke)

    @staticmethod
    def _shape(x1, y1, x2, y2, r):
        return (x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
                x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
                x1, y2, x1, y2 - r, x1, y1 + r, x1, y1)

    def _hovered(self, inside):
        self.inside = inside
        self.draw()

    def draw(self, _event=None):
        self.delete("all")
        width, h, r = max(20, self.winfo_width()), self._height, self._radius
        if self._shadow and self.enabled:
            self.create_polygon(self._shape(8, 8, width - 8, h + 7, r), smooth=True,
                                fill=SHADOW, outline="")
        fill = self._hover if self.inside and self.enabled else self._fill
        if not self.enabled:
            fill = FIELD
        self.create_polygon(self._shape(1, 1, width - 1, h - 1, r), smooth=True,
                            fill=fill, outline="")
        if self.focus_get() is self:
            self.create_polygon(self._shape(3, 3, width - 3, h - 3, max(2, r - 2)), smooth=True,
                                fill="", outline=self._foreground, width=1)
        self.create_text(width / 2, h / 2, text=self.text, font=self.face,
                         fill=self._foreground if self.enabled else MUTED)

    def set(self, *, text=None, enabled=None):
        if text is not None:
            self.text = text
        if enabled is not None:
            self.enabled = bool(enabled)
            self.configure(cursor="hand2" if enabled else "arrow")
        self.draw()

    def set_colors(self, fill, hover, foreground=FG):
        self._fill, self._hover, self._foreground = fill, hover, foreground
        self.draw()

    def invoke(self, _event=None):
        if self.enabled:
            self.command()
        return "break"


class DesktopWindow:
    def __init__(self, app, root=None):
        self.app, self.cfg = app, app.cfg
        self.root = root or tk.Tk()
        self.root.title("LocalDub")
        self.root.configure(bg=BG)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        width, height = min(660, sw - 40), min(780, sh - 80)
        self.root.geometry(f"{width}x{height}+{max(0, (sw - width) // 2)}+{max(0, (sh - height) // 2)}")
        self.root.minsize(min(380, width), min(460, height))

        families = set(font.families(self.root))
        self.face = next((f for f in BODY_FACES if f in families), "TkDefaultFont")
        self.mono = next((f for f in MONO_FACES if f in families), "TkFixedFont")
        self.root.option_add("*Font", (self.face, 10))
        self._style()

        self.events = queue.Queue()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="localdub")
        self.busy = self.polling = self.closed = False
        self.snapshot = None
        self._path_job = None
        self._qr_url = None
        self._wrapping: list[tuple[tk.Label, int]] = []
        self.feedback = tk.StringVar(self.root, self.tr("Готово к запуску", "Ready to go"))
        self._feedback_trace = None

        # Первый запуск — мастер; его можно пропустить целиком.
        self.page = HOME if self.cfg["setup_done"] else SETUP
        self.step = 0
        self.tab = 0
        self.install_after_setup = tk.BooleanVar(self.root, True)

        self._build()
        self.root.bind_all("<MouseWheel>", self._wheel)
        self.root.bind_all("<Button-4>", self._wheel)
        self.root.bind_all("<Button-5>", self._wheel)
        self.root.bind("<Escape>", lambda _e: self.show(HOME) if self.page == SETTINGS else None)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._drain_job = self.root.after(50, self._drain)
        self._refresh_job = self.root.after(100, self.refresh)

    # --- стиль и мелкие виджеты ----------------------------------------------
    def tr(self, ru, en):
        return en if self.cfg["lang"] == "en" else ru

    def _style(self):
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=FG, fieldbackground=FIELD, font=(self.face, 10))
        st.configure("TEntry", fieldbackground=FIELD, foreground=FG, insertcolor=FG,
                     bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, padding=10)
        st.map("TEntry", bordercolor=[("focus", ACC)], lightcolor=[("focus", ACC)],
               darkcolor=[("focus", ACC)], fieldbackground=[("disabled", CARD)],
               foreground=[("disabled", MUTED)])
        st.configure("Card.TCheckbutton", background=CARD, foreground=FG, indicatorbackground=FIELD,
                     indicatorforeground=BG, indicatorbordercolor=BORDER, padding=(0, 4))
        st.map("Card.TCheckbutton", background=[("active", CARD)], foreground=[("!disabled", FG)],
               indicatorbackground=[("selected", ACC), ("!selected", FIELD)])
        st.configure("TCombobox", fieldbackground=FIELD, background=FIELD, foreground=FG,
                     arrowcolor=FG, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, padding=8)
        st.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", FG)],
               selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", FG)],
               bordercolor=[("focus", ACC)])
        st.configure("Vertical.TScrollbar", background=BORDER, troughcolor=BG,
                     bordercolor=BG, lightcolor=BORDER, darkcolor=BORDER,
                     arrowcolor=MUTED, borderwidth=0, arrowsize=12)
        st.map("Vertical.TScrollbar", background=[("active", MUTED)])
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACC)
        self.root.option_add("*TCombobox*Listbox.selectForeground", BG)

    def label(self, parent, text="", *, muted=False, size=10, bold=False, mono=False,
              color=None, wrap=None, center=False, **kwargs):
        """Подпись. `wrap` — отступ от ширины окна, если текст должен переноситься."""
        widget = tk.Label(parent, text=text, bg=parent.cget("bg"),
                          fg=color or (MUTED if muted else FG),
                          font=(self.mono if mono else self.face, size, "bold" if bold else "normal"),
                          anchor="center" if center else "w",
                          justify="center" if center else "left", **kwargs)
        if wrap is not None:
            self._wrapping.append((widget, wrap))
        return widget

    def eyebrow(self, parent, text):
        """Мелкая серая надпись над блоком: «АДРЕС ДЛЯ ИГРОКОВ»."""
        return self.label(parent, text.upper(), muted=True, size=9, bold=True)

    def card_title(self, parent, text):
        return self.label(parent, text, size=14, bold=True)

    def button(self, parent, text, command, *, accent=False, big=False, width=None, height=38, radius=10):
        """Обычная кнопка — тёмная; `accent` — золотая; `big` — главная, с тенью."""
        if big:
            return Button(parent, text, command, width=width or 290, height=54, radius=14,
                          fill=ACC, hover=ACC_HOVER, foreground=BG,
                          face=(self.face, 12, "bold"), shadow=True)
        if accent:
            return Button(parent, text, command, width=width or 174, height=46, radius=12,
                          fill=ACC, hover=ACC_HOVER, foreground=BG, face=(self.face, 10, "bold"))
        return Button(parent, text, command, width=width, height=height, radius=radius,
                      face=(self.face, 10, "bold"))

    def panel(self, parent, padding=20, pady=(0, 12), expand=False):
        """Карточка в рамке, как в LocalBox."""
        border = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
        border.pack(fill="both" if expand else "x", expand=expand, pady=pady)
        body = tk.Frame(border, bg=CARD, padx=padding, pady=padding)
        body.pack(fill="both", expand=True)
        return body

    def entry(self, parent, value, **kwargs):
        widget = ttk.Entry(parent, textvariable=value, **kwargs)
        widget.pack(fill="x")
        return widget

    def combo(self, parent, values, index):
        widget = ttk.Combobox(parent, values=values, state="readonly")
        widget.current(index)
        widget.pack(fill="x")
        return widget

    def field_label(self, parent, text, first=False):
        self.label(parent, text).pack(anchor="w", pady=(0 if first else 12, 5))

    def hint(self, parent, text):
        widget = self.label(parent, text, muted=True, size=9, wrap=90)
        widget.pack(fill="x", pady=(4, 0))
        return widget

    # --- каркас окна -----------------------------------------------------------
    def _build(self):
        """Собирает окно заново: при смене экрана, шага мастера или языка."""
        for child in self.root.winfo_children():
            child.destroy()
        self._wrapping = []
        self._qr_url = None

        outer = tk.Frame(self.root, bg=BG, padx=28, pady=20)
        outer.pack(fill="both", expand=True)
        self._header(outer)
        self._footer(outer)
        bar = tk.Frame(outer, bg=BG)
        bar.pack(fill="x", side="bottom", pady=(14, 0))

        holder = tk.Frame(outer, bg=BG)
        holder.pack(fill="both", expand=True, pady=(18, 0))
        self.canvas = tk.Canvas(holder, bg=BG, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(holder, orient="vertical", command=self.canvas.yview)
        self.canvas.pack(side="left", fill="both", expand=True)
        body = tk.Frame(self.canvas, bg=BG)
        item = self.canvas.create_window(0, 0, anchor="nw", window=body)

        def scrolled(first, last):
            # Полоса прокрутки появляется, только когда содержимое не помещается.
            # Обратно до пересборки экрана не прячем: полоса сужает страницу,
            # текст переносится иначе — и показ с прятанием зациклились бы.
            if (float(first) > 0 or float(last) < 1) and not scrollbar.winfo_ismapped():
                scrollbar.pack(side="right", fill="y", padx=(8, 0), before=self.canvas)
            scrollbar.set(first, last)
        self.canvas.configure(yscrollcommand=scrolled)

        def settle():
            # Один раз после отрисовки убираем полосу, если всё поместилось.
            if scrollbar.winfo_exists() and self.canvas.yview() == (0.0, 1.0):
                scrollbar.pack_forget()
        self.root.after(200, settle)
        body.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

        def resized(event):
            self.canvas.itemconfigure(item, width=event.width)
            for widget, margin in self._wrapping:
                if widget.winfo_exists():
                    widget.configure(wraplength=max(200, event.width - margin))
        self.canvas.bind("<Configure>", resized)

        {HOME: self._home, SETTINGS: self._settings, SETUP: self._setup}[self.page](body, bar)
        if not bar.winfo_children():
            bar.pack_forget()
        if self.snapshot:
            self.render(self.snapshot)

    def _header(self, outer):
        header = tk.Frame(outer, bg=BG)
        header.pack(fill="x")
        if self.page == SETTINGS:
            self.label(header, self.tr("Настройки", "Settings"), size=22, bold=True).pack(side="left")
            self.button(header, self.tr("← Назад", "← Back"), lambda: self.show(HOME),
                        width=100, height=40, radius=12).pack(side="right")
            return
        brand = tk.Frame(header, bg=BG)
        brand.pack(side="left", anchor="nw")
        self.label(brand, "LocalDub", size=21, bold=True).pack(anchor="w")
        self.label(brand, self.tr("What The Dub? в вашей сети", "What The Dub? on your network"),
                   muted=True).pack(anchor="w")
        if self.page == SETUP:
            Button(header, self.tr("Пропустить настройку", "Skip setup"), self.skip_setup,
                   height=42, radius=12, face=(self.face, 10, "bold")).pack(side="right")
        else:
            self.settings_button = self.button(header, self.tr("Настройки", "Settings"),
                                               lambda: self.show(SETTINGS), width=112, height=42, radius=12)
            self.settings_button.pack(side="right")

    def _footer(self, outer):
        """Строка состояния внизу: длинное сообщение обрезается многоточием."""
        footer = tk.Frame(outer, bg=BG)
        footer.pack(fill="x", side="bottom", pady=(12, 0))
        line = tk.Label(footer, bg=BG, fg=MUTED, anchor="w", font=(self.face, 9))
        line.pack(fill="x")
        measure = font.Font(font=line.cget("font"))

        def render(*_):
            if not line.winfo_exists():
                return
            message = self.feedback.get().replace("\n", " ")
            width = max(200, footer.winfo_width() - 8)
            if measure.measure(message) > width:
                while message and measure.measure(message + "…") > width:
                    message = message[:-1]
                message += "…"
            line.configure(text=message)
        if self._feedback_trace:
            self.feedback.trace_remove("write", self._feedback_trace)
        self._feedback_trace = self.feedback.trace_add("write", render)
        footer.bind("<Configure>", render)
        self.feedback_label = line

    def _wheel(self, event):
        first, last = self.canvas.yview()
        if first <= 0 and last >= 1:
            return  # всё и так помещается
        if event.num == 4:
            steps = -1
        elif event.num == 5:
            steps = 1
        else:
            steps = -int(event.delta / 120) or (-1 if event.delta > 0 else 1)
        self.canvas.yview_scroll(steps, "units")

    def show(self, page):
        if page == self.page or self.busy:
            return
        self.save_game()
        self.page = page
        self._build()

    # --- главный экран ---------------------------------------------------------
    def _home(self, body, _bar):
        hero = self.panel(body, 20)
        top = tk.Frame(hero, bg=CARD)
        top.pack(fill="x")
        self.eyebrow(top, self.tr("Локальная игра", "Local game")).pack(side="left")
        self.badge = tk.Label(top, bg=FIELD, fg=MUTED, padx=10, pady=5, font=(self.face, 9))
        self.badge.pack(side="right")
        center = tk.Frame(hero, bg=CARD)
        center.pack(fill="x", pady=(10, 0))
        self.title = self.label(center, "", size=20, bold=True, center=True, wrap=80)
        self.title.pack()
        self.hint_label = self.label(center, "", muted=True, center=True, wrap=110)
        self.hint_label.pack(pady=(6, 14))
        self.start_button = self.button(center, self.tr("Запустить игру", "Start game"),
                                        self.toggle_session, big=True)
        self.start_button.pack(pady=(0, 4))

        join = self.panel(body, 14)
        join.columnconfigure(0, weight=1)
        self.eyebrow(join, self.tr("Адрес для игроков", "Player address")).grid(row=0, column=0, sticky="w")
        self.join_url = tk.StringVar(self.root, "")
        self.address = self.label(join, textvariable=self.join_url, size=13, bold=True, mono=True, wrap=210)
        self.address.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        self.copy_button = self.button(join, self.tr("Копировать", "Copy"), self.copy_address, width=110)
        self.copy_button.grid(row=0, column=1, rowspan=2, padx=(12, 0))

        tk.Frame(join, bg=BORDER, height=1).grid(row=2, column=0, columnspan=2, sticky="ew", pady=12)
        room = tk.Frame(join, bg=CARD)
        room.grid(row=3, column=0, sticky="nsew")
        self.room_caption = self.eyebrow(room, "")
        self.room_caption.pack(anchor="w")
        self.room = self.label(room, "—", size=22, bold=True, mono=True, color=ACC)
        self.room.pack(anchor="w", pady=(2, 0))
        self.players = self.label(room, "", muted=True, size=9, wrap=230)
        self.players.pack(fill="x", pady=(6, 0))
        # QR-код занимает место, только когда есть что показывать.
        self.qr_canvas = tk.Canvas(join, width=120, height=120, bg=CARD, highlightthickness=0)

        game = self.panel(body, 14)
        self.eyebrow(game, self.tr("Папка игры", "Game folder")).pack(anchor="w", pady=(0, 6))
        self.game_path = tk.StringVar(self.root, self.cfg["game_dir"])
        self.game_path.trace_add("write", self._game_edited)
        self.game_entry = self.entry(game, self.game_path)
        self.game_entry.bind("<FocusOut>", lambda _: self.save_game())
        self.game_entry.bind("<Return>", lambda _: self.save_game())
        self.game_status = self.label(game, "", muted=True, size=9, wrap=90)
        self.game_status.pack(fill="x", pady=(6, 10))
        actions = tk.Frame(game, bg=CARD)
        actions.pack(fill="x")
        self.browse_button = self.button(actions, self.tr("Выбрать папку", "Choose folder"), self.browse_game)
        self.browse_button.pack(side="left")
        self.patch_button = self.button(actions, self.tr("Установить патч", "Install patch"),
                                        self.install_patch, width=150)
        self.patch_button.pack(side="left", padx=(8, 0))
        self.restore_button = self.button(actions, self.tr("Убрать патч", "Remove patch"), self.restore_patch)
        self.restore_button.pack(side="left", padx=(8, 0))

    def render(self, state):
        """Обновляет главный экран. На других экранах только запоминает состояние."""
        self.snapshot = state
        if self.page != HOME or not self.title.winfo_exists():
            return
        active = state["session"]
        locked = self.busy or active
        patch = state["patch"] == wtd.ON
        ready = state["game_ok"]

        self.game_entry.configure(state="disabled" if locked else "normal")
        self.browse_button.set(enabled=not locked)
        self.settings_button.set(enabled=not self.busy)
        self.patch_button.set(enabled=ready and not locked and not state["game_running"],
                              text=self.tr("Обновить патч", "Update patch") if patch else self.tr("Установить патч", "Install patch"))
        self.restore_button.set(enabled=ready and state["game"]["backup"] and not locked and not state["game_running"])

        if self.busy:
            badge, color = self.tr("●  Подготовка", "●  Preparing"), ACC
            title = self.tr("Готовим всё для игры", "Getting everything ready")
        elif active:
            badge, color = self.tr("●  Работает", "●  Running"), GREEN
            title = self.tr("Можно подключаться", "Ready for your friends")
        else:
            badge, color = self.tr("●  Остановлен", "●  Stopped"), MUTED
            title = self.tr("Вечер начинается здесь", "Your evening starts here")
        self.badge.configure(text=badge, fg=color)
        self.title.configure(text=title)
        self.hint_label.configure(text=self.tr(
            "Друзья открывают адрес ниже и вводят код комнаты с экрана игры.",
            "Friends open the address below and enter the room code from the game screen.") if active else self.tr(
            "Запустите игру и собирайте друзей. Патч встанет сам, если его ещё нет.",
            "Start the game and gather your friends. The patch installs itself if needed."))
        self.start_button.set(enabled=not self.busy and ready,
                              text=self.tr("Остановить игру", "Stop game") if active else self.tr("Запустить игру", "Start game"))
        if active:
            self.start_button.set_colors(FIELD, BORDER, FG)
        else:
            self.start_button.set_colors(ACC, ACC_HOVER, BG)

        if not ready:
            status = self.tr("Укажите папку What The Dub? с WhatTheDub_Data внутри.",
                             "Choose the What The Dub? folder containing WhatTheDub_Data.")
        elif state["game_running"] and not active:
            status = self.tr("Закройте игру перед установкой или удалением патча.",
                             "Close the game before installing or removing the patch.")
        elif patch:
            status = self.tr("Патч установлен. Оригинал сохранён.", "Patch installed. Original files backed up.")
        else:
            status = self.tr("Игра найдена. Можно установить патч.", "Game found. Ready to install the patch.")
        self.game_status.configure(text=status, fg=MUTED if ready else DANGER)

        url = state["join_url"] if active else ""
        self.join_url.set(url or self.tr("Появится после запуска игры", "Appears after starting the game"))
        self.address.configure(fg=FG if url else MUTED)
        live = state["relay"].get("code") or ""
        wanted = self.cfg["room_code"]
        self.room.configure(text=live or wanted or "—")
        if live:
            caption = self.tr("Код комнаты", "Room code")
        elif wanted:
            caption = self.tr("Код комнаты из настроек", "Room code from settings")
        else:
            caption = self.tr("Код комнаты придумает игра", "The game will pick a room code")
        self.room_caption.configure(text=caption.upper())

        people = state["relay"].get("players", [])
        if active and people:
            names = ", ".join(("★ " if p.get("host") else "") + p["name"] for p in people)
            text = self.tr(f"Игроки ({len(people)}): {names}", f"Players ({len(people)}): {names}")
        elif active:
            text = self.tr("Пока никто не зашёл.", "Nobody has joined yet.")
        else:
            text = self.tr("Телефон и компьютер должны быть в одной сети.",
                           "Phones and this computer must be on the same network.")
        self.players.configure(text=text)
        self.copy_button.set(enabled=active)
        self._draw_qr(url)

    def _draw_qr(self, url):
        if url == self._qr_url:
            return
        self._qr_url = url
        self.qr_canvas.delete("all")
        if not url:
            self.qr_canvas.grid_remove()
            return
        try:
            matrix = qr.encode(url)
        except ValueError:
            self.feedback.set(self.tr("Адрес слишком длинный для QR-кода. Скопируйте его.",
                                      "Address too long for a QR code. Copy it instead."))
            return
        self.qr_canvas.grid(row=3, column=1, sticky="ne", padx=(12, 0))
        size = int(self.qr_canvas.cget("width"))
        n = len(matrix)
        scale = max(1, size // (n + 8))
        offset = (size - n * scale) // 2
        self.qr_canvas.create_rectangle(0, 0, size, size, fill="white", outline="")
        for y, row in enumerate(matrix):
            for x, dark in enumerate(row):
                if dark:
                    self.qr_canvas.create_rectangle(offset + x * scale, offset + y * scale,
                                                    offset + (x + 1) * scale, offset + (y + 1) * scale,
                                                    fill=BG, outline="")

    # --- настройки -------------------------------------------------------------
    def _settings(self, body, bar):
        self.fields = {}
        self.label(body, self.tr("Настройте LocalDub под вашу компанию.", "Tune LocalDub for your crowd."),
                   muted=True).pack(anchor="w", pady=(0, 14))

        tabs = tk.Frame(body, bg=BG)
        tabs.pack(fill="x", pady=(0, 14))
        pages = tk.Frame(body, bg=BG)
        pages.pack(fill="x")
        names = (self.tr("Комната", "Room"), self.tr("Озвучка", "Voice"),
                 self.tr("Сеть", "Network"), self.tr("Прочее", "Other"))
        self.pages = [self._settings_room(pages), self._settings_voice(pages),
                      self._settings_network(pages), self._settings_other(pages)]
        self.tab_buttons = []
        for index, name in enumerate(names):
            tabs.columnconfigure(index, weight=1, uniform="tabs")
            button = Button(tabs, name, lambda i=index: self.select_tab(i), width=120, height=40,
                            radius=10, face=(self.face, 10))
            button.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 4, 0))
            self.tab_buttons.append(button)
        self.select_tab(self.tab)

        self.label(bar, self.tr("Голос и адрес применяются сразу.\nПорт — после остановки игры.",
                                "Voice and address apply at once.\nStop the game to change the port."),
                   muted=True, size=9).pack(side="left")
        self.save_button = self.button(bar, self.tr("Сохранить", "Save"), self.save_settings, accent=True)
        self.save_button.pack(side="right")
        self.root.bind("<Control-s>", lambda _e: self.save_settings() if self.page == SETTINGS else None)

    def select_tab(self, index):
        """Вкладки только прячут страницы: введённое на другой вкладке не теряется."""
        self.tab = index
        for page in self.pages:
            page.pack_forget()
        self.pages[index].pack(fill="x")
        for number, button in enumerate(self.tab_buttons):
            selected = number == index
            button.set_colors(ACC if selected else FIELD, ACC_HOVER if selected else BORDER,
                              BG if selected else MUTED)
        self.canvas.yview_moveto(0)

    def _settings_room(self, parent):
        border = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
        card = tk.Frame(border, bg=CARD, padx=20, pady=20)
        card.pack(fill="both", expand=True)
        self.card_title(card, self.tr("Комната", "Room")).pack(anchor="w", pady=(0, 4))
        self.field_label(card, self.tr("Код комнаты", "Room code"))
        self.fields["room_code"] = self._room_code_input(card)
        return border

    def _settings_voice(self, parent):
        border = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
        card = tk.Frame(border, bg=CARD, padx=20, pady=20)
        card.pack(fill="both", expand=True)
        self.card_title(card, self.tr("Голос вашей игры", "Your game's voice")).pack(anchor="w", pady=(0, 4))
        self.field_label(card, self.tr("Язык", "Language"))
        self.language = self.combo(card, ["Русский", "English"], 0 if self.cfg["lang"] == "ru" else 1)
        self.hint(card, self.tr("Язык интерфейса, телефонов и озвучки.", "Language of the app, phones and speech."))
        self.field_label(card, self.tr("Движок", "Engine"))
        self.engine = self.combo(card, [self.tr("Автоматически", "Automatic"), "Piper", "eSpeak NG",
                                        self.tr("Без озвучки", "No speech")],
                                 VOICE_KEYS.index(self.cfg["tts_engine"]))
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", pady=(10, 0))
        self.voice_button = self.button(row, self.tr("Скачать голос для этого языка",
                                                     "Download a voice for this language"), self.download_voice)
        self.voice_button.pack(side="left")
        self._path_field(card, "piper_dir", self.tr("Папка с голосами Piper", "Piper voice folder"),
                         lambda: filedialog.askdirectory(parent=self.root))
        self._path_field(card, "piper_model", self.tr("Модель Piper (.onnx, необязательно)", "Piper model (.onnx, optional)"),
                         lambda: filedialog.askopenfilename(parent=self.root, filetypes=[("Piper", "*.onnx")]))
        return border

    def _settings_network(self, parent):
        border = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
        card = tk.Frame(border, bg=CARD, padx=20, pady=20)
        card.pack(fill="both", expand=True)
        self.card_title(card, self.tr("Вход игроков", "Player access")).pack(anchor="w", pady=(0, 4))
        self.field_label(card, self.tr("Адрес для друзей (необязательно)", "Public address (optional)"))
        self.fields["announce_ip"] = tk.StringVar(self.root, self.cfg["announce_ip"])
        self.entry(card, self.fields["announce_ip"])
        self.hint(card, self.tr("Пусто — адрес определится сам. Для Radmin VPN и туннелей впишите свой, можно с портом.",
                                "Leave empty to detect it. For Radmin VPN or tunnels, type your own, port allowed."))
        self.field_label(card, self.tr("Порт", "Port"))
        self.fields["port"] = tk.StringVar(self.root, str(self.app.port))
        self.entry(card, self.fields["port"])
        return border

    def _settings_other(self, parent):
        border = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
        card = tk.Frame(border, bg=CARD, padx=20, pady=20)
        card.pack(fill="both", expand=True)
        self.card_title(card, self.tr("Дополнительно", "Advanced")).pack(anchor="w", pady=(0, 4))
        self._path_field(card, "dotnet_path", self.tr("Путь к .NET (необязательно)", ".NET path (optional)"),
                         lambda: filedialog.askopenfilename(parent=self.root))
        self.hint(card, self.tr(".NET нужен патчеру. В готовой сборке LocalDub он уже внутри.",
                                "The patcher needs .NET. Release builds of LocalDub include it."))
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", pady=(16, 0))
        self.button(row, self.tr("Пройти первую настройку заново", "Run the first-time setup again"),
                    self.restart_setup).pack(side="left")
        return border

    def _room_code_input(self, parent):
        value = tk.StringVar(self.root, self.cfg["room_code"])
        row = tk.Frame(parent, bg=parent.cget("bg"))
        row.pack(fill="x")
        entry = ttk.Entry(row, textvariable=value, font=(self.mono, 14, "bold"))
        entry.pack(side="left", fill="x", expand=True)
        self.button(row, self.tr("Случайный", "Random"), lambda: value.set("")).pack(side="left", padx=(8, 0))
        self.hint(parent, self.tr(
            f"{config.ROOM_CODE_MIN}–{config.ROOM_CODE_MAX} букв или цифр, можно по-русски. "
            "Пусто — игра каждый раз придумает код сама. Новый код появится "
            "со следующей комнатой.",
            f"{config.ROOM_CODE_MIN}–{config.ROOM_CODE_MAX} letters or digits, Cyrillic works too. "
            "Leave empty to let the game pick one. A new code applies from the next room."))
        # Пишем сразу заглавными: так код выглядит, как на экране игры.
        def upper(*_):
            text = value.get()
            if text != text.upper():
                at = entry.index("insert")
                value.set(text.upper())
                entry.icursor(at)
        value.trace_add("write", upper)
        return value

    def _path_field(self, parent, key, title, pick, first=False):
        self.field_label(parent, title, first=first)
        value = tk.StringVar(self.root, self.cfg[key])
        self.fields[key] = value
        row = tk.Frame(parent, bg=parent.cget("bg"))
        row.pack(fill="x")
        ttk.Entry(row, textvariable=value).pack(side="left", fill="x", expand=True)
        def choose():
            path = pick()
            if path:
                value.set(path)
        self.button(row, self.tr("Выбрать", "Browse"), choose).pack(side="left", padx=(8, 0))

    def _check_room_code(self, text):
        problem = config.room_code_problem(text)
        if problem == "room_code_chars":
            return self.tr("Код комнаты: только буквы и цифры.", "Room code: letters and digits only.")
        if problem == "room_code_length":
            return self.tr(f"Код комнаты: от {config.ROOM_CODE_MIN} до {config.ROOM_CODE_MAX} символов.",
                           f"Room code: {config.ROOM_CODE_MIN} to {config.ROOM_CODE_MAX} characters.")
        return ""

    def save_settings(self):
        if self.busy:
            return
        changes = {key: value.get().strip() for key, value in self.fields.items()}
        changes["tts_engine"] = VOICE_KEYS[self.engine.current()]
        changes["lang"] = "ru" if self.language.current() == 0 else "en"
        error = self._check_room_code(changes["room_code"])
        if error:
            self.select_tab(0)
            messagebox.showerror("LocalDub", error, parent=self.root)
            return
        try:
            port = int(changes["port"])
            if not 1024 <= port <= 65535:
                raise ValueError
        except ValueError:
            self.select_tab(2)
            messagebox.showerror("LocalDub", self.tr("Порт: число от 1024 до 65535", "Port must be between 1024 and 65535"), parent=self.root)
            return
        if self.app.session_on and port != self.app.port:
            messagebox.showerror("LocalDub", self.tr("Остановите игру перед сменой порта", "Stop the game before changing the port"), parent=self.root)
            return
        if port != self.app.port:
            ok, note = self.app.set_port(port)
            if not ok:
                messagebox.showerror("LocalDub", note, parent=self.root)
                return
        self.cfg.update(changes)
        self.page = HOME
        self._build()
        self.action(self._apply_settings, self.tr("Применяю настройки…", "Applying settings…"))

    def _apply_settings(self):
        if self.app.on_settings_change:
            self.app.on_settings_change()
        self.app.relay.lang = self.cfg["lang"]
        if self.app.session_on:
            self.app.write_handshake()
        return True, self.tr("Настройки сохранены", "Settings saved")

    def download_voice(self):
        lang = "ru" if self.language.current() == 0 else "en"
        self.action(lambda: self.app.speech.download_voice(lang),
                    self.tr("Скачиваю голос Piper…", "Downloading a Piper voice…"))

    def restart_setup(self):
        self.step = 0
        self.show(SETUP)

    # --- мастер первого запуска --------------------------------------------------
    def _setup(self, body, bar):
        card = self.panel(body, 20)
        top = tk.Frame(card, bg=CARD)
        top.pack(fill="x", pady=(0, 14))
        self.eyebrow(top, self.tr(f"Шаг {self.step + 1} из {SETUP_STEPS}",
                                  f"Step {self.step + 1} of {SETUP_STEPS}")).pack(side="left")
        dots = tk.Frame(top, bg=CARD)
        dots.pack(side="right")
        for i in range(SETUP_STEPS):
            tk.Frame(dots, bg=ACC if i <= self.step else BORDER, width=24, height=4).pack(side="left", padx=2)
        [self._step_welcome, self._step_game, self._step_room, self._step_done][self.step](card)

        last = self.step == SETUP_STEPS - 1
        self.next_button = self.button(bar, self.tr("Готово", "Finish") if last else self.tr("Далее", "Next"),
                                       self.setup_next, accent=True)
        self.next_button.pack(side="right")
        if self.step:
            self.button(bar, self.tr("Назад", "Back"), self.setup_back, width=100, height=46,
                        radius=12).pack(side="right", padx=(0, 8))
        self.label(bar, self.tr("Любой шаг можно пропустить.", "You can skip any step."),
                   muted=True, size=9).pack(side="left")

    def _step_title(self, card, title, text):
        self.label(card, title, size=20, bold=True, wrap=80).pack(fill="x")
        self.label(card, text, muted=True, wrap=80).pack(fill="x", pady=(6, 16))

    def _step_welcome(self, card):
        self._step_title(card, self.tr("Добро пожаловать в LocalDub", "Welcome to LocalDub"), self.tr(
            "LocalDub запускает What The Dub? по локальной сети: друзья играют с телефонов, "
            "реплики озвучиваются на этом компьютере. Пара шагов — и можно начинать. "
            "Настройку можно пропустить и вернуться к ней позже.",
            "LocalDub runs What The Dub? over your local network: friends play from their phones "
            "and lines are voiced on this computer. A couple of steps and you are ready. "
            "You can skip setup and come back to it later."))
        self.field_label(card, self.tr("Язык", "Language"), first=True)
        langs = tk.Frame(card, bg=CARD)
        langs.pack(fill="x")
        langs.columnconfigure((0, 1), weight=1, uniform="lang")
        for column, (code, name) in enumerate((("ru", "Русский"), ("en", "English"))):
            button = Button(langs, name, lambda c=code: self._set_language(c), width=100, height=42,
                            radius=12, face=(self.face, 10, "bold"))
            if self.cfg["lang"] == code:
                button.set_colors(ACC, ACC_HOVER, BG)
            button.grid(row=0, column=column, sticky="ew", padx=(0, 4) if column == 0 else (4, 0))

    def _set_language(self, code):
        if code != self.cfg["lang"]:
            self.cfg.update({"lang": code})
            self.app.relay.lang = code
            self._build()

    def _step_game(self, card):
        self._step_title(card, self.tr("Где игра?", "Where is the game?"), self.tr(
            "Укажите папку What The Dub? — внутри неё лежит WhatTheDub_Data. "
            "Обычно она находится в steamapps/common.",
            "Choose the What The Dub? folder — it contains WhatTheDub_Data. "
            "It usually lives in steamapps/common."))
        self.game_path = tk.StringVar(self.root, self.cfg["game_dir"])
        self.game_entry = self.entry(card, self.game_path)
        status = self.label(card, "", size=9, wrap=90)
        status.pack(fill="x", pady=(6, 10))
        def check(*_):
            ok = config.is_game_dir(self.game_path.get().strip().strip('"'))
            status.configure(text=self.tr("●  Игра найдена", "●  Game found") if ok else
                             self.tr("●  Здесь нет What The Dub?", "●  What The Dub? is not here"),
                             fg=GREEN if ok else DANGER)
        self.game_path.trace_add("write", check)
        check()
        self.button(card, self.tr("Выбрать папку", "Choose folder"), self.browse_game).pack(anchor="w")

    def _step_room(self, card):
        self._step_title(card, self.tr("Код комнаты", "Room code"), self.tr(
            "Его вводят друзья на телефонах. Придумайте свой — например, «КИНО» — "
            "или оставьте поле пустым, и игра будет выбирать код сама.",
            "Friends type it on their phones. Pick your own, like “FILM”, "
            "or leave it empty and the game will choose one each time."))
        self.setup_room = self._room_code_input(card)

    def _step_done(self, card):
        state = self.snapshot or {}
        game_ok = config.is_game_dir(self.cfg["game_dir"])
        patch = state.get("patch") == wtd.ON
        self._step_title(card, self.tr("Всё готово", "All set"), self.tr(
            "Настройки можно поменять в любой момент кнопкой «Настройки».",
            "You can change everything later with the Settings button."))
        rows = (
            (self.tr("Игра", "Game"), self.cfg["game_dir"] if game_ok else self.tr("не найдена", "not found"), game_ok),
            (self.tr("Код комнаты", "Room code"), self.cfg["room_code"] or self.tr("случайный", "random"), True),
            (self.tr("Патч", "Patch"), self.tr("установлен", "installed") if patch else self.tr("ещё не установлен", "not installed yet"), patch),
        )
        table = tk.Frame(card, bg=CARD)
        table.pack(fill="x")
        table.columnconfigure(1, weight=1)
        for line, (name, value, good) in enumerate(rows):
            self.label(table, name, muted=True).grid(row=line, column=0, sticky="nw", padx=(0, 16), pady=3)
            self.label(table, value, color=FG if good else ACC, wrap=220).grid(row=line, column=1, sticky="w", pady=3)
        self.install_check = None
        if game_ok and not patch:
            self.install_check = ttk.Checkbutton(
                card, text=self.tr("Установить патч сейчас", "Install the patch now"),
                variable=self.install_after_setup, style="Card.TCheckbutton")
            self.install_check.pack(anchor="w", pady=(14, 0))

    def _leave_step(self):
        """Сохраняет то, что введено на текущем шаге. False — остаться на нём."""
        if self.step == 1:
            self.save_game(force=True)
        elif self.step == 2:
            error = self._check_room_code(self.setup_room.get())
            if error:
                messagebox.showerror("LocalDub", error, parent=self.root)
                return False
            self.cfg.update({"room_code": self.setup_room.get().strip()})
        return True

    def setup_next(self):
        if self.busy or not self._leave_step():
            return
        if self.step < SETUP_STEPS - 1:
            self.step += 1
            self._build()
            return
        install = self.install_check is not None and self.install_after_setup.get()
        self._finish_setup(self.tr("Настройка завершена", "Setup complete"))
        if install:
            self.install_patch()

    def setup_back(self):
        if self.busy:
            return
        self._leave_step()
        self.step = max(0, self.step - 1)
        self._build()

    def skip_setup(self):
        if self.step == 1:
            self.save_game(force=True)
        self._finish_setup(self.tr("Настройку можно пройти позже в «Настройках».",
                                   "You can run setup later from Settings."))

    def _finish_setup(self, message):
        self.cfg.update({"setup_done": True})
        self.step = 0
        self.page = HOME
        self.feedback.set(message)
        self._build()

    # --- фоновая работа ------------------------------------------------------
    def _submit(self, work, done):
        future = self.worker.submit(work)
        def completed(result):
            try:
                value, error = result.result(), None
            except Exception as exc:
                value, error = None, str(exc)
            if not self.closed:
                self.events.put(lambda: done(value, error))
        future.add_done_callback(completed)

    def _drain(self):
        if self.closed:
            return
        try:
            while True:
                self.events.get_nowait()()
        except queue.Empty:
            pass
        self._drain_job = self.root.after(50, self._drain)

    def refresh(self):
        if self.closed:
            return
        if not self.polling and not self.busy:
            self.polling = True
            def updated(state, error):
                self.polling = False
                if error:
                    self.feedback.set(error)
                elif not self.busy:
                    self.render(state)
            self._submit(self.app.state, updated)
        self._refresh_job = self.root.after(1500, self.refresh)

    def action(self, work, message):
        if self.busy:
            return
        self.save_game()
        self.busy = True
        self.feedback.set(message)
        if self.snapshot:
            self.render(self.snapshot)
        def task():
            result = work()
            return result, self.app.state()
        def done(value, error):
            self.busy = False
            if error:
                self.feedback.set(error)
                messagebox.showerror("LocalDub", error, parent=self.root)
                if self.snapshot:
                    self.render(self.snapshot)
                return
            (ok, note), state = value
            self.render(state)
            self.feedback.set(note)
            if not ok:
                messagebox.showerror("LocalDub", note, parent=self.root)
        self._submit(task, done)

    # --- действия ------------------------------------------------------------
    def save_game(self, force=False):
        """Запоминает папку игры, если её поле сейчас на экране."""
        path = getattr(self, "game_path", None)
        if path is None or self.app.session_on or (self.busy and not force):
            return
        try:
            value = path.get().strip().strip('"')
        except tk.TclError:
            return
        if value != self.cfg["game_dir"]:
            self.cfg.update({"game_dir": value})

    def _game_edited(self, *_):
        if self._path_job is not None:
            self.root.after_cancel(self._path_job)
        self._path_job = self.root.after(350, self.save_game)

    def browse_game(self):
        selected = filedialog.askdirectory(parent=self.root, title=self.tr("Папка What The Dub?", "What The Dub? folder"),
                                           initialdir=self.cfg["game_dir"] or None)
        if selected:
            self.game_path.set(selected)
            self.save_game()
            self._submit(self.app.state, lambda s, e: self.render(s) if s else self.feedback.set(e))

    def install_patch(self):
        def install():
            if wtd.game_running():
                return False, self.tr("Сначала закройте игру.", "Close the game first.")
            return wtd.apply_patch(self.cfg["game_dir"], self.app.port, self.cfg["dotnet_path"])
        self.action(install, self.tr("Сохраняю оригинал и устанавливаю патч…", "Backing up original files and installing the patch…"))

    def restore_patch(self):
        def restore():
            if wtd.game_running():
                return False, self.tr("Сначала закройте игру.", "Close the game first.")
            return wtd.remove_patch(self.cfg["game_dir"], self.cfg["dotnet_path"])
        self.action(restore, self.tr("Восстанавливаю оригинальные файлы…", "Restoring original files…"))

    def toggle_session(self):
        def toggle():
            if self.app.session_on:
                self.app.stop_session()
                return True, self.tr("Игра остановлена", "Game stopped")
            ok, why = self.app.start_session()
            return ok, (self.tr("Можно подключаться", "Ready for your friends") if ok
                        else self.app.last_error or i18n.t(self.cfg["lang"], why))
        self.action(toggle, self.tr("Подготавливаю игру…", "Preparing the game…"))

    def copy_address(self):
        if self.app.session_on:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.join_url.get())
            self.feedback.set(self.tr("Адрес скопирован", "Address copied"))

    # --- жизненный цикл -----------------------------------------------------
    def run(self):
        self.root.mainloop()

    def close(self):
        if self.busy:
            self.feedback.set(self.tr("Дождитесь завершения операции…", "Wait for the current operation to finish…"))
            return
        self.save_game()
        self.closed = True
        for job in (self._drain_job, self._refresh_job, self._path_job):
            if job is not None:
                self.root.after_cancel(job)
        self.worker.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def create(app):
    return DesktopWindow(app)
