# Linked Citations

The heart of this plugin is its linked citation syntax, which allows you to integrate all citations as nodes in your Obsidian thought universe while also formatting them for display and export in publication-ready documents. This syntax simply takes pandoc’s citation syntax, places it in the link’s alias, and optionally allows you to use @ as a proxy for the citation key, since you’ve already mentioned it in the link. Although you can write something after the @, including the citekey itself, the parser only sees the @ and expands it back into `@citekey`, so anything beyond the @ is redundant.

This plugin can parse conventional pandoc `[@citekey]` citations, and it has commands to convert citations in a note or the whole vault between the two formats: `Convert pandoc citations to linked citations (current note)` / `… (vault)` and `Revert linked citations to pandoc-style citations (current note)` / `… (vault)`. See [Commands](./commands.md).

None of this needs external tools — linked citations work without Pandoc or Zotero. (Zotero or a bibliography file is only needed to *resolve* the references; see [Dependencies](./dependencies.md).)

## Linked citation syntax

| Wikilink form           | Rendered as                    | Pandoc equivalent           |
| ----------------------- | ------------------------------ | --------------------------- |
| `[[@key]]`              | (Author Year)                  | `[@key]`                    |
| `[[@key\|@]]`           | (Author Year)                  | `[@key]`                    |
| `[[@key\|@ -]]`         | Author (Year)                  | `@key` (narrative)          |
| `[[@key\|-@]]`          | (Year)                         | `[-@key]` (suppress author) |
| `[[@key\|see @, p. 6]]` | (see Author Year, p. 6)        | `[see @key, p. 6]`          |
| `[[@key\|-@, p. 6]]`    | (Year, p. 6)                   | `[-@key, p. 6]`             |
| `[ [[@a]]; [[@b]] ]`    | (Author A Year; Author B Year) | `[@a; @b]` (multi-work)     |

Inside an alias, `@` is a proxy for the link’s own citekey. The convert commands translate between linked and pandoc forms losslessly.

See the [pandoc citation syntax](https://pandoc.org/demo/example33/8.20-citation-syntax.html#citation-syntax) for the underlying format.
