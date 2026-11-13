#!/usr/bin/env sh
# Собирает LocalDubBridge.dll против сборок установленной игры.
set -e

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/.." && pwd)
PROJECT=$(CDPATH= cd -- "$ROOT/.." && pwd)
PY3=$(command -v python3 || echo python3)
# Папку игры не угадываем: либо её передали аргументом, либо спрашиваем у
# самого приложения — оно умеет искать установленную игру.
MANAGED=$1
if [ -z "$MANAGED" ]; then
  MANAGED=$(PROJECT="$PROJECT" "$PY3" - <<'PY' 2>/dev/null
import os, sys
sys.path.insert(0, os.environ["PROJECT"])
from ld import config
game = config.find_game_dir()
print(config.managed_dir(game) if game else "")
PY
)
fi
OUT="$ROOT/build"

if [ -z "$MANAGED" ] || [ ! -f "$MANAGED/Assembly-CSharp.dll" ]; then
  echo "Не найдены сборки игры." >&2
  echo "Укажите путь: $0 /путь/к/WhatTheDub/WhatTheDub_Data/Managed" >&2
  exit 1
fi

CS=$(command -v mcs || command -v csc || true)
if [ -z "$CS" ]; then
  echo "Нужен компилятор C# из Mono: sudo pacman -S mono" >&2
  exit 1
fi

mkdir -p "$OUT"
# Старый способ сборки оставлял здесь копию исходника с подставленным путём.
rm -f "$OUT/Bridge.gen.cs"

set -- \
  -target:library -out:"$OUT/LocalDubBridge.dll" -nostdlib -langversion:6 \
  -r:"$MANAGED/mscorlib.dll" \
  -r:"$MANAGED/netstandard.dll" \
  -r:"$MANAGED/System.dll" \
  -r:"$MANAGED/System.Core.dll" \
  -r:"$MANAGED/UnityEngine.CoreModule.dll" \
  -r:"$MANAGED/UnityEngine.IMGUIModule.dll" \
  -r:"$MANAGED/Newtonsoft.Json.dll" \
  -r:"$MANAGED/PhotonRealtime.dll" \
  -r:"$MANAGED/Photon3Unity3D.dll"

"$CS" "$@" "$HERE/Bridge.cs"

echo "собрано: $OUT/LocalDubBridge.dll"
