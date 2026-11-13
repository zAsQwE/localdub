# Компоненты готовой программы

Python и Tcl/Tk: [Astral Python Standalone Builds](https://github.com/astral-sh/python-build-standalone).
Тексты лицензий входят в `runtime/python`.

.NET Runtime 8: [Microsoft .NET](https://github.com/dotnet/runtime),
MIT и сторонние лицензии в `runtime/dotnet`.

Piper 2023.11.14-2: [исходный код](https://github.com/rhasspy/piper/tree/2023.11.14-2), MIT.
Piper phonemize: [исходный код](https://github.com/rhasspy/piper-phonemize), MIT.
Содержит eSpeak NG ([исходный код используемой версии](https://github.com/rhasspy/espeak-ng/tree/0f65aa301e0d6bae5e172cc74197d32a6182200f)), GPL-3.0-or-later,
и ONNX Runtime 1.14.1 (MIT, уведомления в этом каталоге).
Дополнительные зависимости Piper: fmt 10.0.0 и spdlog 1.12.0 (MIT).
Исходники включённой версии eSpeak NG и рецепт сборки находятся в `sources/espeak-ng.zip`
и `piper-phonemize-build.txt` рядом с этим файлом.

В Windows библиотеки Visual C++ из официального Microsoft VC Redistributable
размещены рядом с Piper. Условия поставляются в `Microsoft-VC-License.rtf`.

Русский голос dmitri-medium: [карточка модели](https://huggingface.co/rhasspy/piper-voices/tree/main/ru/ru_RU/dmitri/medium),
данные CC0; карточка поставляется рядом с голосом.

Mono.Cecil 0.11.6: [исходный код](https://github.com/jbevain/cecil/tree/0.11.6), MIT.

URL и контрольные суммы всех загруженных рантаймов находятся в `runtime-lock.json`.
