#!/bin/bash
# PreToolUse hook for Bash: блокирует три анти-паттерна.
# 1. Команды, начинающиеся с '#' (комментарий не выполняется — потерянный round-trip).
# 2. Leading `cat` / `grep` / `find` — у нас есть Read / Grep / Glob, они быстрее
#    и не загружают результат stdout-выводом. Heredoc (`cat <<`) разрешён.
# 3. Test-команды (pytest, make test*, uv run pytest) в pipe — буферизуют stdout,
#    ломают run_in_background. Должны редиректиться в /tmp/test-<worktree>.txt.

INPUT=$(cat)
COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
[ -z "$COMMAND" ] && exit 0

# Escape-hatch: команда заканчивается на '# bash-ok' — пропускаем все проверки.
# Использовать только когда действительно нет альтернативы через Read/Grep/Glob.
if printf '%s' "$COMMAND" | grep -qE '#[[:space:]]*bash-ok[[:space:]]*$'; then
  exit 0
fi

# --- 1. Leading '#' ---
if [[ "$COMMAND" =~ ^[[:space:]]*# ]]; then
  cat >&2 <<'EOF'
BLOCKED: команда Bash начинается с '#' — комментарий не выполняется и тратит round-trip.
Удали leading-комментарий, или вынеси описание в поле `description` tool-вызова.
EOF
  exit 2
fi

# --- 2. Leading cat/grep/find ---
# Снимаем prefix'ы вроде `timeout 120`, `env X=Y`, `VAR=val`, `nohup`, `stdbuf -o0`, `time`.
STRIPPED=$(printf '%s' "$COMMAND" | sed -E 's/^[[:space:]]+//')
while :; do
  case "$STRIPPED" in
    timeout\ [0-9]*[smh]\ *|timeout\ [0-9]*\ *)
      STRIPPED="${STRIPPED#* }"; STRIPPED="${STRIPPED#* }" ;;
    env\ *|nohup\ *|time\ *)
      STRIPPED="${STRIPPED#* }" ;;
    stdbuf\ -*\ *)
      STRIPPED="${STRIPPED#* }"; STRIPPED="${STRIPPED#* }" ;;
    [A-Z_]*=*\ *)
      STRIPPED="${STRIPPED#* }" ;;
    *) break ;;
  esac
done

FIRST_WORD="${STRIPPED%% *}"

case "$FIRST_WORD" in
  cat)
    # Разрешаем heredoc: `cat << EOF`, `cat <<'EOF'`, `cat <<-EOF`.
    if ! [[ "$STRIPPED" =~ ^cat[[:space:]]+\<\<- ]] && \
       ! [[ "$STRIPPED" =~ ^cat[[:space:]]+\<\< ]]; then
      cat >&2 <<'EOF'
BLOCKED: leading `cat <file>` — у тебя есть лучшие альтернативы.
  `cat file | tail -N`  →  `tail -N file`   (leading tail не блокируется)
  `cat file | grep X`   →  Grep tool (или `grep X file` — но лучше Grep)
  `cat file` целиком    →  Read tool (поддерживает offset/limit)
Разрешено: `cat << EOF ...` (heredoc), `что-то | cat` (cat не ведущий).
Если действительно нужно — добавь `# bash-ok` в конец команды.
EOF
      exit 2
    fi
    ;;
  grep)
    cat >&2 <<'EOF'
BLOCKED: leading `grep` — используй Grep tool. Он умеет:
  -n  -B/-A/-C  -r/-rn  multiline  filter по типу (`type="py"`)  count (`output_mode="count"`)
Pipe-формы разрешены: `git log | grep`, `jq ... | grep`, `gh pr view | grep`.
Если действительно нужен сырой grep — добавь `# bash-ok` в конец команды.
EOF
    exit 2
    ;;
  find)
    cat >&2 <<'EOF'
BLOCKED: leading `find` — используй Glob tool (pattern: `**/*.py`, `tests/**/test_*.py`).
Для `find ... -exec` / `-delete` или редких трюков — добавь `# bash-ok` в конец команды.
EOF
    exit 2
    ;;
esac

# --- 3. Test-команда в pipe ---
# Ловим pytest / py.test / make test* / uv run pytest. Игнорируем `||` (boolean OR).
if printf '%s' "$COMMAND" | grep -qE '(\b(pytest|py\.test)\b|\bmake[[:space:]]+test|\buv[[:space:]]+run[[:space:]]+pytest)'; then
  CLEANED=$(printf '%s' "$COMMAND" | sed 's/||/__OR__/g')
  if printf '%s' "$CLEANED" | grep -q '|'; then
    cat >&2 <<'EOF'
BLOCKED: test-команда передаётся в pipe (`|`, `| tee`, `| head`, `| tail`).
Pipe буферизует stdout и ломает run_in_background. Шаблон:
  timeout 300 make test-fast > /tmp/test-<worktree>.txt 2>&1
Потом отдельной командой: `tail -N /tmp/test-...txt` или Read tool.
Если редкое исключение — добавь `# bash-ok` в конец команды.
EOF
    exit 2
  fi
fi

exit 0
