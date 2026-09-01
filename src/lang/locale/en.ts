// English

export default {
  // src/settings.ts
  'Path to bibliography file': 'Path to bibliography file',
  'Bibliography files': 'Bibliography files',
  'One or more bibliography files (.bib, .json, or .yaml). Vault-relative paths work on all platforms; absolute paths work on desktop only. All files are merged — Zotero wins on conflict. Can be overridden per-note via the "bibliography" frontmatter key.':
    'One or more bibliography files (.bib, .json, or .yaml). Vault-relative paths work on all platforms; absolute paths work on desktop only. All files are merged — Zotero wins on conflict. Can be overridden per-note via the "bibliography" frontmatter key.',
  'Add file': 'Add file',
  'Remove': 'Remove',
  'Prioritize citation completion': 'Prioritize citation completion',
  'Use this plugin\'s citation search for "@" completions. When ON, typing "[@key" (or "[[" followed by "@") searches the Zotero/bibliography index with citekey-first fuzzy matching. When OFF, plain "[@key" yields to another plugin\'s suggester (e.g. ZotLit); "[[@key" is still always handled by this plugin since Obsidian\'s link search can\'t see unimported references.':
    'Use this plugin\'s citation search for "@" completions. When ON, typing "[@key" (or "[[" followed by "@") searches the Zotero/bibliography index with citekey-first fuzzy matching. When OFF, plain "[@key" yields to another plugin\'s suggester (e.g. ZotLit); "[[@key" is still always handled by this plugin since Obsidian\'s link search can\'t see unimported references.',
  'Move the citation autocomplete suggester to the front of Obsidian\'s internal queue so it wins when multiple plugins respond to "@". Disable this if another plugin\'s "@" completions stop working.':
    'Move the citation autocomplete suggester to the front of Obsidian\'s internal queue so it wins when multiple plugins respond to "@". Disable this if another plugin\'s "@" completions stop working.',
  'Save bibliography snapshot': 'Save bibliography snapshot',
  'Save bibliography snapshot for this note': 'Save bibliography snapshot for this note',
  'Citations not in local bibliography snapshot':
    'Citations not in local bibliography snapshot',
  'Save as': 'Save as',
  'Cancel': 'Cancel',
  'Save': 'Save',
  'Path to Pandoc (optional)': 'Path to Pandoc (optional)',
  'Absolute path to the Pandoc executable. When set, Pandoc is used to convert .bib/.yaml files instead of the built-in parser. Leave blank to use the built-in parser (works on all platforms).':
    'Absolute path to the Pandoc executable. When set, Pandoc is used to convert .bib/.yaml files instead of the built-in parser. Leave blank to use the built-in parser (works on all platforms).',
  'Auto-detect Pandoc': 'Auto-detect Pandoc',
  'Browse…': 'Browse…',
  'Search…': 'Search…',
  'The absolute path to your desired bibliography file. This can be overridden on a per-file basis by setting "bibliography" in the file\'s frontmatter.':
    'The absolute path to your desired bibliography file. This can be overridden on a per-file basis by setting "bibliography" in the file\'s frontmatter.',
  'Path to your bibliography file (.bib, .json, or .yaml). Can be vault-relative (e.g. references.bib) or absolute. Can be overridden per-note via the "bibliography" frontmatter key.':
    'Path to your bibliography file (.bib, .json, or .yaml). Can be vault-relative (e.g. references.bib) or absolute. Can be overridden per-note via the "bibliography" frontmatter key.',
  'Path to your bibliography file (.bib, .json, or .yaml). Vault-relative paths (e.g. references.bib) work on all platforms. Absolute paths work on desktop only. On blur, absolute paths inside the vault are automatically shortened to vault-relative. Can be overridden per-note via the "bibliography" frontmatter key.':
    'Path to your bibliography file (.bib, .json, or .yaml). Vault-relative paths (e.g. references.bib) work on all platforms. Absolute paths work on desktop only. On blur, absolute paths inside the vault are automatically shortened to vault-relative. Can be overridden per-note via the "bibliography" frontmatter key.',
  'Select a bibliography file.': 'Select a bibliography file.',
  'Custom citation style': 'Custom citation style',
  'Citation style': 'Citation style',
  'Citation style language': 'Citation style language',
  'Search...': 'Search...',
  'Path to a CSL file. This can be an absolute path or one relative to your vault. This will override the style selected above. This can be overridden on a per-file basis by setting "csl" or "citation-style" in the file\'s frontmatter. A URL can be supplied when setting the style via frontmatter.':
    'Path to a CSL file. This can be an absolute path or one relative to your vault. This will override the style selected above. This can be overridden on a per-file basis by setting "csl" or "citation-style" in the file\'s frontmatter. A URL can be supplied when setting the style via frontmatter.',
  'Path to a CSL file (vault-relative or absolute). Overrides the style selected above. Can be overridden per-note via the "csl" or "citation-style" frontmatter key. A URL can be supplied when setting the style via frontmatter.':
    'Path to a CSL file (vault-relative or absolute). Overrides the style selected above. Can be overridden per-note via the "csl" or "citation-style" frontmatter key. A URL can be supplied when setting the style via frontmatter.',
  'Select a CSL file located on your computer':
    'Select a CSL file located on your computer',
  'Fallback path to Pandoc': 'Fallback path to Pandoc',
  "The absolute path to the Pandoc executable. This plugin will attempt to locate pandoc for you and will use this path if it fails to do so. To find pandoc, use the output of 'which pandoc' in a terminal on Mac/Linux or 'Get-Command pandoc' in powershell on Windows.":
    "The absolute path to the Pandoc executable. This plugin will attempt to locate pandoc for you and will use this path if it fails to do so. To find pandoc, use the output of 'which pandoc' in a terminal on Mac/Linux or 'Get-Command pandoc' in powershell on Windows.",
  'Attempt to find Pandoc automatically':
    'Attempt to find Pandoc automatically',
  'Unable to find pandoc on your system. If it is installed, please manually enter a path.':
    'Unable to find pandoc on your system. If it is installed, please manually enter a path.',
  'Hide links in references': 'Hide links in references',
  'Replace links with link icons to save space.':
    'Replace links with link icons to save space.',
  'Show PDF links in references': 'Show PDF links in references',
  'Add per-entry PDF-open icons to the bibliography and use PDFs as the tooltip link fallback. Off by default: "Open in Zotero" already reveals every attachment, and fetching the PDF list costs a per-citekey Zotero request.':
    'Add per-entry PDF-open icons to the bibliography and use PDFs as the tooltip link fallback. Off by default: "Open in Zotero" already reveals every attachment, and fetching the PDF list costs a per-citekey Zotero request.',
  'Citation decoration': 'Citation decoration',
  'Highlight citation keys with colors and underlines in the editor. Colors and underline styles can be customized with the Style Settings plugin.':
    'Highlight citation keys with colors and underlines in the editor. Colors and underline styles can be customized with the Style Settings plugin.',
  'Preview': 'Preview',
  'citation · wikilink citation · unresolved': 'citation · wikilink citation · unresolved',
  'Show citekey tooltips': 'Show citekey tooltips',
  'When enabled, hovering over citekeys will open a tooltip containing a formatted citation.':
    'When enabled, hovering over citekeys will open a tooltip containing a formatted citation.',
  'Tooltip delay': 'Tooltip delay',
  'Set the amount of time (in milliseconds) to wait before displaying tooltips.':
    'Set the amount of time (in milliseconds) to wait before displaying tooltips.',
  'Validate Pandoc configuration': 'Validate Pandoc configuration',
  Validate: 'Validate',
  'Validation successful': 'Validation successful',
  'Show citekey suggestions': 'Show citekey suggestions',
  'When enabled, an autocomplete dialog will display when typing citation keys.':
    'When enabled, an autocomplete dialog will display when typing citation keys.',
  'Pull bibliography from Zotero': 'Pull bibliography from Zotero',
  'When enabled, bibliography data will be pulled from Zotero rather than a bibliography file. The Better Bibtex plugin must be installed in Zotero.':
    'When enabled, bibliography data will be pulled from Zotero rather than a bibliography file. The Better Bibtex plugin must be installed in Zotero.',
  'When enabled, bibliography data will be pulled from Zotero rather than a bibliography file.':
    'When enabled, bibliography data will be pulled from Zotero rather than a bibliography file.',
  'Use native Zotero API (Zotero 7/8)': 'Use native Zotero API (Zotero 7/8)',
  'Query the standard Zotero local API directly using the native citationKey field introduced in Zotero 7/8. Better BibTeX is not required when this is enabled.':
    'Query the standard Zotero local API directly using the native citationKey field introduced in Zotero 7/8. Better BibTeX is not required when this is enabled.',
  'Zotero port': 'Zotero port',
  "Use 24119 for Juris-M or specify a custom port if you have changed Zotero's default.":
    "Use 24119 for Juris-M or specify a custom port if you have changed Zotero's default.",
  'Render live preview inline citations':
    'Render live preview inline citations',
  'Render reading mode inline citations':
    'Render reading mode inline citations',
  'Convert [@pandoc] citations to formatted inline citations in live preview mode.':
    'Convert [@pandoc] citations to formatted inline citations in live preview mode.',
  'Convert [@pandoc] citations to formatted inline citations in reading mode.':
    'Convert [@pandoc] citations to formatted inline citations in reading mode.',
  'Process citations in links': 'Process citations in links',
  'Include [[@pandoc]] citations in the reference list and format them as inline citations in live preview mode.':
    'Include [[@pandoc]] citations in the reference list and format them as inline citations in live preview mode.',
  'Format link aliases as Pandoc citations':
    'Format link aliases as Pandoc citations',
  "When enabled, aliased citation links like [[@key|see also @@, 6]] are parsed as Pandoc citations and rendered as [see also @key, 6] instead of a plain link label. Use @@ inside the alias as a shortcut for the link's own citekey. Aliases without a citekey (e.g. [[@key|Just a label]]) are left untouched. Requires \"Process citations in links\".":
    "When enabled, aliased citation links like [[@key|see also @@, 6]] are parsed as Pandoc citations and rendered as [see also @key, 6] instead of a plain link label. Use @@ inside the alias as a shortcut for the link's own citekey. Aliases without a citekey (e.g. [[@key|Just a label]]) are left untouched. Requires \"Process citations in links\".",
  'Link citations to literature notes': 'Link citations to literature notes',
  'Make rendered [@citekey] citations clickable links to their literature note. Only applies when a note with the matching citekey name exists — dead-link citations are not linked.':
    'Make rendered [@citekey] citations clickable links to their literature note. Only applies when a note with the matching citekey name exists — dead-link citations are not linked.',
  // src/view.ts
  'Please provide the path to Pandoc in the Linked Citations plugin settings.':
    'Please provide the path to Pandoc in the ScholarWeave plugin settings.',
  'Click to copy': 'Click to copy',
  'Click to jump to citation': 'Click to jump to citation',
  'Copy citekey': 'Copy citekey',
  'Copy reference': 'Copy reference',
  'Copy list': 'Copy list',
  'Unresolved citations': 'Unresolved citations',
  'No citations found in the current document.':
    'No citations found in the current document.',
  References: 'References',
  'This can be overridden on a per-file basis by setting "lang" or "citation-language" in the file\'s frontmatter. A language code must be used when setting the language via frontmatter.':
    'This can be overridden on a per-file basis by setting "lang" or "citation-language" in the file\'s frontmatter. A language code must be used when setting the language via frontmatter.',
  'See here for a list of available language codes':
    'See here for a list of available language codes',
  'Cannot connect to Zotero': 'Cannot connect to Zotero',
  'Start Zotero and try again.': 'Start Zotero and try again.',
  'Libraries to include in bibliography':
    'Libraries to include in bibliography',
  'Please provide the path to your bibliography file in the Linked Citations plugin settings.':
    'Please provide the path to your bibliography file in the ScholarWeave plugin settings.',
  'Refresh bibliography': 'Refresh bibliography',
  'Linked Citations settings': 'ScholarWeave settings',
  'Insert bibliography at cursor': 'Insert bibliography at cursor',
  // src/main.ts commands
  'Sync literature note filenames to citekeys': 'Sync literature note filenames to citekeys',
  'Create literature notes for citations lacking notes (current note)': 'Create literature notes for citations lacking notes (current note)',
  'Create literature notes for citations lacking notes (vault)': 'Create literature notes for citations lacking notes (vault)',
  // src/tooltip.ts
  'No citation found for ': 'No citation found for ',

  'Mobile tap action': 'Mobile tap action',
  'What happens when you tap a citation on mobile. On desktop, hover tooltips are used instead.':
    'What happens when you tap a citation on mobile. On desktop, hover tooltips are used instead.',
  'Show citation info': 'Show citation info',
  'Copy citation to clipboard': 'Copy citation to clipboard',
  'Open link (Zotero → PDF → URL)': 'Open link (Zotero → PDF → URL)',
  'Close': 'Close',

  // src/main.ts
  'Show reference list': 'Show reference list',
  'Compile and export a document (outline or markdown)':
    'Compile and export a document (outline or markdown)',
  'Convert pandoc citations to linked citations (current note)':
    'Convert pandoc citations to linked citations (current note)',
  'Update stale citekeys and literature note filenames (vault)': 'Update stale citekeys and literature note filenames (vault)',
  'Convert pandoc citations to linked citations (vault)': 'Convert pandoc citations to linked citations (vault)',
  'Revert linked citations to pandoc-style citations (current note)':
    'Revert linked citations to pandoc-style citations (current note)',
  'Revert linked citations to pandoc-style citations (vault)':
    'Revert linked citations to pandoc-style citations (vault)',

  'Purge citekey rename history': 'Purge citekey rename history',
  'Update unresolved citations': 'Update unresolved citations',

  // src/view.ts
  'Open literature note': 'Open literature note',
  'Create literature note': 'Create literature note',
  'Literature notes folder': 'Literature notes folder',
  'Folder where the plugin\'s own literature notes are created (vault-relative). Leave blank to create at the vault root. Used for the "Create literature note" button when ZotLit is not handling creation. ZotLit uses its own configured folder.':
    'Folder where the plugin\'s own literature notes are created (vault-relative). Leave blank to create at the vault root. Used for the "Create literature note" button when ZotLit is not handling creation. ZotLit uses its own configured folder.',
  'Create literature notes with ZotLit':
    'Create literature notes with ZotLit',
  'When ZotLit is available, the tooltip\'s "Create literature note" button creates the note with ZotLit\'s templates instead of the plugin\'s basic template. Falls back to the plugin template when ZotLit is absent or this is off.':
    'When ZotLit is available, the tooltip\'s "Create literature note" button creates the note with ZotLit\'s templates instead of the plugin\'s basic template. Falls back to the plugin template when ZotLit is absent or this is off.',
  'Open in Zotero': 'Open in Zotero',
  'Filter references…': 'Filter references…',

  // src/settings.tsx — Book Compiler (outline → markdown → docx)
  'Path to Python 3 (for Document Compiler)': 'Path to Python 3 (for Document Compiler)',
  'Absolute path to the python3 interpreter used by the "Compile outline…" and "Compile + export to docx" commands. Leave blank to auto-detect (python3 on PATH, then common install locations).':
    'Absolute path to the python3 interpreter used by the "Compile outline…" and "Compile + export to docx" commands. Leave blank to auto-detect (python3 on PATH, then common install locations).',
  'Docx export templates directory (optional)': 'Docx export templates directory (optional)',
  'Directory of your .docx export templates. Vault-relative (e.g. Export Templates) or absolute. Leave blank to use <vault>/Export Templates/, then the templates bundled with the plugin.':
    'Directory of your .docx export templates. Vault-relative (e.g. Export Templates) or absolute. Leave blank to use <vault>/Export Templates/, then the templates bundled with the plugin.',
  'Default output folder for compiled/exported documents (optional)': 'Default output folder for compiled/exported documents (optional)',
  'Vault-relative folder where "Compile and export a book" puts the compiled markdown and docx. Leave blank to use the source file\'s own folder. Can be changed per-export in the modal.':
    'Vault-relative folder where "Compile and export a book" puts the compiled markdown and docx. Leave blank to use the source file\'s own folder. Can be changed per-export in the modal.',

  'This entry exists in both your .bib file and Zotero. Zotero data is shown.':
    'This entry exists in both your .bib file and Zotero. Zotero data is shown.',
  'ZotLit detected — [@key completions are handled by ZotLit. This plugin still provides bare @key suggestions (outside brackets) and for .bib file entries.':
    'ZotLit detected — [@key completions are handled by ZotLit. This plugin still provides bare @key suggestions (outside brackets) and for .bib file entries.',
};
