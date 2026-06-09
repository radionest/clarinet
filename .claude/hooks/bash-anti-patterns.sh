#!/bin/bash
# PreToolUse hook for Bash: blocks three anti-patterns.
# 1. Commands starting with '#' (the comment is not executed — a wasted round-trip).
# 2. Leading `cat` / `grep` / `find` — Read / Grep / Glob tools are faster and
#    don't flood the transcript with stdout. Heredoc (`cat <<`) is allowed.
# 3. Test commands (pytest, make test*, uv run pytest) in a pipe — they buffer
#    stdout and break run_in_background. Redirect to /tmp/test-<worktree>.txt instead.

INPUT=$(cat)
COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
[ -z "$COMMAND" ] && exit 0

# Escape hatch: command ends with '# bash-ok' — skip all checks.
# Use only when Read/Grep/Glob genuinely cannot do the job.
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
# Strip prefixes like `timeout 120`, `env X=Y`, `VAR=val`, `nohup`, `stdbuf -o0`, `time`.
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
    # Allow any heredoc form: `cat << EOF`, `cat <<'EOF'`, `cat > file << 'EOF'`.
    if ! [[ "$STRIPPED" =~ \<\< ]]; then
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

# --- 3. Test command in a pipe ---
# Match pytest / py.test / make test* / uv run pytest only in command position
# (start of line or after ; & | parenthesis, with optional env/timeout prefixes) —
# a mere argument like `git log --grep=pytest` must not trigger. `||` is ignored (boolean OR).
if printf '%s' "$COMMAND" | grep -qP '(^|[;&|(])\s*((timeout|env|nohup|time|stdbuf)\s+\S+\s+|\w+=\S*\s+)*(pytest\b|py\.test\b|uv\s+run\s+pytest\b|make\s+test)'; then
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
