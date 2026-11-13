#!/usr/bin/env sh
# Добавляет LocalDub в меню приложений (и убирает по --uninstall).
set -e

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
FILE="$APPS/localdub.desktop"

if [ "$1" = "--uninstall" ]; then
  rm -f "$FILE"
  echo "Убрано из меню: $FILE"
  exit 0
fi

mkdir -p "$APPS"
cat > "$FILE" <<EOF
[Desktop Entry]
Type=Application
Name=LocalDub
Comment=Своя озвучка немого кино по локальной сети
Exec=$DIR/run.sh
Path=$DIR
Terminal=false
Categories=Game;
StartupNotify=true
EOF
chmod +x "$FILE"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" || true
echo "Добавлено в меню: $FILE"
