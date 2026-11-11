# Shared templates

Every `<key>.json` file in this folder is a **built-in template** on every board: it
appears as a card on the home page ("Start from a template") after the board's next
software update. Nothing else has to change.

## Add a template

1. Build it on a board with a developer account, in the template editor.
2. On that board, export it:
   ```bash
   cd /opt/irivai-systor
   python3 scripts/export_template.py --list                 # see the keys
   python3 scripts/export_template.py <key> --out /tmp/      # writes /tmp/<key>.json
   ```
   Add `--publish` if the template is still a draft (drafts are hidden from users).
3. Copy `<key>.json` into this folder, commit and push.
4. Optional: card pictures go in `core/static/img/templates/<key>.png`
   (or `<key>-1.png`, `<key>-2.png` ... for a slider).

## The file

The same fields as a `[formats.<key>]` section of `config/formats.toml`, as JSON:
`label` and `hint` (required label), `category`, `model`, `classes`, `zone`
(`mode`, `hint`), `workflows_json`, `dashboard_json`, `tables_json` (the database
tables it creates - columns only, never rows) and `settings_json`.

- The file name is the key: lowercase letters, digits and `_`, starting with a letter.
- Each file is checked the same way as the template editor checks a template. A file
  that does not parse or check is skipped (and named in the log); the others still load.
- A template saved on a board with the same key wins on that board.
- To remove a template from every board, delete its file here and publish an update.
