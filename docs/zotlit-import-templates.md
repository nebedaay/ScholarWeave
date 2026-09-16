# ZotLit Import Templates

In ScholarWeft's settings you can click **Install and use ScholarWeft's ZotLit import templates**. The templates are packaged in the plugin, so nothing is downloaded; they are written to `sw-zotlit-templates/` in your vault, which is set as your ZotLit template folder so your existing templates are left untouched.

The templates follow ScholarWeft's "link everything" philosophy and assume you curate your Zotero items and annotations with Obsidian in mind:

- All annotations appear in their original colour in callout boxes, so you can colour-code annotation types; each colour is linked to a colour note where you can explain its use and link onward.
- Zotero `tags` and `related` fields become links in the literature note's `related` property.
- `tags` in PDF-annotation comments also become links inside the comment callout.
- Existing wikilinks in annotation comments are preserved, so imported notes slot straight into your note network.
- Rectangular and ink annotations are imported as attachments and displayed as images.
- A comment beginning with `+` marks the highlighted text as a continuation of the previous highlight, appended after " … ", so you can combine quotes that span pages or parts of a longer passage.

Requires ZotLit (and Zotero). See [Literature Notes](./literature-notes.md) and [Dependencies](./dependencies.md).
