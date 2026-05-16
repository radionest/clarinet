#!/usr/bin/env bash
# Lint guard: reject direct `.working_folder` access in services / api.
#
# After the FileRepository refactor (P29 in .claude/rules/pr-review.md),
# path resolution is owned by FileRepository — `.working_folder` no longer
# exists on RecordRead / SeriesRead, and re-introducing it in code under
# `clarinet/services/` or `clarinet/api/` is a regression we want to catch
# before merge.
#
# Exempt contexts (the regex below filters them):
#   - lines that build the path through the canonical FileRepository
#     API on the same line: `FileRepository(record).working_dir` /
#     `.working_dirs_all` / `.resolve_file`, or the
#     `FileRepository.resolve_with_fallback` staticmethod. A bare
#     mention of `FileRepository` is NOT enough — otherwise
#     `record.working_folder = FileRepository(rec).working_dir` would
#     slip through even though the LHS is exactly what P29 forbids.
#   - the in-Slicer-subprocess helper code (`SlicerHelper.working_folder`,
#     `self.working_folder`) — those are different attributes living
#     entirely inside the Slicer Python environment, not the model field
#   - lines marked `# noqa: working_folder` (escape hatch for legitimate
#     uses we have not yet thought of)
#
# Slicer-context.py legitimately writes `context["working_folder"] = ...`
# into a dict — that is a string key, not a model attribute, and is
# captured by the FileRepository exception (the line constructs the
# value through FileResolver in the same statement).

set -euo pipefail

ROOT="${1:-$(pwd)}"
PATTERN='\.working_folder'

# Limit the scope to backend code where the model-attribute regression
# would actually appear; tests, frontend, CLI migrations are allowed.
SCAN_PATHS=(
  "${ROOT}/clarinet/services"
  "${ROOT}/clarinet/api"
  "${ROOT}/clarinet/repositories"
)

# Files allowed to keep historical mentions in docstrings / module
# headers; they are documentation of past behaviour, not live code.
# Match against the path tail so this script works from any CWD.
DOCSTRING_ALLOWLIST_RE='services/(slicer/helper\.py|common/storage_paths\.py|slicer/context\.py)$'

found_violations=0
violations=""

for path in "${SCAN_PATHS[@]}"; do
  if [[ ! -d "${path}" ]]; then
    continue
  fi
  while IFS= read -r line; do
    file="${line%%:*}"
    rest="${line#*:}"
    line_no="${rest%%:*}"
    code="${rest#*:}"

    # Writing to `.working_folder` is always a regression (the field
    # doesn't exist on the model anymore), even if the RHS uses the
    # canonical FileRepository pattern. Only self/SlicerHelper writes
    # or an explicit noqa hatch are allowed.
    if echo "${code}" | grep -Eq '\.working_folder\s*='; then
      if echo "${code}" | grep -Eq '# noqa: working_folder|self\.working_folder|SlicerHelper'; then
        continue
      fi
      violations+="${file}:${line_no}:${code}"$'\n'
      found_violations=1
      continue
    fi
    # Read access — see header for the rationale of each exempt branch.
    canonical_fr='FileRepository\([^)]*\)\.(working_dir|working_dirs_all|resolve_file)|FileRepository\.resolve_with_fallback'
    if echo "${code}" | grep -Eq "# noqa: working_folder|self\.working_folder|SlicerHelper|${canonical_fr}"; then
      continue
    fi
    # Skip docstring mentions in pre-approved files
    if [[ "${file}" =~ ${DOCSTRING_ALLOWLIST_RE} ]]; then
      continue
    fi
    violations+="${file}:${line_no}:${code}"$'\n'
    found_violations=1
  done < <(grep -rn "${PATTERN}" "${path}" 2>/dev/null || true)
done

if [[ "${found_violations}" -eq 1 ]]; then
  echo "ERROR: direct .working_folder access detected — use FileRepository(record).working_dir" >&2
  echo "  (or add '# noqa: working_folder' on the line if the use is legitimate)" >&2
  echo "" >&2
  echo "${violations}" >&2
  echo "See P29 in .claude/rules/pr-review.md." >&2
  exit 1
fi

exit 0
