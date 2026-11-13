// Мост между игрой What The Dub и локальным сервером LocalDub.
//
// Пропатченная сборка игры вызывает методы отсюда вместо Photon. Сеть Photon
// не поднимается вообще: комната и игроки подделываются на месте, а обмен идёт
// по HTTP с нашим сервером на этой же машине.
//
// Видео и озвучка остаются в самой игре — на телефоны уходит только текст.
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Reflection;
using System.Runtime.Serialization;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using ExitGames.Client.Photon;
using Newtonsoft.Json;
using Photon.Realtime;
using UnityEngine;
using PhotonHashtable = ExitGames.Client.Photon.Hashtable;

namespace LocalDub
{
    // ------------------------------------------------------------------ лог
    internal static class Log
    {
        static readonly object Gate = new object();
        static string _dir;

        /// <summary>Каталог рядом с самой библиотекой — то есть Managed игры.
        ///
        /// Никаких зашитых путей: так один и тот же файл работает и в Linux
        /// под Proton, и в Windows, и его можно класть в репозиторий готовым.</summary>
        internal static string RuntimeDir
        {
            get
            {
                if (!string.IsNullOrEmpty(_dir)) return _dir;
                try
                {
                    _dir = Path.GetDirectoryName(typeof(Log).Assembly.Location);
                }
                catch
                {
                    _dir = null;
                }
                if (string.IsNullOrEmpty(_dir)) _dir = ".";
                return _dir;
            }
        }

        internal static bool Verbose;

        internal static void Line(string message)
        {
            try { Debug.Log("[LocalDub] " + message); }
            catch { }
            try
            {
                lock (Gate)
                {
                    File.AppendAllText(Path.Combine(RuntimeDir, "bridge.log"),
                        DateTime.Now.ToString("HH:mm:ss.fff") + "  " + message + Environment.NewLine);
                }
            }
            catch { }
        }

        internal static void Detail(string message)
        {
            if (Verbose) Line(message);
        }

        internal static void Fail(string what, Exception e)
        {
            // Разворачиваем обёртку рефлексии: без внутреннего исключения
            // сообщение бесполезно.
            var inner = e;
            while (inner is TargetInvocationException && inner.InnerException != null)
                inner = inner.InnerException;
            if (inner == null)
            {
                Line("ОШИБКА " + what + ": неизвестно");
                return;
            }
            Line("ОШИБКА " + what + ": " + inner.GetType().Name + " " + inner.Message);
            var trace = inner.StackTrace;
            if (!string.IsNullOrEmpty(trace))
            {
                foreach (var row in trace.Split('\n'))
                {
                    var line = row.Trim();
                    if (line.Length > 0) Line("    " + line);
                }
            }
        }
    }

    // ------------------------------------------------------- разбор ответов
    // Newtonsoft в сборке игры идёт без Linq, поэтому JSON читаем по типам.
    internal class BridgeConfig
    {
        public int port;
        public string token;
        public string lang;
        public bool verbose;
        public string room_code;
        public string join_text;      // адрес для игроков, как его показать на экране
        public List<string> join_qr;  // QR-код этого адреса: строки из 0 и 1
    }

    internal class PollItem
    {
        public string kind;
        public int code;
        public string content;
        public int actor;
        public string nick;
        public string userId;
    }

    internal class PollAnswer
    {
        public bool ok;
        public List<PollItem> items;
    }

    // -------------------------------------------------------------- настройки
    internal static class Cfg
    {
        internal static int Port;
        internal static string Token = "";
        internal static string Lang = "ru";

        /// <summary>Читает bridge.json, который кладёт лаунчер перед запуском игры.</summary>
        internal static bool Load()
        {
            try
            {
                var path = Path.Combine(Log.RuntimeDir, "bridge.json");
                if (!File.Exists(path))
                {
                    Log.Line("нет bridge.json — лаунчер не запускал эту игру");
                    return false;
                }
                var root = JsonConvert.DeserializeObject<BridgeConfig>(File.ReadAllText(path));
                if (root == null) return false;
                Port = root.port;
                Token = root.token ?? "";
                Lang = root.lang ?? "ru";
                Log.Verbose = root.verbose;
                return Port > 0 && Token.Length > 0;
            }
            catch (Exception e)
            {
                Log.Fail("чтение bridge.json", e);
                return false;
            }
        }

        /// <summary>Свежая копия bridge.json. Лаунчер переписывает его, когда в
        /// настройках меняют код комнаты или адрес, — мост подхватывает это на ходу.</summary>
        internal static BridgeConfig Fresh()
        {
            try
            {
                var path = Path.Combine(Log.RuntimeDir, "bridge.json");
                if (!File.Exists(path)) return null;
                return JsonConvert.DeserializeObject<BridgeConfig>(File.ReadAllText(path));
            }
            catch (Exception e)
            {
                Log.Fail("чтение bridge.json", e);
                return null;
            }
        }

        /// <summary>Код комнаты, заданный в лаунчере; пусто — пусть игра выберет сама.</summary>
        internal static string RoomCode()
        {
            var root = Fresh();
            var code = root == null ? "" : (root.room_code ?? "").Trim();
            return code.Length > 16 ? code.Substring(0, 16) : code;
        }

        internal static string Url(string path)
        {
            return "http://127.0.0.1:" + Port + path;
        }
    }

    // ------------------------------------------------- подделка Photon-объектов
    internal static class Fake
    {
        /// <summary>Создаёт объект, не вызывая конструктор: так подделка не зависит
        /// от того, как именно устроен конструктор в этой версии PUN.</summary>
        static object Blank(Type type)
        {
            return FormatterServices.GetUninitializedObject(type);
        }

        static FieldInfo Find(object target, string name)
        {
            var type = target.GetType();
            while (type != null)
            {
                var field = type.GetField(name,
                    BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (field != null) return field;
                type = type.BaseType;
            }
            return null;
        }

        /// <summary>Ставит поле по первому подходящему имени.
        ///
        /// Имена в PUN разные: где-то обычное поле, где-то автосвойство с
        /// подчёркнутым именем. Промах по полю виден в логе всегда: именно
        /// из-за молчаливого промаха игра падала с NullReference.</summary>
        static bool SetAny(object target, string[] names, object value)
        {
            foreach (var name in names)
            {
                var field = Find(target, name);
                if (field == null) continue;
                field.SetValue(target, value);
                return true;
            }
            Log.Line("не нашлось поле " + string.Join("/", names) + " у " + target.GetType().Name);
            return false;
        }

        static void SetField(object target, string name, object value)
        {
            SetAny(target, new[] { name }, value);
        }

        static object GetAny(object target, string[] names)
        {
            foreach (var name in names)
            {
                var field = Find(target, name);
                if (field != null) return field.GetValue(target);
            }
            return null;
        }

        static object GetField(object target, string name)
        {
            return GetAny(target, new[] { name });
        }

        // Свойства комнаты и игрока хранятся под разными именами.
        static readonly string[] PropsNames =
            { "customProperties", "<CustomProperties>k__BackingField" };

        internal static Player MakePlayer(int actor, string nick, string userId, bool isLocal)
        {
            var player = (Player)Blank(typeof(Player));
            SetField(player, "actorNumber", actor);
            SetField(player, "nickName", nick ?? "");
            SetField(player, "IsLocal", isLocal);
            SetField(player, "<UserId>k__BackingField", userId ?? "");
            var props = new PhotonHashtable();
            // Игра ищет свой идентификатор игрока именно здесь.
            if (!string.IsNullOrEmpty(userId)) props["WTD_PLAYER_ID"] = userId;
            SetAny(player, PropsNames, props);
            return player;
        }

        internal static Room MakeRoom(string name, byte maxPlayers)
        {
            var room = (Room)Blank(typeof(Room));
            SetField(room, "players", new Dictionary<int, Player>());
            SetAny(room, PropsNames, new PhotonHashtable());
            SetField(room, "maxPlayers", maxPlayers);
            SetField(room, "isOpen", true);
            SetField(room, "isVisible", true);
            SetField(room, "isOffline", true);   // чтобы штатный код не искал сеть
            SetName(room, name);
            return room;
        }

        static void SetName(Room room, string name)
        {
            // Имя комнаты живёт в RoomInfo и в разных версиях называется по-разному.
            foreach (var candidate in new[] { "name", "<Name>k__BackingField", "roomName" })
            {
                var type = room.GetType();
                while (type != null)
                {
                    var field = type.GetField(candidate,
                        BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                    if (field != null && field.FieldType == typeof(string))
                    {
                        field.SetValue(room, name ?? "");
                        return;
                    }
                    type = type.BaseType;
                }
            }
            Log.Detail("не нашлось поле имени комнаты");
        }

        internal static Dictionary<int, Player> PlayersOf(Room room)
        {
            return GetField(room, "players") as Dictionary<int, Player>;
        }

        internal static PhotonHashtable PropsOf(object target)
        {
            return GetAny(target, PropsNames) as PhotonHashtable;
        }

        internal static void SetNick(Player player, string nick)
        {
            SetField(player, "nickName", nick ?? "");
        }

        /// <summary>Собирает событие так, как его читает игра: код плюс строка
        /// в CustomData. Поле заполняем напрямую — геттер его и вернёт.</summary>
        internal static EventData MakeEvent(byte code, object content, int sender)
        {
            var data = new EventData();
            data.Code = code;
            data.CustomDataKey = 0xF5;          // ParameterCode.CustomEventContent
            data.SenderKey = 0xFE;              // ParameterCode.ActorNr
            SetField(data, "customData", content);
            SetField(data, "sender", sender);
            return data;
        }
    }

    // --------------------------------------------------- связь с нашим сервером
    internal static class Link
    {
        static Thread _poller;
        static volatile bool _stop;

        internal static bool Hello()
        {
            var answer = Post("/bridge/hello", new Dictionary<string, object>());
            return answer != null;
        }

        internal static void SendEvent(byte code, string content)
        {
            var body = new Dictionary<string, object> { { "code", code }, { "content", content } };
            Post("/bridge/event", body);
        }

        internal static void SendProps(Dictionary<string, object> props)
        {
            Post("/bridge/props", new Dictionary<string, object> { { "props", props } });
        }

        /// <summary>Код комнаты: игра показывает его на экране, телефоны вводят.</summary>
        /// <summary>`room` различает комнаты с одинаковым кодом: заданный в лаунчере
        /// код повторяется, а состав игроков у новой комнаты свой.</summary>
        internal static void SendRoom(string code, string room)
        {
            Post("/bridge/room", new Dictionary<string, object> { { "code", code }, { "room", room } });
        }

        static string Post(string path, Dictionary<string, object> body)
        {
            body["token"] = Cfg.Token;
            try
            {
                var payload = Encoding.UTF8.GetBytes(JsonConvert.SerializeObject(body));
                var request = (HttpWebRequest)WebRequest.Create(Cfg.Url(path));
                request.Method = "POST";
                request.ContentType = "application/json";
                request.Timeout = 8000;
                request.ContentLength = payload.Length;
                using (var stream = request.GetRequestStream())
                    stream.Write(payload, 0, payload.Length);
                using (var response = (HttpWebResponse)request.GetResponse())
                using (var reader = new StreamReader(response.GetResponseStream()))
                    return reader.ReadToEnd();
            }
            catch (Exception e)
            {
                Log.Detail("запрос " + path + " не удался: " + e.Message);
                return null;
            }
        }

        /// <summary>Долгий опрос в отдельном потоке: сервер держит запрос,
        /// пока игроки что-нибудь не сделают.</summary>
        internal static void StartPolling()
        {
            if (_poller != null) return;
            _stop = false;
            _poller = new Thread(Loop) { IsBackground = true, Name = "LocalDubPoll" };
            _poller.Start();
            Log.Line("опрос сервера запущен");
        }

        internal static void Stop()
        {
            _stop = true;
        }

        static void Loop()
        {
            while (!_stop)
            {
                string text = null;
                try
                {
                    var request = (HttpWebRequest)WebRequest.Create(
                        Cfg.Url("/bridge/poll?token=" + Uri.EscapeDataString(Cfg.Token)));
                    request.Method = "GET";
                    request.Timeout = 40000;
                    request.ReadWriteTimeout = 40000;
                    using (var response = (HttpWebResponse)request.GetResponse())
                    using (var reader = new StreamReader(response.GetResponseStream()))
                        text = reader.ReadToEnd();
                }
                catch (Exception e)
                {
                    Log.Detail("опрос сорвался: " + e.Message);
                    Thread.Sleep(1500);
                    continue;
                }

                if (string.IsNullOrEmpty(text)) continue;
                try
                {
                    var answer = JsonConvert.DeserializeObject<PollAnswer>(text);
                    if (answer == null || answer.items == null) continue;
                    foreach (var item in answer.items)
                    {
                        var copy = item;
                        if (copy != null) Pump.Post(() => Inbox.Handle(copy));
                    }
                }
                catch (Exception e)
                {
                    Log.Fail("разбор ответа опроса", e);
                }
            }
        }
    }

    // ------------------------------------------- очередь в главный поток Unity
    internal class Pump : MonoBehaviour
    {
        static readonly Queue<Action> Jobs = new Queue<Action>();
        static Pump _instance;

        internal static void Ensure()
        {
            if (_instance != null) return;
            var host = new GameObject("LocalDubBridge");
            UnityEngine.Object.DontDestroyOnLoad(host);
            _instance = host.AddComponent<Pump>();
        }

        /// <summary>Всё, что касается Unity и игры, обязано идти в главном потоке.</summary>
        internal static void Post(Action job)
        {
            if (job == null) return;
            lock (Jobs) Jobs.Enqueue(job);
        }

        void Update()
        {
            JoinScreen.Tick();
            while (true)
            {
                Action job;
                lock (Jobs)
                {
                    if (Jobs.Count == 0) return;
                    job = Jobs.Dequeue();
                }
                try { job(); }
                catch (Exception e) { Log.Fail("задание из очереди", e); }
            }
        }

        void OnGUI()
        {
            JoinScreen.Draw();
        }
    }

    // ------------------------------------- адрес для входа и QR-код на экране
    /// <summary>Игра зовёт игроков на playwtd.com (перевод — на wtd.tdot.space).
    /// Без интернета туда не попасть, поэтому в текстах на экране этот адрес
    /// меняется на адрес LocalDub, а пока надпись видна — рядом рисуется QR-код.
    /// Тексты лежат в ассетах, поэтому правим их уже на сцене, по ходу игры.</summary>
    internal static class JoinScreen
    {
        // Только сам адрес: ссылки вида wtd.tdot.space/mod или /credits — про другое.
        static readonly Regex Site = new Regex(
            @"(?:https?://)?(?:www\.)?(?:playwtd\.com|wtd\.tdot\.space)(?![\w/-]|\.\w)",
            RegexOptions.IgnoreCase);

        static readonly List<Type> TextTypes = new List<Type>();
        static bool _typesLooked;
        static float _nextScan, _nextConfig;
        static string _text = "";
        static List<string> _qrRows;
        static Texture2D _qr;
        static bool _visible;

        internal static void Tick()
        {
            try
            {
                var now = Time.unscaledTime;
                if (now >= _nextConfig)
                {
                    _nextConfig = now + 3f;
                    Reload();
                }
                if (now < _nextScan || string.IsNullOrEmpty(_text)) return;
                _nextScan = now + 0.4f;
                _visible = Scan();
            }
            catch (Exception e)
            {
                Log.Fail("подмена адреса на экране", e);
                _nextScan = Time.unscaledTime + 5f;
            }
        }

        static void Reload()
        {
            var root = Cfg.Fresh();
            if (root == null) return;
            _text = (root.join_text ?? "").Trim();
            var rows = root.join_qr;
            if (rows == null || rows.Count == 0) return;
            if (_qrRows != null && string.Join("", _qrRows.ToArray()) == string.Join("", rows.ToArray())) return;
            _qrRows = rows;
            _qr = MakeTexture(rows);
        }

        /// <summary>QR-код с белой рамкой в два модуля, по пикселю на модуль:
        /// при выводе он растягивается без сглаживания и остаётся чётким.</summary>
        static Texture2D MakeTexture(List<string> rows)
        {
            const int quiet = 2;
            int n = rows.Count, size = n + quiet * 2;
            var texture = new Texture2D(size, size, TextureFormat.RGBA32, false);
            texture.filterMode = FilterMode.Point;
            texture.wrapMode = TextureWrapMode.Clamp;
            var pixels = new Color32[size * size];
            var white = new Color32(255, 255, 255, 255);
            var black = new Color32(0, 0, 0, 255);
            for (int y = 0; y < size; y++)
            {
                for (int x = 0; x < size; x++)
                {
                    int row = y - quiet, column = x - quiet;
                    bool dark = row >= 0 && row < n && column >= 0 && column < rows[row].Length
                                && rows[row][column] == '1';
                    // В текстуре Unity строки идут снизу вверх.
                    pixels[(size - 1 - y) * size + x] = dark ? black : white;
                }
            }
            texture.SetPixels32(pixels);
            texture.Apply(false, false);
            return texture;
        }

        static void FindTypes()
        {
            _typesLooked = true;
            foreach (var name in new[] { "TMPro.TMP_Text", "UnityEngine.UI.Text" })
            {
                foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
                {
                    Type type = null;
                    try { type = assembly.GetType(name, false); }
                    catch { }
                    if (type == null) continue;
                    TextTypes.Add(type);
                    break;
                }
            }
            Log.Line("тексты на экране: найдено типов " + TextTypes.Count);
        }

        /// <summary>Правит тексты на сцене. true — адрес для входа сейчас виден.</summary>
        static bool Scan()
        {
            if (!_typesLooked) FindTypes();
            bool shown = false;
            foreach (var type in TextTypes)
            {
                var property = type.GetProperty("text", BindingFlags.Instance | BindingFlags.Public);
                if (property == null) continue;
                foreach (var found in UnityEngine.Object.FindObjectsOfType(type))
                {
                    var component = found as Component;
                    if (component == null || !component.gameObject.activeInHierarchy) continue;
                    var current = property.GetValue(found, null) as string;
                    if (string.IsNullOrEmpty(current)) continue;
                    if (Site.IsMatch(current))
                    {
                        var fixedText = Site.Replace(current, _text);
                        property.SetValue(found, fixedText, null);
                        Log.Detail("адрес на экране заменён: " + fixedText);
                        current = fixedText;
                    }
                    if (current.IndexOf(_text, StringComparison.Ordinal) >= 0) shown = true;
                }
            }
            return shown;
        }

        internal static void Draw()
        {
            if (!_visible || _qr == null) return;
            // Правый нижний угол, примерно пятая часть высоты экрана.
            float side = Mathf.Round(Screen.height * 0.2f);
            float margin = Mathf.Round(Screen.height * 0.03f);
            var place = new Rect(Screen.width - side - margin, Screen.height - side - margin, side, side);
            GUI.DrawTexture(place, _qr, ScaleMode.StretchToFill, false);
        }
    }

    // ------------------------------------------ зов обработчиков внутри игры
    internal static class Dispatch
    {
        /// <summary>Ищет у живых компонентов метод с таким именем и вызывает его.
        /// Так не нужно знать, в каком именно классе игры он объявлен.</summary>
        internal static int Call(string method, object[] args)
        {
            int called = 0;
            try
            {
                // Именно FindObjectsOfType, а не FindObjectsOfTypeAll: второй
                // возвращает ещё и префабы с неинициализированными полями, и
                // обработчик на таком шаблоне падает на пустых ссылках.
                var all = UnityEngine.Object.FindObjectsOfType<MonoBehaviour>();
                foreach (var behaviour in all)
                {
                    if (behaviour == null) continue;
                    if (!behaviour.gameObject.scene.IsValid()) continue;
                    var type = behaviour.GetType();
                    if (type.Assembly != GameAssembly) continue;
                    var info = type.GetMethod(method,
                        BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic,
                        null, Types(args), null);
                    if (info == null || info.DeclaringType == null) continue;
                    if (info.DeclaringType.Assembly != GameAssembly) continue;
                    try
                    {
                        info.Invoke(behaviour, args);
                        called++;
                    }
                    catch (Exception e)
                    {
                        Log.Fail(method + " у " + type.Name, e);
                    }
                }
            }
            catch (Exception e)
            {
                Log.Fail("поиск " + method, e);
            }
            Log.Detail(method + " -> вызван у " + called + " компонентов");
            return called;
        }

        static Assembly _gameAssembly;

        /// <summary>Сборка с кодом игры.
        ///
        /// Через Type.GetType её типы не найти: они без пространства имён и лежат
        /// в чужой сборке. Поэтому ищем по имени среди загруженных.</summary>
        internal static Assembly GameAssembly
        {
            get
            {
                if (_gameAssembly != null) return _gameAssembly;
                try
                {
                    foreach (var candidate in AppDomain.CurrentDomain.GetAssemblies())
                    {
                        if (candidate.GetName().Name == "Assembly-CSharp")
                        {
                            _gameAssembly = candidate;
                            Log.Line("сборка игры найдена");
                            return _gameAssembly;
                        }
                    }
                }
                catch (Exception e)
                {
                    Log.Fail("поиск сборки игры", e);
                }
                Log.Line("сборка игры не найдена — обработчики звать некому");
                _gameAssembly = typeof(Bridge).Assembly;
                return _gameAssembly;
            }
        }

        static Type[] Types(object[] args)
        {
            var types = new Type[args == null ? 0 : args.Length];
            for (int i = 0; i < types.Length; i++)
                types[i] = args[i] == null ? typeof(object) : ArgType(args[i]);
            return types;
        }

        static Type ArgType(object value)
        {
            if (value is Player) return typeof(Player);
            if (value is EventData) return typeof(EventData);
            return value.GetType();
        }
    }

    // ---------------------------------------- то, что пришло от игроков
    // ------------------------------------------- управление партией с телефона
    /// <summary>Начать игру и отменить отсчёт с телефона игра разрешает, только если
    /// в её настройках включено «управление игроками». У LocalDub хост — игрок с
    /// телефона, поэтому мост включает это сам. В файл настроек не пишем: меняем
    /// только значение в памяти, пока игра запущена.</summary>
    internal static class HostControl
    {
        static bool _failed;

        static bool _smallLogged;

        /// <summary>Игра сама умеет играть вдвоём и даже в одиночку — «без очков»:
        /// реплики есть, голосования и счёта нет. Флаг обычно и так включён;
        /// включаем на случай сборок, где его выключили.</summary>
        static void AllowSmallGames(Assembly game)
        {
            var constants = game == null ? null : game.GetType("WtdConstants");
            var field = constants == null ? null : constants.GetField("ALLOW_NO_SCORE_MODE",
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
            if (field == null || field.IsLiteral || (bool)field.GetValue(null)) return;
            field.SetValue(null, true);
            if (!_smallLogged)
            {
                _smallLogged = true;
                Log.Line("разрешена игра меньше чем втроём (без очков)");
            }
        }

        internal static void Allow()
        {
            if (_failed) return;
            try
            {
                var game = Dispatch.GameAssembly;
                AllowSmallGames(game);
                var managerType = game == null ? null : game.GetType("SaveDataManager");
                if (managerType == null) return;
                var flags = BindingFlags.Public | BindingFlags.NonPublic;
                var instance = managerType.GetMethod("get_Instance", flags | BindingFlags.Static,
                                                     null, Type.EmptyTypes, null);
                var manager = instance == null ? null : instance.Invoke(null, null);
                if (manager == null) return;  // игра ещё не загрузила настройки
                var getter = managerType.GetMethod("GetGameSettings", flags | BindingFlags.Instance,
                                                   null, Type.EmptyTypes, null);
                var settings = getter == null ? null : getter.Invoke(manager, null);
                if (settings == null) return;
                var field = settings.GetType().GetField("allowClientManagement", flags | BindingFlags.Instance);
                if (field == null)
                {
                    _failed = true;
                    Log.Line("в настройках игры нет allowClientManagement — хост с телефона не управляет");
                    return;
                }
                if (!(bool)field.GetValue(settings))
                {
                    field.SetValue(settings, true);
                    Log.Line("хосту с телефона разрешено начинать и отменять игру");
                }
            }
            catch (Exception e)
            {
                _failed = true;
                Log.Fail("разрешение управлять с телефона", e);
            }
        }
    }

    internal static class Inbox
    {
        internal static void Handle(PollItem item)
        {
            if (item.kind == "event")
                Bridge.DeliverEvent((byte)item.code, item.content);
            else if (item.kind == "join")
                Bridge.AddRemotePlayer(item.actor, item.nick, item.userId);
            else if (item.kind == "leave")
                Bridge.DropRemotePlayer(item.actor);
        }
    }

    // ================================================================ мост
    public static class Bridge
    {
        const int HostActor = 1;

        static bool _booted;
        static bool _ready;
        static bool _connected;
        static Room _room;
        static Player _host;
        static string _nick = "Host";
        static readonly object Gate = new object();

        // ---------------------------------------------------------- запуск
        /// <summary>Вызывается из Splash.Start() пропатченной игры.</summary>
        public static void Boot()
        {
            lock (Gate)
            {
                if (_booted) return;
                _booted = true;
            }
            try
            {
                Log.Line("мост запущен, рантайм " + Environment.Version);
                _ready = Cfg.Load();
                if (!_ready)
                {
                    Log.Line("настройки не прочитаны — мост бездействует, игра работает как обычно");
                    return;
                }
                Pump.Ensure();
                Log.Line("сервер: " + Cfg.Url(""));
                if (Link.Hello()) Log.Line("сервер ответил");
                else Log.Line("сервер молчит — попробуем позже");
                Link.StartPolling();
            }
            catch (Exception e)
            {
                Log.Fail("запуск моста", e);
            }
        }

        static bool Off
        {
            get { return !_ready; }
        }

        // ------------------------------------------------ вместо PhotonNetwork
        public static bool Connect()
        {
            return ConnectInternal();
        }

        public static bool Connect(AppSettings settings, bool startInOfflineMode)
        {
            return ConnectInternal();
        }

        static bool ConnectInternal()
        {
            Log.Line("подключение перехвачено, Photon не поднимаем");
            _connected = true;
            _host = Fake.MakePlayer(HostActor, _nick, "host", true);
            // Игра ждёт обратных вызовов, а не мгновенного результата.
            Pump.Post(() =>
            {
                Dispatch.Call("OnConnectedToMaster", new object[0]);
                Dispatch.Call("OnJoinedLobby", new object[0]);
            });
            return true;
        }

        public static bool CreateRoom(string name, RoomOptions options, TypedLobby lobby,
                                     string[] expectedUsers)
        {
            byte max = options != null ? options.MaxPlayers : (byte)16;
            HostControl.Allow();   // до OnJoinedRoom: игра читает флаг, собирая свойства комнаты
            var wanted = Cfg.RoomCode();
            if (!string.IsNullOrEmpty(wanted)) name = wanted;
            _room = Fake.MakeRoom(string.IsNullOrEmpty(name) ? RoomCode() : name, max);
            if (_host == null) _host = Fake.MakePlayer(HostActor, _nick, "host", true);
            var players = Fake.PlayersOf(_room);
            if (players != null) players[HostActor] = _host;
            Log.Line("комната создана: " + _room.Name);
            var code = _room.Name;
            var id = Guid.NewGuid().ToString("N");
            new Thread(() => Link.SendRoom(code, id)) { IsBackground = true }.Start();
            Pump.Post(() => Dispatch.Call("OnJoinedRoom", new object[0]));
            return true;
        }

        public static bool JoinRoom(string name, string[] expectedUsers)
        {
            return CreateRoom(name, null, null, expectedUsers);
        }

        public static bool JoinOrCreateRoom(string name, RoomOptions options, TypedLobby lobby,
                                          string[] expectedUsers)
        {
            return CreateRoom(name, options, lobby, expectedUsers);
        }

        static string RoomCode()
        {
            var letters = "ABCDEFGHJKLMNPQRSTUVWXYZ";
            var random = new System.Random();
            var code = new char[4];
            for (int i = 0; i < 4; i++) code[i] = letters[random.Next(letters.Length)];
            return new string(code);
        }

        public static Room get_CurrentRoom()
        {
            return _room;
        }

        public static Player get_LocalPlayer()
        {
            if (_host == null) _host = Fake.MakePlayer(HostActor, _nick, "host", true);
            return _host;
        }

        public static Player[] get_PlayerList()
        {
            var players = _room == null ? null : Fake.PlayersOf(_room);
            if (players == null) return new Player[0];
            var list = new List<Player>(players.Values);
            return list.ToArray();
        }

        public static bool get_IsConnected()
        {
            // После Disconnect игра ждёт именно «не подключены», иначе её
            // собственная логика возврата в меню пойдёт по неверной ветке.
            return _ready && _connected;
        }

        public static bool get_InRoom()
        {
            return _room != null;
        }

        public static bool get_IsMasterClient()
        {
            return true;
        }

        public static void set_GameVersion(string value)
        {
            Log.Detail("версия игры: " + value);
        }

        public static void set_AutomaticallySyncScene(bool value)
        {
        }

        public static void set_NickName(string value)
        {
            _nick = value ?? "Host";
            if (_host != null) Fake.SetNick(_host, _nick);
        }

        public static void LoadLevel(string sceneName)
        {
            // Photon делал это за игру; синхронизировать не с кем, грузим сами.
            Log.Detail("сцена: " + sceneName);
            try { UnityEngine.SceneManagement.SceneManager.LoadScene(sceneName); }
            catch (Exception e) { Log.Fail("загрузка сцены " + sceneName, e); }
        }

        public static bool LeaveRoom(bool becomeInactive)
        {
            Log.Line("выход из комнаты");
            CloseRoom();
            _connected = true;
            // Без этого игра не вернётся в меню: возврат делает её собственный
            // OnLeftRoom, а зовёт его обычно Photon.
            Pump.Post(() => Dispatch.Call("OnLeftRoom", new object[0]));
            return true;
        }

        public static void Disconnect()
        {
            Log.Line("отключение");
            CloseRoom();
            Pump.Post(() => Dispatch.Call("OnDisconnected",
                new object[] { DisconnectCause.DisconnectByClientLogic }));
        }

        /// <summary>Комнаты больше нет: телефонам туда заходить незачем.</summary>
        static void CloseRoom()
        {
            _room = null;
            _connected = false;
            new Thread(() => Link.SendRoom("", "")) { IsBackground = true }.Start();
        }

        public static bool CloseConnection(Player player)
        {
            if (player == null) return false;
            DropRemotePlayer(player.ActorNumber);
            return true;
        }

        public static bool RaiseEvent(byte code, object content, RaiseEventOptions options,
                                     SendOptions sendOptions)
        {
            var text = Payload(content);
            Log.Detail("в телефоны: код " + code + (text == null ? "" : " " + Trim(text)));
            Link.SendEvent(code, text);
            return true;
        }

        /// <summary>Приводит содержимое события к строке.
        ///
        /// Игра передаёт сюда не только строки: список вариантов для
        /// голосования и текст сцены приходят словарями. Photon сериализовал бы
        /// их сам, поэтому и нам нужно — иначе на телефон уходит пустота.</summary>
        static string Payload(object content)
        {
            if (content == null) return null;
            var text = content as string;
            if (text != null) return text;
            try
            {
                return JsonConvert.SerializeObject(ToPlain(content));
            }
            catch (Exception e)
            {
                Log.Fail("сериализация события", e);
                return null;
            }
        }

        static string Trim(string text)
        {
            return text.Length <= 120 ? text : text.Substring(0, 120) + "…";
        }

        // -------------------------------------------- вместо Room/Player методов
        public static bool RoomSetCustomProperties(Room room, PhotonHashtable properties,
                                                  PhotonHashtable expected, WebFlags flags)
        {
            if (room == null || properties == null) return false;
            Merge(Fake.PropsOf(room), properties);
            var plain = ToPlain(properties) as Dictionary<string, object>;
            if (plain != null && plain.Count > 0)
            {
                Log.Detail("свойства комнаты: " + string.Join(", ", KeysOf(plain)));
                Link.SendProps(plain);
            }
            return true;
        }

        public static bool PlayerSetCustomProperties(Player player, PhotonHashtable properties,
                                                    PhotonHashtable expected, WebFlags flags)
        {
            if (player == null || properties == null) return false;
            Merge(Fake.PropsOf(player), properties);
            return true;
        }

        public static void PlayerSetNickName(Player player, string value)
        {
            if (player != null) Fake.SetNick(player, value);
        }

        /// <summary>Сливает свойства в хранилище объекта.
        ///
        /// Игра нередко передаёт сюда тот же самый Hashtable, который у объекта
        /// и лежит. Поэтому сначала снимок, потом запись — иначе перебор падает
        /// на «коллекция изменена».</summary>
        static void Merge(PhotonHashtable current, PhotonHashtable incoming)
        {
            if (current == null || incoming == null) return;
            if (ReferenceEquals(current, incoming)) return;

            var keys = new List<object>();
            var values = new List<object>();
            foreach (DictionaryEntry entry in incoming)
            {
                keys.Add(entry.Key);
                values.Add(entry.Value);
            }
            for (int i = 0; i < keys.Count; i++)
                current[keys[i]] = values[i];
        }

        static string[] KeysOf(Dictionary<string, object> map)
        {
            var keys = new List<string>(map.Keys);
            return keys.ToArray();
        }

        /// <summary>Приводит структуры Photon к тому, что переживёт JSON.</summary>
        static object ToPlain(object value)
        {
            var table = value as PhotonHashtable;
            if (table != null)
            {
                var map = new Dictionary<string, object>();
                var entries = new List<DictionaryEntry>();
                foreach (DictionaryEntry entry in table) entries.Add(entry);
                foreach (var entry in entries)
                {
                    if (entry.Key == null) continue;
                    map[entry.Key.ToString()] = ToPlain(entry.Value);
                }
                return map;
            }
            var dictionary = value as IDictionary;
            if (dictionary != null)
            {
                var map = new Dictionary<string, object>();
                foreach (DictionaryEntry entry in dictionary)
                {
                    if (entry.Key == null) continue;
                    map[entry.Key.ToString()] = ToPlain(entry.Value);
                }
                return map;
            }
            if (value is string || value == null) return value;
            var list = value as IEnumerable;
            if (list != null)
            {
                var items = new List<object>();
                foreach (var item in list) items.Add(ToPlain(item));
                return items;
            }
            return value;
        }

        // ------------------------------------------- то, что пришло с телефонов
        internal static void DeliverEvent(byte code, string content)
        {
            try
            {
                HostControl.Allow();
                var data = Fake.MakeEvent(code, content, 0);
                int called = Dispatch.Call("OnEvent", new object[] { data });
                if (called == 0) Log.Line("некому отдать событие " + code);
            }
            catch (Exception e)
            {
                Log.Fail("доставка события " + code, e);
            }
        }

        internal static void AddRemotePlayer(int actor, string nick, string userId)
        {
            if (_room == null)
            {
                Log.Line("игрок пришёл до комнаты: " + nick);
                return;
            }
            var players = Fake.PlayersOf(_room);
            if (players == null) return;
            var player = Fake.MakePlayer(actor, nick, userId, false);
            players[actor] = player;
            Log.Line("игрок вошёл: " + nick + " (актёр " + actor + ")");
            Dispatch.Call("OnPlayerEnteredRoom", new object[] { player });
        }

        internal static void DropRemotePlayer(int actor)
        {
            if (_room == null) return;
            var players = Fake.PlayersOf(_room);
            if (players == null) return;
            Player player;
            if (!players.TryGetValue(actor, out player)) return;
            players.Remove(actor);
            Log.Line("игрок вышел: актёр " + actor);
            Dispatch.Call("OnPlayerLeftRoom", new object[] { player });
        }
    }
}
