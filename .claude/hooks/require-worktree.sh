#!/bin/bash
# PreToolUse hook: блокирует Edit/Write на ветке main.
# Для любых изменений нужен worktree или feature-ветка.
# Исключение: .claude/ — инфраструктурная конфигурация.

# Проверяем, что мы в git-репозитории
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

# Пропускаем файлы вне репозитория (plan-файлы, глобальный .claude/ и т.д.)
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | grep -oP '"file_path"\s*:\s*"\K[^"]*' || true)
if [ -n "$FILE_PATH" ]; then
  REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
  case "$FILE_PATH" in
    "$REPO_ROOT"/.claude/*) exit 0 ;;  # .claude/ инфраструктура — пропускаем
    "$REPO_ROOT"/*) ;;                  # файл в репо — проверяем дальше
    *) exit 0 ;;                        # файл вне репо — пропускаем
  esac
fi

BRANCH=$(git branch --show-current 2>/dev/null)

if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "master" ]; then
  cat >&2 <<'EOF'
BLOCKED: Редактирование файлов на ветке main запрещено.
Войди в worktree через EnterWorktree перед внесением изменений.
EOF
  exit 2
fi

exit 0
