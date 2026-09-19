# Literature Notes

ScholarWeft treats each source's literature note (normally `@citekey.md`) as the graph node its citations link to. This page covers where those notes live and how they are created.

## Folder

**Settings → Literature note import → Literature notes folder** sets where notes are created and found (e.g. `Bibliographic notes`). ScholarWeft looks a note up by its citekey filename.

Using a dedicated folder keeps citations resolvable and tidy, but any folder works as long as the filename is `@citekey`.

## Creating notes

- **Create literature notes for citations lacking notes (current note)** and **…(vault)** create a note for every cited work that doesn't already have one.
- Individual notes can also be created from the reference sidebar, a citation tooltip, or an entry's "Create literature note" button.

Creating notes needs **Zotero** for citekey and metadata lookup. No live Zotero field is placed in the note itself.

## ZotLit

If [ZotLit](https://github.com/PKM-er/obsidian-zotlit) is installed, ScholarWeft uses it to create literature notes and to format imported PDF annotations. Enable **Create literature notes with ZotLit**.

ScholarWeft can also install a curated set of ZotLit templates: **Install and use ScholarWeft's ZotLit import templates** writes them to `sw-zotlit-templates/` and points ZotLit's template folder there, leaving your own templates untouched. See [ZotLit Import Templates](./zotlit-import-templates.md).

## Bringing your Zotero notes into the literature note

ZotLit imports Zotero **notes** as separate files and can only *link* to them from a template — it never hands a template the note's text. If you keep your reading notes in Zotero and want them *inside* the literature note (as the old Zotero Integration plugin did), run:

**Insert Zotero notes into literature notes (vault)**

It goes through every literature note, fetches the matching item's **child notes** from Zotero, and inserts their text as Markdown directly under `## Notes`, above ZotLit's `%%zt-managed%%` region. Nothing inside the managed region is touched, so ZotLit's later updates don't overwrite it.

- **Only notes with an empty `## Notes` section are filled.** If a section already has content (your own writing, or earlier imported notes), it is left alone — ScholarWeft never overwrites or appends over what's there. The summary tells you how many were skipped and how many had no Zotero notes; skipped paths are also printed to the developer console (Ctrl/Cmd+Shift+I).
- After a run, a marker comment is added at the very **end** of the file (`<!-- sw-zn: KEY … -->`) recording which Zotero notes were inserted, so re-running is a no-op.
- The command is also run automatically right after **Create literature notes for citations lacking notes**, so notes created from within ScholarWeft get their Zotero notes at once.

## Updating citekeys

When a Zotero citekey changes, **Update stale citekeys and literature note filenames (vault)** updates citations across the vault and renames the matching literature notes to the new citekey (after showing a preview of what will change). **Purge citekey rename history** clears the stored rename records once you no longer need them.

See [Commands](./commands.md) and [Dependencies](./dependencies.md).
