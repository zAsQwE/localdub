#!/bin/sh
# Самораспаковывающаяся программа. Python и системный .NET не требуются.
set -eu
task_id='@BUILD_ID@'
task_line='@PAYLOAD_LINE@'
task_cache="${XDG_CACHE_HOME:-$HOME/.cache}/localdub/app"
task_extract=''
if [ "${1:-}" = '--extract' ]; then
    [ "$#" -eq 2 ] || { echo 'Usage: LocalDub.run --extract DIRECTORY' >&2; exit 2; }
    task_extract=$2
    mkdir -p "$task_extract"
    tail -n +"$task_line" "$0" | tar -xz -C "$task_extract"
    exit 0
fi
task_app="$task_cache/$task_id"
if [ ! -f "$task_app/.ready" ]; then
    mkdir -p "$task_cache"
    task_stage=$(mktemp -d "$task_cache/.unpack.XXXXXX")
    trap 'rm -rf "$task_stage"' EXIT HUP INT TERM
    tail -n +"$task_line" "$0" | tar -xz -C "$task_stage"
    touch "$task_stage/.ready"
    # Два одновременных запуска могут распаковывать одну сборку.
    if [ ! -d "$task_app" ]; then
        mv -T "$task_stage" "$task_app" 2>/dev/null || [ -f "$task_app/.ready" ]
    fi
    rm -rf "$task_stage"
    trap - EXIT HUP INT TERM
fi
exec "$task_app/runtime/python/bin/python3" -X utf8 "$task_app/boot.py" "$@"
exit 1
