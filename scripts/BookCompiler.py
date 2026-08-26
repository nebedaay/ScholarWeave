## Started with ChatGPT, ironed out with DeepSeek
# Converts a bulleted outline of Obsidian notes to a single compiled file.
#   - Top-level bullets are HEADINGS: plain text ("- Preface") or a numbered
#     chapter ("- @@ Hidden Treasures"). A top-level link ("- [[Note]]") is
#     treated as a legacy section heading with the note's content.
#   - Links under a heading are SECTIONS (##), links under those are
#     SUBSECTIONS (###), etc. — each rendered from the linked note.
#   - Initial ordering numbers are stripped from filename-provided titles
#     ("1 Introduction" → "Introduction", "7-1 Lallā Manāna..." → "Lallā
#     Manāna..."), since they only keep files in order in the folder.
# It renames and renumbers footnotes. The default is to restart numbering
# with each chapter. Use the --global-footnotes flag for continuous numbering.
#
# The same command handles an outline OR an already-compiled markdown note:
#   - template: compile-<name> in YAML marks an outline (effective template
#     becomes <name> after compilation; the compiled output keeps <name>)
#   - otherwise, structure detection: a top-level bullet list with no
#     # headings is an outline
# In both cases "--export" exports to docx via the Linked Citations pipeline.

import re
import os
from pathlib import Path
from collections import defaultdict
import argparse

# ── vault / plugin path resolution ───────────────────────────────────────────
# The script lives in <vault>/.obsidian/plugins/linked-citations/scripts/
# (or a copy/symlink of it). The vault root is derived from THIS FILE's real
# location (os.path.realpath resolves symlinks), so the script works from any
# cwd, whether run directly, via a symlink in <vault>/scripts/, or from the
# plugin's commands.

_PLUGIN_SCRIPTS_DIR = Path(os.path.dirname(os.path.realpath(os.path.abspath(__file__))))
_PLUGIN_DIR = _PLUGIN_SCRIPTS_DIR.parent          # …/plugins/linked-citations
_OBSIDIAN_DIR = _PLUGIN_DIR.parent                # …/plugins
_VAULT_ABS = _OBSIDIAN_DIR.parent                 # the vault root

# Fallback for running the script standalone from a copy elsewhere: if the
# derived vault root doesn't look like one (no .obsidian sibling), fall back
# to the user's known vault.
if not (_VAULT_ABS / '.obsidian').exists():
    _VAULT_ABS = Path("~/Documents/Obsidian Vault").expanduser()

def vault_rel(*parts):
    """Return a vault path (absolute, anchored at the vault root).
    The name is historical; the path is absolute so rglob/reads work
    regardless of the caller's working directory."""
    return str(Path(_VAULT_ABS, *parts))

def vault_rel_to_cwd(*parts):
    """Return a vault path expressed relative to the current working
    directory. Used ONLY for subprocess args (pandoc/merge) where the
    process runs in the vault and relative paths avoid permission prompts."""
    rel = os.path.relpath(Path(_VAULT_ABS, *parts), os.getcwd())
    return rel

def plugin_script_path(*parts):
    """A path inside the plugin's scripts/ dir (lua filters, merge script,
    converter). Absolute, anchored at the plugin's real location."""
    return str(Path(_PLUGIN_SCRIPTS_DIR, *parts))

def plugin_template_path(name):
    """An Export Template inside the plugin's templates dir (fallback)."""
    return str(Path(_PLUGIN_DIR, 'templates', name))

def get_indent_level(line: str) -> int:
    line_expand = line.replace("\t", "    ")
    spaces = len(line_expand) - len(line_expand.lstrip(" "))
    return spaces // 4

def extract_yaml(text: str):
    yaml_match = re.match(r"(?s)^---\n(.*?)\n---\n", text)
    if yaml_match:
        return yaml_match.group(1), text[yaml_match.end():]
    return None, text

def sanitize_title(title: str) -> str:
    # Keep alphanumeric and underscores only
    return re.sub(r"[^\w]", "_", title.strip())

def strip_ordering_number(title: str) -> str:
    """Strip a leading ordering number from a filename-provided title.
    Handles '1 Title', '7-1 Title', '1.5 Title', '12 Title', '7–1 Title'.
    """
    return re.sub(r'^\d+(?:[-–.\u2013]\d+)*\s+', '', title, count=1)

def extract_footnotes_from_text(text: str):
    """Extract all footnotes (anchors and definitions) from a text."""
    # Pattern for footnote anchors (NOT followed by colon)
    anchor_pattern = re.compile(r"\[\^([^\]]+)\](?!:)")
    # Pattern for footnote definitions
    def_pattern = re.compile(r"\[\^([^\]]+)\]:\s*(.*?)(?=\n\[\^|\n\n|\Z)", re.DOTALL)
    
    # Find all anchors in order with their positions
    anchors = []
    for match in anchor_pattern.finditer(text):
        anchors.append({
            'name': match.group(1),
            'start': match.start(),
            'end': match.end(),
            'full_text': match.group(0)
        })
    
    # Find all definitions in order
    definitions = []
    for match in def_pattern.finditer(text):
        definitions.append({
            'name': match.group(1),
            'content': match.group(2).strip(),
            'start': match.start(),
            'end': match.end()
        })
    
    return anchors, definitions

def process_chapter_footnotes(chapter_text: str, chapter_prefix: str, chapter_name: str, 
                              global_footnotes: bool = False, global_counter: int = 0):
    """Process all footnotes in a chapter at once."""
    
    print(f"\n--- Processing {chapter_name} ---")
    
    # Extract all anchors and definitions from the chapter text
    anchors, definitions = extract_footnotes_from_text(chapter_text)
    
    print(f"  Found {len(anchors)} anchors in order")
    print(f"  Found {len(definitions)} definitions in order")
    
    # Pair anchors with definitions in order of appearance
    pair_count = min(len(anchors), len(definitions))
    
    if len(anchors) != len(definitions):
        print(f"  WARNING: Mismatch: {len(anchors)} anchors vs {len(definitions)} definitions")
    
    # Create list of new footnote names in order
    if global_footnotes:
        # Use global counter for sequential numbering across all chapters
        new_names = [f"{global_counter + i + 1}" for i in range(pair_count)]
        print(f"  Using global numbering: starting from Global_{global_counter + 1}")
    else:
        # Use chapter-specific numbering
        new_names = [f"{chapter_prefix}_{i+1}" for i in range(pair_count)]
        print(f"  Using chapter numbering: starting from {chapter_prefix}_1")
    
    # Process the text from the end to avoid position shifting
    # Sort anchors in reverse order by position
    sorted_anchors = sorted(anchors[:pair_count], key=lambda x: x['start'], reverse=True)
    
    # Replace anchors in reverse order (from last to first)
    modified_text = chapter_text
    for i, anchor in enumerate(sorted_anchors):
        # Find the corresponding new name (need to map from original order)
        original_index = anchors.index(anchor)
        new_name = new_names[original_index]
        
        old_anchor = anchor['full_text']
        new_anchor = f"[^{new_name}]"
        
        # Replace this specific instance
        modified_text = modified_text[:anchor['start']] + new_anchor + modified_text[anchor['end']:]
    
    # Remove all footnote definitions from the chapter text
    def_pattern = re.compile(r"\[\^([^\]]+)\]:\s*(.*?)(?=\n\[\^|\n\n|\Z)", re.DOTALL)
    modified_text = def_pattern.sub("", modified_text)
    
    # Create footnote definitions for this chapter (in order)
    chapter_notes = []
    print(f"\n  Creating footnote definitions:")
    for i in range(pair_count):
        new_name = new_names[i]
        content = definitions[i]['content']
        chapter_notes.append(f"[^{new_name}]: {content}")
        print(f"    Created: [^{new_name}]: {content[:50]}...")
    
    return modified_text, chapter_notes, pair_count

def resolve_note_path(note_name: str):
    """Find a vault note by name (with .md extension). Returns Path or None.
    Uses vault-relative paths only.

    Normalizes both the search target and every filesystem entry to NFC before
    comparing. macOS APFS preserves whatever Unicode normalization was used
    when a file was created, so a note whose filename is NFD on disk won't be
    found by rglob() when the wikilink text is NFC (or vice versa). Comparing
    NFC-normalized forms handles both cases correctly.
    """
    import unicodedata
    target = unicodedata.normalize('NFC', note_name + '.md')
    matches = [
        p for p in Path(vault_rel()).rglob('*.md')
        if unicodedata.normalize('NFC', p.name) == target
    ]
    if not matches:
        raise FileNotFoundError(f"Note '{note_name}' not found in vault: {_VAULT_ABS}")
    # Prefer the shallowest match (fewest path parts) — mirrors the old
    # behavior while being more predictable when names repeat.
    return min(matches, key=lambda p: len(p.parts))

def rewrite_poetry_callouts(content: str) -> str:
    """Rewrite Obsidian poetry callouts so pandoc preserves the couplet
    structure. Pandoc flattens '>-' (verse start) and '>\t-' (second
    hemistich) into one Para where both become plain Str('-') — the lua
    filter can then no longer tell them apart and emits each hemistich as
    its own paragraph.

    Fix: join each verse's two hemistichs onto ONE line with an explicit
    '<br>' marker, which pandoc preserves as a RawInline and the filter
    treats as a hemistich separator:
      English:  >- h1  +  >\t- h2   →  >- h1<br>h2
      Arabic:   >\t- h1 + >\t- h2  →  >\t- h1<br>h2  (consecutive pairs)
    """
    def repl(m):
        marker = m.group(1)
        body = m.group(2)
        lines = body.splitlines()
        is_arabic = 'arabic' in marker.lower()
        out_lines = []
        verse_buf = None
        for raw in lines:
            line = raw.lstrip('>').strip()
            if not line:
                continue
            is_tab = raw.startswith('>\t')
            if is_arabic:
                # Consecutive \t- lines form verses in pairs: (1,2), (3,4)…
                if verse_buf is not None:
                    verse_buf += '<br>' + line.lstrip('- ').strip()
                    out_lines.append(verse_buf)
                    verse_buf = None
                else:
                    verse_buf = line.lstrip('- ').strip()
                continue
            # English: tab line = second hemistich of current verse.
            if is_tab and verse_buf is not None:
                verse_buf += '<br>' + line.lstrip('- ').strip()
                continue
            if verse_buf is not None:
                out_lines.append(verse_buf)
            verse_buf = line.lstrip('- ').strip()
        if verse_buf is not None:
            out_lines.append(verse_buf)
        # Keep a trailing newline so a body paragraph that follows the
        # callout doesn't get glued to the last verse line.
        return marker.rstrip() + '\n>' + '\n>'.join(out_lines) + '\n'
    return re.sub(r'(>?\s*\[!(?:arabic-)?poetry(?:-callout)?\][^\n]*\n)'
                  r'((?:\s*>.*\n?)+)', repl, content)

def resolve_attachment_path(filename: str):
    """Find a vault attachment (image etc.) by exact filename. Returns Path
    or None. Obsidian resolves embeds against the whole vault."""
    matches = list(Path(vault_rel()).rglob(filename))
    if not matches:
        return None
    return min(matches, key=lambda p: len(p.parts))

def resolve_embed_links(content: str) -> str:
    """Rewrite Obsidian image embeds (![[name.ext]]) to vault-relative paths
    so pandoc can fetch them. Without this, pandoc looks relative to the
    compiled file's directory and replaces the image with its filename.
    Non-image embeds (![[other note]]) are left untouched (wikilinks).
    """
    img_ext = r'(?:png|jpe?g|gif|bmp|tiff?|webp|svg)'
    def repl(m):
        target = m.group(1).strip()
        if not re.search(rf'\.{img_ext}$', target, re.IGNORECASE):
            return m.group(0)
        # Obsidian alt text: ![[img.jpg|alt text]] → alt goes into the alt
        # syntax so the caption/description is meaningful.
        alt = ''
        if '|' in target:
            target, alt = target.rsplit('|', 1)
            target, alt = target.strip(), alt.strip()
        resolved = resolve_attachment_path(target)
        if resolved is None:
            return m.group(0)
        # Path relative to the vault root (which is the cwd).
        rel = os.path.relpath(resolved, os.getcwd())
        if alt:
            return f'![{alt}]({rel})'
        return f'![{target}]({rel})'
    return re.sub(r'!\[\[([^\]]+)\]\]', repl, content)

def compile_note(note_name: str, depth: int, chapter_number=None):
    """Compile a linked note into a section heading + content.
    The heading title comes from, in order:
      1. the note's YAML `title:` property (allows colons and other
         punctuation the filename can't carry — e.g. "Conclusion: Cultivating
         Pious Women, Cultivating a Pious Society")
      2. the note's own first # heading
      3. the note NAME with a leading ordering number stripped
         ('1 Introduction' → 'Introduction')
    """
    note_file = resolve_note_path(note_name)
    text = note_file.read_text(encoding="utf-8")

    lines = text.splitlines()
    heading_line = next((l for l in lines if re.match(r"^#+\s+", l) and not l.startswith("#tags")), None)

    # 1. YAML title property (same-line value only, quoted or bare).
    yaml_title = None
    m = re.search(r'^title:[^\S\n]*(?:["\']([^"\']+)["\']|(\S.*?))(?:\s*#.*)?$',
                  text, re.M)
    if m and (m.group(1) or m.group(2)):
        yaml_title = (m.group(1) or m.group(2)).strip()

    if yaml_title:
        original_heading = yaml_title
    elif heading_line:
        original_heading = heading_line.lstrip("#").strip()
    else:
        original_heading = strip_ordering_number(note_name)

    if chapter_number is not None:
        section_heading = "#" * depth + f" Chapter {chapter_number}: {original_heading}"
    else:
        section_heading = "#" * depth + " " + original_heading

    if heading_line:
        content_lines = [l for l in lines if l != heading_line]
    else:
        content_lines = lines

    content = "\n".join(content_lines).strip()
    content = rewrite_poetry_callouts(content)
    content = resolve_embed_links(content)
    return section_heading + "\n\n" + content

def parse_outline(body_text: str):
    """Parse the bullet-list body into a tree of nodes.
    Returns a list of nodes, each:
      {'text': str, 'link': str|None, 'chapter': bool, 'children': [node]}
    A node is a 'chapter' when its top-level text starts with '@@'.
    """
    root = []
    stack = []  # stack of (indent, node)

    def flush_to(indent):
        while stack and stack[-1][0] >= indent:
            stack.pop()

    for raw in body_text.splitlines():
        if not raw.strip():
            continue
        stripped = raw.lstrip()
        if not stripped.startswith("-"):
            continue
        indent = get_indent_level(raw)
        item = stripped.lstrip("-").strip()
        if not item:
            continue

        node = {'text': item, 'link': None, 'chapter': False, 'children': []}
        # Only a bullet that IS a single wikilink is a note include
        # ("- [[Note]]"), per the documented outline grammar. A bullet that
        # merely CONTAINS a link (e.g. prose with an inline citation or a
        # "…Adapted from [[Note]]" tail) is plain text.
        link_match = re.fullmatch(r"\[\[([^\[\]]+)\]\]", item)
        if link_match:
            target = link_match.group(1).strip()
            # Citekey aliases ("- [[@key|…]]") are citations, not includes.
            if not target.startswith('@'):
                node['link'] = target
                node['text'] = target
        if node['link'] is None and item.startswith("@@"):
            node['chapter'] = True
            node['text'] = item[2:].strip()

        flush_to(indent)
        if stack:
            stack[-1][1]['children'].append(node)
        else:
            root.append(node)
        stack.append((indent, node))

    return root

def compile_node(node, depth, chapter_number=None):
    """Compile one outline node recursively. Returns the section text.
    - A node with a link: heading + note content + children.
    - A plain heading node: heading + children.
    """
    parts = []
    if node['link']:
        parts.append(compile_note(node['link'], depth, chapter_number))
    else:
        title = node['text']
        if chapter_number is not None:
            parts.append("#" * depth + f" Chapter {chapter_number}: {title}")
        else:
            parts.append("#" * depth + " " + title)

    child_num = chapter_number  # chapter number does NOT propagate to children
    for child in node['children']:
        parts.append(compile_node(child, depth + 1, None))
    return "\n\n".join(parts)

def compile_book(master_file_path, global_footnotes=False, output_dir=None):
    """
    Read the master (outline) file and return the path of the compiled file.

    output_dir: where the compiled markdown is written (default: the master
    file's own folder).
    """
    master_file_path = Path(master_file_path).expanduser()
    text = master_file_path.read_text(encoding="utf-8")
    
    # First, extract YAML frontmatter
    yaml_block, body = extract_yaml(text)
    
    # Remove any existing footnote definitions from the master file body
    def_pattern = re.compile(r"\[\^([^\]]+)\]:\s*(.*?)(?=\n\[\^|\n\n|\Z)", re.DOTALL)
    body_without_footnotes = def_pattern.sub("", body)

    output_sections = []
    collected_notes = defaultdict(list)
    chapter_number = 0
    global_counter = 0  # Track global footnote count if using global numbering

    tree = parse_outline(body_without_footnotes)

    for node in tree:
        is_chapter = node['chapter']
        if is_chapter:
            chapter_number += 1
            chapter_prefix = f"Ch_{chapter_number}"
            chapter_heading = f"Chapter {chapter_number}: {node['text']}"
            print(f"\n{'='*60}")
            print(f"Processing Chapter {chapter_number}: {node['text']}")
            print(f"{'='*60}")
        else:
            chapter_prefix = sanitize_title(node['text'])
            chapter_heading = node['text']
            print(f"\n{'='*60}")
            print(f"Processing Section: {node['text']}")
            print(f"{'='*60}")

        # Compile this node (heading + any linked content) and its children.
        if node['link']:
            section_text = compile_note(node['link'], 1, chapter_number if is_chapter else None)
        else:
            section_text = compile_node(node, 1, chapter_number if is_chapter else None)
        print(f"  Added main section: {node['text']}")

        # Process ALL footnotes in the combined section text at once
        processed_text, chapter_notes, footnote_count = process_chapter_footnotes(
            section_text, chapter_prefix, chapter_heading,
            global_footnotes, global_counter
        )

        # Update global counter if using global numbering
        if global_footnotes:
            global_counter += footnote_count

        # Add processed section to output
        output_sections.append(processed_text)

        # Store chapter notes
        if chapter_notes:
            collected_notes[chapter_heading].extend(chapter_notes)
            print(f"\n  ✓ {chapter_heading} has {footnote_count} footnotes")
        else:
            print(f"\n  ✗ {chapter_heading} has no footnotes")

    # Build the final document
    final_text = ""
    if yaml_block:
        final_text += f"---\n{yaml_block}\n---\n\n"
    
    # Add all the processed sections
    final_text += "\n\n".join(output_sections)
    
    # Add the Notes section if there are any footnotes
    if collected_notes:
        notes_heading = "Notes" if global_footnotes else "Notes"
        final_text += f"\n\n# {notes_heading}\n\n"
        
        if global_footnotes:
            # For global footnotes, put them all in one section
            all_notes = []
            for notes_list in collected_notes.values():
                all_notes.extend(notes_list)
            final_text += "\n\n".join(all_notes)
            final_text += "\n\n"
            print(f"\nAdded global Notes section with {global_counter} footnotes")
        else:
            # For chapter-specific footnotes, organize by chapter
            for notes_heading, notes_list in collected_notes.items():
                final_text += f"## {notes_heading}\n\n"
                final_text += "\n\n".join(notes_list)
                final_text += "\n\n"
            print(f"\nAdded chapter-specific Notes section with {len(collected_notes)} chapters")

    # Clean up extra blank lines
    final_text = re.sub(r'\n{3,}', '\n\n', final_text)

    out_dir = Path(output_dir).expanduser() if output_dir else master_file_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / f"{master_file_path.stem} - compiled.md"
    output_path.write_text(final_text, encoding="utf-8")
    print(f"\nCompiled book written to {output_path}")
    return output_path

def detect_outline(text: str) -> bool:
    """Structural check: a top-level bullet list with no # headings is an
    outline (needs compiling). A compiled note has # headings, not bullets."""
    _, body = extract_yaml(text)
    has_bullets = False
    has_headings = False
    for raw in body.splitlines():
        if not raw.strip():
            continue
        if raw.lstrip().startswith("#"):
            has_headings = True
        if get_indent_level(raw) == 0 and raw.lstrip().startswith("-"):
            has_bullets = True
    return has_bullets and not has_headings

def read_yaml_prop(yaml_block, prop: str):
    m = re.search(rf'^{prop}:[^\S\n]*["\']?([^"\'\n]+)', yaml_block, re.M)
    return m.group(1).strip() if m else None

def write_compiled_template_prop(compiled_path, template_name: str):
    """After compiling an outline whose template was 'compile-<name>', the
    compiled output should carry 'template: <name>' (without the prefix) so a
    later export uses the real template name."""
    path = Path(compiled_path)
    text = path.read_text(encoding='utf-8')
    # Replace the whole template value (prefix + name) with the bare name.
    new_text, n = re.subn(r'^(template:\s*["\']?)(?:compile-)?[^"\'\n]+',
                          rf'\g<1>{re.escape(template_name)}',
                          text, count=1, flags=re.M)
    if n:
        path.write_text(new_text, encoding='utf-8')
        print(f"  template: compile-{template_name} → template: {template_name}")

def export_docx(compiled_md, vault_root=None, template=None, toc=False,
                template_dir=None, output_dir=None):
    """
    Export the compiled markdown to docx using the Linked Citations pipeline:
    pandoc (with lc-*.lua filters) → lc_export_merge.py (template merge).

    Reads the "template" YAML property from the compiled md (default
    "document"). Returns the output docx path.

    output_dir: where the .docx is written (default: the compiled md's own
    folder).
    """
    import subprocess
    vault_root = vault_root or vault_rel()
    compiled_md = Path(compiled_md)

    # Read template + title from compiled YAML
    text = compiled_md.read_text(encoding='utf-8')
    tpl = template
    if tpl is None:
        m = re.search(r'^template:[^\S\n]*["\']?([^"\'\n]+)', text, re.M)
        tpl = m.group(1).strip() if m else 'document'
    tpl = tpl.replace('.docx', '')
    if tpl.startswith('compile-'):
        tpl = tpl[len('compile-'):]

    m = re.search(r'^title:[^\S\n]*["\']?([^"\'\n]+)', text, re.M)
    doc_title = m.group(1).strip() if m else compiled_md.stem

    # Author: 'author' YAML prop (string or "- Name" list item) → merge
    # default "Joseph Hill".
    m = re.search(r'^author:[^\S\n]*(.*)$', text, re.M)
    doc_author = None
    if m:
        inline = m.group(1).strip()
        if inline:
            doc_author = inline.strip('"\'')
        else:
            # YAML list form: a following "- Name" line.
            lm = re.search(r'^author:[^\S\n]*\n[^\S\n]*-\s*(.+)$', text, re.M)
            if lm:
                doc_author = lm.group(1).strip().strip('"\'')
    if doc_author:
        doc_author = re.sub(r'\s+', ' ', doc_author)

    # Subtitle: 'subtitle' YAML prop → merge derives after-':' fallback.
    m = re.search(r'^subtitle:[^\S\n]*["\']?([^"\'\n]+)', text, re.M)
    doc_subtitle = m.group(1).strip() if m else None

    # Abstract / Note: passed to the merge; empty → cover sections dropped.
    # Only a same-line value counts (an empty `abstract:` key yields None).
    m = re.search(r'^abstract:[^\S\n]*(?:["\']([^"\']+)["\']|(\S.*?))(?:\s*#.*)?$',
                  text, re.M)
    doc_abstract = (m.group(1) or m.group(2)).strip() if m and (m.group(1) or m.group(2)) else None
    m = re.search(r'^note:[^\S\n]*(?:["\']([^"\']+)["\']|(\S.*?))(?:\s*#.*)?$',
                  text, re.M)
    doc_note = (m.group(1) or m.group(2)).strip() if m and (m.group(1) or m.group(2)) else None

    # Shorttitle: 'shorttitle' prop → 'title' before ':' → whole title → basename
    # Keep any markdown markers here — the merge strips them for docProps
    # (plain strings) but formats them as rich runs in the even-page header.
    m = re.search(r'^shorttitle:[^\S\n]*["\']?([^"\'\n]+)', text, re.M)
    short_title = m.group(1).strip() if m else None
    if not short_title:
        m = re.search(r'^title:[^\S\n]*["\']?([^"\'\n]+)', text, re.M)
        if m:
            short_title = m.group(1).strip().split(':')[0].strip()
    if not short_title:
        short_title = compiled_md.stem

    out_dir = Path(output_dir).expanduser() if output_dir else compiled_md.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    clean_docx = out_dir / f"{compiled_md.stem}.clean.docx"
    out_docx = out_dir / f"{compiled_md.stem}.docx"

    # Step 1: convert citation wikilinks → pandoc citations using the plugin's
    # own parser (scripts/convert-citations.mjs + parser-bundle.mjs). This
    # produces an intermediary markdown where [[@key|alias]] and containers
    # are standard pandoc [@a; @b] syntax — pandoc parses those natively, so
    # lc-wikilink-citations.lua is no longer needed.
    citations_md = compiled_md.with_suffix('.citations.md')
    # The converter, lua filters, and merge script live in the plugin's
    # scripts/ dir (anchored at the plugin's real location, cwd-independent).
    # Export Templates: look in the vault's Export Templates/ first (user
    # edits them there), fall back to the plugin's bundled templates/.
    conv_script = plugin_script_path('convert-citations.mjs')
    node_bin = os.environ.get('LC_NODE', 'node')
    subprocess.run([node_bin, conv_script, str(compiled_md), str(citations_md)],
                   check=True)

    filters = [
        plugin_script_path('lc-doc-title.lua'),
        plugin_script_path('lc-export.lua'),
        plugin_script_path('lc-zotero.lua'),
    ]
    filter_args = []
    for f in filters:
        filter_args += ['--lua-filter', f]

    cmd = [os.environ.get('LC_PANDOC', 'pandoc'), str(citations_md), '-t', 'docx',
           '-f', 'markdown+wikilinks_title_after_pipe',
           *filter_args,
           '--metadata', f'source-note={compiled_md.stem}',
           '-o', str(clean_docx)]
    print('Running pandoc:', ' '.join(cmd))
    subprocess.run(cmd, check=True)

    # Remove the intermediary citations markdown (pandoc's job is done).
    citations_md.unlink(missing_ok=True)

    # Template lookup order:
    #   1. user templates dir, if provided (e.g. <vault>/Export Templates/)
    #   2. the plugin's bundled templates/
    # The user dir comes from the --templates-dir arg or a "templates-dir"
    # setting; BookCompiler falls back to <vault>/Export Templates/ when the
    # user hasn't configured one.
    if template_dir is None:
        template_dir = os.environ.get('LC_TEMPLATES_DIR', '')
    if not template_dir:
        vault_tpl_dir = os.path.join(vault_root, 'Export Templates')
        if os.path.isdir(vault_tpl_dir):
            template_dir = vault_tpl_dir
    candidates = []
    if template_dir:
        candidates.append(os.path.join(template_dir, f'{tpl}.docx'))
    candidates.append(plugin_template_path(f'{tpl}.docx'))
    template_path = next((c for c in candidates if os.path.exists(c)), candidates[-1])
    merge_script = plugin_script_path('lc_export_merge.py')
    merge_cmd = [os.environ.get('LC_PYTHON', 'python3'), merge_script,
                 '--template', template_path,
                 '--input', str(clean_docx),
                 '--output', str(out_docx),
                 '--title', doc_title,
                 '--shorttitle', short_title,
                 '--basename', compiled_md.stem]
    if doc_author:
        merge_cmd += ['--author', doc_author]
    if doc_subtitle:
        merge_cmd += ['--subtitle', doc_subtitle]
    if doc_abstract:
        merge_cmd += ['--abstract', doc_abstract]
    if doc_note:
        merge_cmd += ['--note', doc_note]
    if toc:
        merge_cmd.append('--toc')
    print('Merging:', ' '.join(merge_cmd))
    subprocess.run(merge_cmd, check=True)

    # remove the intermediate clean docx
    clean_docx.unlink(missing_ok=True)
    print(f"\nExported docx written to {out_docx}")
    return out_docx

def main():
    parser = argparse.ArgumentParser(description='Compile an Obsidian book outline to markdown (and optionally export to docx)')
    parser.add_argument('master_file', help='Path to the master markdown file (outline or compiled)')
    parser.add_argument('--global-footnotes', action='store_true',
                       help='Use global footnote numbering (default for article* templates; override for book* templates)')
    parser.add_argument('--no-global-footnotes', action='store_true',
                       help='Restart footnote numbering per chapter (default for book* templates; override for article* templates)')
    parser.add_argument('--export', action='store_true',
                       help='Also export to docx via pandoc + template merge')
    parser.add_argument('--template', default=None,
                       help='Override the template (default: read "template" YAML from the master file)')
    parser.add_argument('--toc', action='store_true',
                       help='Include a TOC field in the exported docx (default for book* templates)')
    parser.add_argument('--no-toc', action='store_true',
                       help='Omit the TOC field (default for article* templates; override for book* templates)')
    parser.add_argument('--templates-dir', default=None,
                       help='Directory of user .docx export templates (default: <vault>/Export Templates/, '
                            'then the plugin\'s bundled templates/)')
    parser.add_argument('--output-dir', default=None,
                       help='Directory for the compiled markdown and exported docx (default: the source '
                            'file\'s own folder)')

    args = parser.parse_args()

    master_file = Path(args.master_file).expanduser()
    if not master_file.exists():
        print(f"Error: {master_file} does not exist.")
        sys.exit(1)

    text = master_file.read_text(encoding='utf-8')
    yaml_block, _ = extract_yaml(text)
    yaml_tpl = read_yaml_prop(yaml_block, 'template') if yaml_block else None

    # Decide: outline vs already-compiled.
    # 1. Explicit marker: template: compile-<name>
    explicit_compile = yaml_tpl and yaml_tpl.startswith('compile-')
    is_outline = explicit_compile or detect_outline(text)

    if explicit_compile:
        # template: compile-book2 → book2
        effective_tpl = yaml_tpl[len('compile-'):]
    else:
        effective_tpl = args.template or yaml_tpl
    effective_tpl = (effective_tpl or 'document').replace('.docx', '')

    # Template-aware defaults:
    #   book*    -> TOC on, footnotes restart per chapter
    #   article* -> TOC off, footnotes global (Heading 1 is a section, not a
    #               chapter)
    is_book = effective_tpl.startswith('book')
    is_article = effective_tpl.startswith('article')
    use_toc = is_book
    use_global = is_article
    if args.toc:
        use_toc = True
    if args.no_toc:
        use_toc = False
    if args.global_footnotes:
        use_global = True
    if args.no_global_footnotes:
        use_global = False

    if is_outline:
        print(f"Detected outline ({'explicit compile- template' if explicit_compile else 'bullet list without headings'}). Compiling…")
        print(f"Template: {effective_tpl} — TOC {'on' if use_toc else 'off'}, "
              f"footnotes {'global' if use_global else 'per-chapter'}")
        compiled = compile_book(master_file, use_global, output_dir=args.output_dir)
        if explicit_compile:
            write_compiled_template_prop(compiled, effective_tpl)
    else:
        print("Detected compiled markdown (headings present). Skipping compilation.")
        compiled = master_file

    if args.export:
        export_docx(compiled, template=args.template or effective_tpl, toc=use_toc,
                    template_dir=args.templates_dir, output_dir=args.output_dir)

if __name__ == "__main__":
    import sys
    main()
