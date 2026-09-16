# Commands

Open the command palette with `Cmd/Ctrl+P` and type "ScholarWeft". The names below are the command names; Obsidian shows them grouped under the plugin name.

## Citations and references

| Command | Scope | Notes |
|---|---|---|
| Show reference list | — | Open the reference sidebar |
| Insert bibliography at cursor | Current note | Insert the formatted reference list |
| Save bibliography snapshot for this note | Current note | Save the note's citations as a `.bib` file |
| Convert pandoc citations to linked citations (current note) | Current note | `[@key]` → `[[@key]]` |
| Convert pandoc citations to linked citations (vault) | Vault | For every note |
| Revert linked citations to pandoc-style citations (current note) | Current note | `[[@key]]` → `[@key]` |
| Revert linked citations to pandoc-style citations (vault) | Vault | For every note |
| Purge citekey rename history | — | Clear the stored rename records |

See [Citations](./citations.md) and [Linked Citations](./linked-citations.md).

## Literature notes

| Command | Scope | Notes |
|---|---|---|
| Create literature notes for citations lacking notes (current note) | Current note | Uses ZotLit templates when available |
| Create literature notes for citations lacking notes (vault) | Vault | For every note |
| Update stale citekeys and literature note filenames (vault) | Vault | Apply accumulated citekey renames |

See [Literature Notes](./literature-notes.md).

## Document import and export (desktop only)

| Command | Notes |
|---|---|
| Import a Word or ODT document with Zotero citations | Opens the import dialogue — see [Document Import and Export](./import-export.md) |
| Compile and export a book, article, or other document (outline or markdown) | Opens the export dialogue |

These call external tools; see [Dependencies](./dependencies.md) for what each requires.
