#!/bin/bash
# PreToolUse hook for Bash: блокирует смену ветки в корневой директории проекта.
# Для работы на другой ветке — используй EnterWorktree.

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | grep -oP '"command"\s*:\s*"\K[^"]*' || true)
[ -z "$COMMAND" ] && exit 0

# Проверяем наличие git checkout/switch (не file-restore вариант)
if ! echo "$COMMAND" | grep -qP 'git\s+.*?(checkout|switch)\b'; then
  exit 0
fi

# Разрешаем git checkout -- <file> (restore files)
if echo "$COMMAND" | grep -qP 'git\s+.*?(checkout|switch)\s+--\s'; then
  exit 0
fi

# Извлекаем -C путь если есть (git -C <path> checkout ...)
TARGET_DIR=$(echo "$COMMAND" | grep -oP 'git\s+-C\s+\K\S+' || true)

if [ -n "$TARGET_DIR" ]; then
  # Проверяем целевой репозиторий
  GIT_DIR=$(git -C "$TARGET_DIR" rev-parse --git-dir 2>/dev/null) || exit 0
  COMMON_DIR=$(git -C "$TARGET_DIR" rev-parse --git-common-dir 2>/dev/null) || exit 0
else
  # Проверяем CWD
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
  GIT_DIR=$(git rev-parse --git-dir 2>/dev/null)
  COMMON_DIR=$(git rev-parse --git-common-dir 2>/dev/null)
fi

if [ "$(realpath "$GIT_DIR")" = "$(realpath "$COMMON_DIR")" ]; then
  cat >&2 <<'EOF'
BLOCKED: Смена ветки в корневой директории проекта запрещена.
Используй EnterWorktree для работы на другой ветке.
`git checkout -- <file>` для восстановления файлов по-прежнему доступен.
EOF
  exit 2
fi

exit 0
