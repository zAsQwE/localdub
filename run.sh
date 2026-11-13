#!/usr/bin/env sh
# Запуск LocalDub. Все аргументы передаются приложению.
set -e

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

PY=$(command -v python3 || true)
if [ -z "$PY" ]; then
  echo "Нужен python3 — установите его и запустите снова." >&2
  exit 1
fi

# Нативное окно использует Tkinter; готовый LocalDub.run включает его внутрь.
exec "$PY" "$DIR/localdub.py" "$@"
