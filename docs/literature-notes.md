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

## Updating citekeys

When a Zotero citekey changes, **Update stale citekeys and literature note filenames (vault)** updates citations across the vault and renames the matching literature notes to the new citekey (after showing a preview of what will change). **Purge citekey rename history** clears the stored rename records once you no longer need them.

See [Commands](./commands.md) and [Dependencies](./dependencies.md).
