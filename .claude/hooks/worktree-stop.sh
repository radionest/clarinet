#!/bin/bash
# Stop hook: блокирует завершение сессии в worktree,
# чтобы Claude спросил пользователя о судьбе изменений.

INPUT=$(cat)

# Предотвращаем бесконечный цикл — на повторной попытке пропускаем
if echo "$INPUT" | grep -qE '"stop_hook_active"\s*:\s*true'; then
  exit 0
fi

# Debounce: не блокировать повторно в течение 120 секунд
# PPID = Claude process — один debounce на сессию
DEBOUNCE_FILE="/tmp/claude-worktree-stop-${PPID}"
if [ -f "$DEBOUNCE_FILE" ]; then
  LAST=$(cat "$DEBOUNCE_FILE" 2>/dev/null || echo 0)
  NOW=$(date +%s)
  if [ $((NOW - LAST)) -lt 120 ]; then
    exit 0
  fi
fi

# Проверяем, что мы в git-репозитории
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

# Проверяем, что это worktree (не основной репо)
GIT_DIR=$(git rev-parse --git-dir 2>/dev/null)
GIT_COMMON_DIR=$(git rev-parse --git-common-dir 2>/dev/null)
if [ "$GIT_DIR" = "$GIT_COMMON_DIR" ]; then
  exit 0
fi

# Собираем информацию о состоянии
BRANCH=$(git branch --show-current 2>/dev/null)
HAS_CHANGES=$(git status --porcelain 2>/dev/null | head -1)
AHEAD=$(git rev-list main..HEAD --count 2>/dev/null || echo "0")

# Записываем timestamp для debounce
date +%s > "$DEBOUNCE_FILE"

# Пустой worktree (0 коммитов, 0 изменений) — короткое сообщение
if [ "$AHEAD" = "0" ] && [ -z "$HAS_CHANGES" ]; then
  echo "WORKTREE_EMPTY: ветка '$BRANCH', нет коммитов и изменений. Оставить или удалить?" >&2
  exit 2
fi

# Блокируем stop — stderr покажется Claude
cat >&2 <<EOF
WORKTREE_PENDING: Сессия в worktree, ветка '$BRANCH'.
Коммитов впереди main: $AHEAD
Незакоммиченные изменения: $([ -n "$HAS_CHANGES" ] && echo "есть" || echo "нет")

Перед завершением спроси пользователя:
1) Push ветки + создать PR в main + удалить worktree
2) Оставить worktree (для продолжения позже)
3) Отменить изменения + удалить worktree
EOF

exit 2
