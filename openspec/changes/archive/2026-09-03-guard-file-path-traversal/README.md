# guard-file-path-traversal

Closes radionest/clarinet#521. Two layers against path traversal in rendered file paths:
a runtime guard in `clarinet/files/_template.py` (`assert_path_safe_value` on each substituted
value, `join_within` on the working-dir join) wired into every `Files` render→join site, and a
config-load pattern validator on `FileDefinitionRead` that rejects unsafe literal text and —
breaking — bans `{data.*}` placeholders from file patterns entirely.

The guard closes the vulnerability on its own; the `{data.*}` ban is scope reduction, deferring
the non-traversal corner cases (length, reserved basenames, unicode, PHI in filenames) to a
follow-up PR that reintroduces the placeholder. It is deliberately shaped for that reversal —
see Q6 and Section 4 of `brainstorm.md`.
