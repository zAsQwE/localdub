// Патчер Assembly-CSharp.dll: вставляет вызовы моста в код игры.
//
// Работает всегда от резервной копии оригинала, поэтому его можно прогонять
// сколько угодно раз — в том числе после каждого обновления игры в Steam.
using System;
using System.IO;
using System.Collections.Generic;
using System.Linq;
using Mono.Cecil;
using Mono.Cecil.Cil;

namespace LocalDub.Patcher
{
    static class Program
    {
        const string BridgeAssembly = "LocalDubBridge";
        const string BridgeType = "LocalDub.Bridge";

        static int Main(string[] args)
        {
            string managed = args.FirstOrDefault(a => !a.StartsWith("--"));
            if (string.IsNullOrEmpty(managed))
            {
                Console.Error.WriteLine(
                    "Использование: wtd-patcher <путь к Managed> [--restore] [--status] "
                    + "[--tts-url http://127.0.0.1:8080]");
                return 2;
            }

            string ttsUrl = Value(args, "--tts-url");
            string target = Path.Combine(managed, "Assembly-CSharp.dll");
            string backup = target + ".localdub-orig";

            if (!File.Exists(target))
            {
                Console.Error.WriteLine($"Не найдена сборка игры: {target}");
                return 2;
            }

            if (args.Contains("--status")) return Status(target, backup);
            if (args.Contains("--restore")) return Restore(target, backup);

            return Patch(managed, target, backup, ttsUrl);
        }

        // --- состояние ------------------------------------------------------
        static int Status(string target, string backup)
        {
            bool patched = IsPatched(target);
            Console.WriteLine($"сборка игры : {(patched ? "пропатчена" : "оригинальная")}");
            Console.WriteLine($"резервная   : {(File.Exists(backup) ? "есть" : "нет")}");
            return patched ? 0 : 1;
        }

        static bool IsPatched(string path)
        {
            try
            {
                using var asm = AssemblyDefinition.ReadAssembly(path);
                return asm.MainModule.AssemblyReferences.Any(r => r.Name == BridgeAssembly);
            }
            catch
            {
                return false;
            }
        }

        static int Restore(string target, string backup)
        {
            if (!File.Exists(backup))
            {
                Console.Error.WriteLine("Резервной копии нет — восстанавливать нечего.");
                return 1;
            }
            File.Copy(backup, target, overwrite: true);
            Console.WriteLine("Оригинальная сборка возвращена на место.");
            return 0;
        }

        // --- патч -------------------------------------------------------------
        static string Value(string[] args, string key)
        {
            int at = Array.IndexOf(args, key);
            return at >= 0 && at + 1 < args.Length ? args[at + 1] : null;
        }

        static int Patch(string managed, string target, string backup, string ttsUrl)
        {
            string bridgePath = Path.Combine(managed, BridgeAssembly + ".dll");
            if (!File.Exists(bridgePath))
            {
                Console.Error.WriteLine($"Мост не найден: {bridgePath}");
                Console.Error.WriteLine("Соберите его через wtd/bridge/build.sh и скопируйте в Managed.");
                return 2;
            }

            // Оригинал сохраняем один раз и дальше патчим только его копию.
            if (!File.Exists(backup))
            {
                if (IsPatched(target))
                {
                    Console.Error.WriteLine("Сборка уже изменена, а копии оригинала нет.");
                    Console.Error.WriteLine("Проверьте целостность файлов в Steam и повторите.");
                    return 2;
                }
                File.Copy(target, backup);
                Console.WriteLine($"сохранён оригинал: {Path.GetFileName(backup)}");
            }

            var resolver = new DefaultAssemblyResolver();
            resolver.AddSearchDirectory(managed);

            using var bridge = AssemblyDefinition.ReadAssembly(bridgePath,
                new ReaderParameters { AssemblyResolver = resolver });
            using var game = AssemblyDefinition.ReadAssembly(backup,
                new ReaderParameters { AssemblyResolver = resolver });

            var bridgeType = bridge.MainModule.GetType(BridgeType);
            if (bridgeType == null)
            {
                Console.Error.WriteLine($"В мосте нет типа {BridgeType}.");
                return 2;
            }

            var boot = bridgeType.Methods.FirstOrDefault(m => m.Name == "Boot" && m.IsStatic);
            if (boot == null)
            {
                Console.Error.WriteLine("В мосте нет метода Boot().");
                return 2;
            }

            // Инициализатор модуля Unity выполняет не всегда, поэтому основной
            // зацеп — Start() сплэш-экрана: он точно вызывается на первой сцене.
            int changes = InjectAtMethodStart(game.MainModule, "Splash", "Start", boot);
            changes += InjectModuleInitializer(game.MainModule, boot);
            if (changes == 0)
                Console.Error.WriteLine("Предупреждение: ни одна точка входа не изменена.");

            int redirects = RedirectCalls(game.MainModule, bridgeType);
            Console.WriteLine($"перенаправлено вызовов: {redirects}");
            changes += redirects;

            if (!string.IsNullOrEmpty(ttsUrl))
            {
                int voices = RedirectTts(game.MainModule, ttsUrl);
                Console.WriteLine($"перенаправлено адресов озвучки: {voices}");
                changes += voices;
            }

            string temp = target + ".tmp";
            game.Write(temp);
            File.Move(temp, target, overwrite: true);

            Console.WriteLine($"внесено правок: {changes}");
            Console.WriteLine($"записано: {target}");
            return 0;
        }

        /// <summary>Что на что подменяется. Последнее поле — метод в мосте;
        /// для методов экземпляра он статический и первым параметром берёт объект.</summary>
        static readonly (string Type, string Method, int Args, bool Instance, string Bridge)[] Map =
        {
            // статика PhotonNetwork
            ("Photon.Pun.PhotonNetwork", "get_CurrentRoom", 0, false, "get_CurrentRoom"),
            ("Photon.Pun.PhotonNetwork", "get_LocalPlayer", 0, false, "get_LocalPlayer"),
            ("Photon.Pun.PhotonNetwork", "get_PlayerList", 0, false, "get_PlayerList"),
            ("Photon.Pun.PhotonNetwork", "get_IsMasterClient", 0, false, "get_IsMasterClient"),
            ("Photon.Pun.PhotonNetwork", "get_IsConnected", 0, false, "get_IsConnected"),
            ("Photon.Pun.PhotonNetwork", "get_InRoom", 0, false, "get_InRoom"),
            ("Photon.Pun.PhotonNetwork", "RaiseEvent", 4, false, "RaiseEvent"),
            ("Photon.Pun.PhotonNetwork", "ConnectUsingSettings", 0, false, "Connect"),
            ("Photon.Pun.PhotonNetwork", "ConnectUsingSettings", 2, false, "Connect"),
            ("Photon.Pun.PhotonNetwork", "CreateRoom", 4, false, "CreateRoom"),
            ("Photon.Pun.PhotonNetwork", "JoinRoom", 2, false, "JoinRoom"),
            ("Photon.Pun.PhotonNetwork", "JoinOrCreateRoom", 4, false, "JoinOrCreateRoom"),
            ("Photon.Pun.PhotonNetwork", "LeaveRoom", 1, false, "LeaveRoom"),
            ("Photon.Pun.PhotonNetwork", "Disconnect", 0, false, "Disconnect"),
            ("Photon.Pun.PhotonNetwork", "CloseConnection", 1, false, "CloseConnection"),
            ("Photon.Pun.PhotonNetwork", "set_GameVersion", 1, false, "set_GameVersion"),
            ("Photon.Pun.PhotonNetwork", "set_AutomaticallySyncScene", 1, false, "set_AutomaticallySyncScene"),
            ("Photon.Pun.PhotonNetwork", "set_NickName", 1, false, "set_NickName"),
            ("Photon.Pun.PhotonNetwork", "LoadLevel", 1, false, "LoadLevel"),
            // методы экземпляра: объект уже лежит на стеке первым
            ("Photon.Realtime.Room", "SetCustomProperties", 3, true, "RoomSetCustomProperties"),
            ("Photon.Realtime.Player", "SetCustomProperties", 3, true, "PlayerSetCustomProperties"),
            ("Photon.Realtime.Player", "set_NickName", 1, true, "PlayerSetNickName"),
        };

        /// <summary>Заменяет обращения к Photon на вызовы моста во всём коде игры.</summary>
        static int RedirectCalls(ModuleDefinition module, TypeDefinition bridgeType)
        {
            var replacements = new Dictionary<string, MethodReference>();
            foreach (var rule in Map)
            {
                int want = rule.Args + (rule.Instance ? 1 : 0);
                var target = bridgeType.Methods.FirstOrDefault(
                    m => m.IsStatic && m.Name == rule.Bridge && m.Parameters.Count == want);
                if (target == null)
                {
                    Console.Error.WriteLine(
                        $"в мосте нет {rule.Bridge} на {want} аргументов — {rule.Method} пропущен");
                    continue;
                }
                replacements[$"{rule.Type}::{rule.Method}/{rule.Args}"] = module.ImportReference(target);
            }

            int count = 0;
            var missed = new SortedSet<string>();
            var mismatched = new SortedSet<string>();
            foreach (var type in AllTypes(module))
            {
                foreach (var method in type.Methods)
                {
                    if (!method.HasBody) continue;
                    foreach (var instruction in method.Body.Instructions)
                    {
                        if (instruction.OpCode != OpCodes.Call && instruction.OpCode != OpCodes.Callvirt)
                            continue;
                        if (!(instruction.Operand is MethodReference called)) continue;
                        var owner = called.DeclaringType?.FullName;
                        if (owner == null) continue;
                        if (!owner.StartsWith("Photon.")) continue;

                        var key = $"{owner}::{called.Name}/{called.Parameters.Count}";
                        if (replacements.TryGetValue(key, out var target))
                        {
                            // Тип возврата обязан совпадать: иначе стек поедет и
                            // рантайм упадёт с InvalidProgramException.
                            if (target.ReturnType.FullName != called.ReturnType.FullName)
                            {
                                mismatched.Add(
                                    $"{key}: игра ждёт {called.ReturnType.Name}, в мосте {target.ReturnType.Name}");
                                continue;
                            }
                            instruction.OpCode = OpCodes.Call;
                            instruction.Operand = target;
                            count++;
                        }
                        else if (owner == "Photon.Pun.PhotonNetwork")
                        {
                            missed.Add(key);
                        }
                    }
                }
            }

            foreach (var key in missed)
                Console.Error.WriteLine($"не перенаправлено: {key}");
            foreach (var key in mismatched)
                Console.Error.WriteLine($"РАСХОЖДЕНИЕ ТИПА: {key}");
            if (mismatched.Count > 0)
                throw new InvalidOperationException(
                    "Мост не совпадает с игрой по типам возврата — патч отменён.");
            return count;
        }

        const string GoogleTts = "https://texttospeech.googleapis.com";

        /// <summary>Уводит облачный синтез Google на наш локальный сервер.
        ///
        /// Адрес зашит в коде строковыми литералами, поэтому переписываем их.
        /// Схема меняется на http — тогда игре не нужен ни сертификат, ни ключ.</summary>
        static int RedirectTts(ModuleDefinition module, string baseUrl)
        {
            baseUrl = baseUrl.TrimEnd('/');
            int count = 0;
            foreach (var type in AllTypes(module))
            {
                foreach (var method in type.Methods)
                {
                    if (!method.HasBody) continue;
                    foreach (var instruction in method.Body.Instructions)
                    {
                        if (instruction.OpCode != OpCodes.Ldstr) continue;
                        if (!(instruction.Operand is string text)) continue;
                        if (!text.StartsWith(GoogleTts, StringComparison.OrdinalIgnoreCase)) continue;
                        instruction.Operand = baseUrl + text.Substring(GoogleTts.Length);
                        count++;
                    }
                }
            }
            return count;
        }

        static IEnumerable<TypeDefinition> AllTypes(ModuleDefinition module)
        {
            foreach (var type in module.Types)
            {
                yield return type;
                foreach (var nested in Nested(type))
                    yield return nested;
            }
        }

        static IEnumerable<TypeDefinition> Nested(TypeDefinition type)
        {
            foreach (var inner in type.NestedTypes)
            {
                yield return inner;
                foreach (var deeper in Nested(inner))
                    yield return deeper;
            }
        }

        /// <summary>Вставляет вызов в самое начало указанного метода игры.</summary>
        static int InjectAtMethodStart(ModuleDefinition module, string typeName,
                                      string methodName, MethodDefinition target)
        {
            var type = module.Types.FirstOrDefault(t => t.Name == typeName);
            var method = type?.Methods.FirstOrDefault(m => m.Name == methodName && m.HasBody);
            if (method == null)
            {
                Console.Error.WriteLine($"Не найден {typeName}::{methodName} — точка входа пропущена.");
                return 0;
            }
            if (AlreadyCalls(method))
                return 0;

            var il = method.Body.GetILProcessor();
            il.InsertBefore(method.Body.Instructions[0],
                            il.Create(OpCodes.Call, module.ImportReference(target)));
            Console.WriteLine($"точка входа: {typeName}::{methodName}");
            return 1;
        }

        static bool AlreadyCalls(MethodDefinition method)
        {
            return method.Body.Instructions.Any(i => i.OpCode == OpCodes.Call
                && (i.Operand as MethodReference)?.DeclaringType.FullName == BridgeType);
        }

        /// <summary>Вешает вызов Boot() на инициализатор модуля — он выполняется
        /// при первой же загрузке сборки игры, до любого её кода.</summary>
        static int InjectModuleInitializer(ModuleDefinition module, MethodDefinition boot)
        {
            var moduleType = module.Types.FirstOrDefault(t => t.Name == "<Module>");
            if (moduleType == null)
                throw new InvalidOperationException("В сборке нет типа <Module>.");

            var cctor = moduleType.Methods.FirstOrDefault(m => m.Name == ".cctor");
            if (cctor == null)
            {
                var attrs = MethodAttributes.Static | MethodAttributes.SpecialName
                          | MethodAttributes.RTSpecialName | MethodAttributes.Private
                          | MethodAttributes.HideBySig;
                cctor = new MethodDefinition(".cctor", attrs, module.TypeSystem.Void);
                cctor.Body = new MethodBody(cctor);
                cctor.Body.GetILProcessor().Emit(OpCodes.Ret);
                moduleType.Methods.Add(cctor);
            }

            var call = module.ImportReference(boot);
            if (AlreadyCalls(cctor))
                return 0;

            var il = cctor.Body.GetILProcessor();
            il.InsertBefore(cctor.Body.Instructions[0], il.Create(OpCodes.Call, call));
            return 1;
        }
    }
}
