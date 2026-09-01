## Started with ChatGPT, ironed out with DeepSeek, OpenWork, and Claude
# DocumentCompiler: converts a bulleted outline of Obsidian notes to a single
# compiled markdown file (and optionally exports to .docx).
#
# Outline grammar — applies uniformly at every bullet level:
#   - "- Heading text"          plain-text bullet → heading at the bullet’s depth
#   - "- [[Note]]"              linked bullet → heading (title/filename) + note contents
#   - "- @@ Heading text"       plain-text with @@ → numbered heading ("Chapter N: …")
#   - "- @@[[Note]]"            linked with @@  → numbered heading + note contents
#   - "- x [[Note]]"            linked with x   → note contents only (no heading)
#
# Heading title for linked notes (in order of preference):
#   1. YAML "title:" property of the linked note
#   2. Base filename, with leading ordering numbers stripped
#      ("1 Introduction" → "Introduction", "7-1 Details" → "Details")
#
# Heading demotion for included notes:
#   If the linked note contains section headings, the shallowest heading in the
#   note’s body becomes the level directly below the note’s own position in the
#   outline hierarchy. Example: a note whose title is a ## heading (depth 2) whose
#   body contains a "# Section" — that section becomes "### Section", and any
#   deeper headings shift by the same offset.
#
# Footnotes:
#   Footnotes are renumbered. Default is per-chapter for book* templates and
#   global (continuous) for article* templates. Override with --global-footnotes
#   or --no-global-footnotes.
#
# Outline vs. compiled detection:
#   - "template: compile-<name>" in YAML explicitly marks a file as an outline;
#     after compilation the template is rewritten to "<name>".
#   - Otherwise: a top-level bullet list with no # headings is treated as an outline.
#   - "--export" exports the compiled markdown to .docx via the ScholarWeave pipeline.

import json
import re
import os
from pathlib import Path
from collections import defaultdict
import argparse

# ── vault / plugin path resolution ───────────────────────────────────────────
# The script lives in <vault>/.obsidian/plugins/scholar-weave/scripts/
# (or a copy/symlink of it). The vault root is derived from THIS FILE's real
# location (os.path.realpath resolves symlinks), so the script works from any
# cwd, whether run directly, via a symlink in <vault>/scripts/, or from the
# plugin's commands.

_PLUGIN_SCRIPTS_DIR = Path(os.path.dirname(os.path.realpath(os.path.abspath(__file__))))
_PLUGIN_DIR = _PLUGIN_SCRIPTS_DIR.parent          # …/plugins/scholar-weave
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
    # Normalise CRLF → LF so the regex works on Windows-edited files.
    text = text.replace('\r\n', '\n')
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
                # Format:  > - full line  → a full verse line (not a hemistich)
                #          >\t- hemistich → one hemistich of a couplet (tab-indented)
                #          >-             → bare dash separator, ignored
                # Tab-indented pairs are joined with <br>; top-level lines stand alone.
                stripped = line.lstrip('- ').strip()
                if not stripped:
                    # Bare ">-" separator: flush any dangling single hemistich.
                    if verse_buf is not None:
                        out_lines.append(verse_buf)
                        verse_buf = None
                elif is_tab:
                    # Tab-indented: part of a hemistich pair.
                    if verse_buf is not None:
                        out_lines.append(verse_buf + '<br>' + stripped)
                        verse_buf = None
                    else:
                        verse_buf = stripped
                else:
                    # Top-level (not tab): a full verse line, not a hemistich.
                    if verse_buf is not None:
                        out_lines.append(verse_buf)
                        verse_buf = None
                    out_lines.append(stripped)
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
        # The blank '>' line between the marker and the body is essential:
        # without it pandoc puts the marker and all body lines into ONE Para,
        # and collect_all_inlines returns an empty list (rest[0] is the marker block).
        return marker.rstrip() + '\n>\n>' + '\n>'.join(out_lines) + '\n'
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

def adjust_heading_levels(content: str, depth: int) -> str:
    """Demote all markdown headings in *content* so the shallowest heading sits
    at depth+1 (i.e., one level below the note's own position in the outline).

    Example: a note at depth 2 whose body has "# Section" and "## Sub":
      min_level = 1, offset = (2+1) - 1 = 2
      "# Section" → "### Section"   "## Sub" → "#### Sub"
    """
    heading_re = re.compile(r'^(#{1,6}) ', re.M)
    levels = [len(m.group(1)) for m in heading_re.finditer(content)]
    if not levels:
        return content
    min_level = min(levels)
    offset = (depth + 1) - min_level
    if offset <= 0:
        return content
    def repl(m):
        new_level = min(len(m.group(1)) + offset, 6)
        return '#' * new_level + ' '
    return heading_re.sub(repl, content)


def compile_note(note_name: str, depth: int, chapter_number=None, suppress_heading=False):
    """Compile a linked note into an optional heading + content.

    Heading title resolution order:
      1. YAML `title:` property (bare or quoted, same line only)
      2. Base filename with leading ordering number stripped
         ('1 Introduction' → 'Introduction')

    The note's body headings are demoted so that the shallowest heading
    in the body sits one level below the note's own depth in the outline.

    suppress_heading=True omits the section heading entirely (for "x [[Note]]"
    bullets that append content to an existing section).
    """
    note_file = resolve_note_path(note_name)
    text = note_file.read_text(encoding="utf-8")

    # Strip YAML frontmatter before processing body.
    fm_match = re.match(r'^---\n[\s\S]*?\n---\n?', text)
    body = text[fm_match.end():] if fm_match else text

    # 1. YAML title property (same-line value only, quoted or bare).
    yaml_title = None
    if fm_match:
        m = re.search(r'^title:[^\S\n]*(?:["\']([^"\']+)["\']|(\S.*?))(?:\s*#.*)?$',
                      fm_match.group(0), re.M)
        if m and (m.group(1) or m.group(2)):
            yaml_title = (m.group(1) or m.group(2)).strip()

    # 2. Fallback: base filename with ordering number stripped.
    if yaml_title:
        original_heading = yaml_title
        # Body headings are NOT the note's title — keep them all in content.
        content = body.strip()
    else:
        # Use the first # heading as the section title and remove it from body.
        lines = body.splitlines()
        heading_line = next(
            (l for l in lines if re.match(r'^#+\s+', l) and not l.startswith('#tags')),
            None
        )
        if heading_line:
            original_heading = heading_line.lstrip('#').strip()
            content = '\n'.join(l for l in lines if l != heading_line).strip()
        else:
            original_heading = strip_ordering_number(note_name)
            content = body.strip()

    # Demote body headings relative to this node's depth.
    content = adjust_heading_levels(content, depth)
    content = rewrite_poetry_callouts(content)
    content = resolve_embed_links(content)

    if suppress_heading:
        return content

    if chapter_number is not None:
        section_heading = '#' * depth + f' Chapter {chapter_number}: {original_heading}'
    else:
        section_heading = '#' * depth + ' ' + original_heading

    return section_heading + '\n\n' + content

def parse_outline(body_text: str):
    """Parse the bullet-list body into a tree of nodes.

    Each node is a dict:
      {
        'text': str,              # display text (link target name or heading text)
        'link': str | None,       # wikilink target if this bullet is a note include
        'chapter': bool,          # True if bullet started with @@
        'suppress_heading': bool, # True if bullet started with 'x' (link only)
        'children': [node],
      }

    Prefix rules (applied in order before the wikilink check):
      'x '  or  'x[['  → suppress_heading (content only, no title heading)
      '@@'              → chapter (numbered heading)
    Both prefixes may apply together only via '@@' on a plain-text node; 'x'
    only applies to linked nodes (suppressing the heading makes no sense for
    a plain-text heading).
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
        if not stripped.startswith('-'):
            continue
        indent = get_indent_level(raw)
        item = stripped.lstrip('-').strip()
        if not item:
            continue

        node = {
            'text': item,
            'link': None,
            'chapter': False,
            'suppress_heading': False,
            'children': [],
        }

        rest = item

        # Check for 'x' suppress-heading prefix (only meaningful before a link).
        suppress = False
        if re.match(r'^x\s*\[\[', rest):
            suppress = True
            rest = re.sub(r'^x\s*', '', rest)

        # Check for '@@' numbered-heading prefix.
        is_chapter = False
        if rest.startswith('@@'):
            is_chapter = True
            rest = rest[2:].strip()

        # A bullet that IS a bare wikilink is a note include.
        # A bullet that merely CONTAINS a link (inline citation, prose tail) is plain text.
        link_match = re.fullmatch(r'\[\[([^\[\]]+)\]\]', rest)
        if link_match:
            target = link_match.group(1).strip()
            # Citekey wikilinks ([[@key|…]]) are citations, not includes.
            if not target.startswith('@'):
                node['link'] = target
                node['text'] = target
                node['suppress_heading'] = suppress

        node['chapter'] = is_chapter
        if not node['link'] and not suppress:
            # Plain-text bullet: use rest (prefix-stripped) as the heading text.
            node['text'] = rest if is_chapter else item

        flush_to(indent)
        if stack:
            stack[-1][1]['children'].append(node)
        else:
            root.append(node)
        stack.append((indent, node))

    return root

def compile_node(node, depth, chapter_number=None):
    """Compile one outline node recursively. Returns the section text.

    - Linked node:       heading (from note title/filename) + note content + children.
    - Linked + suppress: note content only (no heading) + children.
    - Plain-text node:   heading from bullet text + children.
    - chapter=True:      heading is prefixed "Chapter N: " using chapter_number.

    Children that have chapter=True get their own sequential chapter number
    within the sibling group at that depth.
    """
    parts = []

    if node['link']:
        parts.append(compile_note(
            node['link'], depth,
            chapter_number if node['chapter'] else None,
            suppress_heading=node.get('suppress_heading', False),
        ))
    else:
        title = node['text']
        if chapter_number is not None:
            parts.append('#' * depth + f' Chapter {chapter_number}: {title}')
        else:
            parts.append('#' * depth + ' ' + title)

    # Compile children, assigning sequential numbers to @@-marked siblings.
    child_chapter_num = 0
    for child in node['children']:
        num = None
        if child.get('chapter'):
            child_chapter_num += 1
            num = child_chapter_num
        parts.append(compile_node(child, depth + 1, num))

    return '\n\n'.join(parts)

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

def _yaml_block(text, pos, indicator):
    """Read a YAML block scalar (|, |-, >, >-) starting at pos in text.

    pos  — character position just after the key's line (i.e. m.end() from the
           key regex), so the very next lines are the block content.
    indicator — the raw matched token, e.g. '|-' or '>'.

    Returns the block content as a string (newlines joined for literal, spaces
    for folded), or None if no indented lines are found.
    """
    folded = indicator.startswith('>')
    after = text[pos:]
    block_lines = []
    for line in after.splitlines():
        if re.match(r'^\s', line):
            block_lines.append(line.strip())
        elif not line.strip():
            if block_lines:
                block_lines.append('')  # keep internal blank lines for literal
        else:
            break  # non-indented, non-blank line ends the block
    # Strip trailing blank lines (|- / >- semantics)
    while block_lines and block_lines[-1] == '':
        block_lines.pop()
    if not block_lines:
        return None
    if folded:
        return ' '.join(l for l in block_lines if l)
    else:
        return '\n'.join(block_lines)


def resolve_template_dir(template_dir, vault_root):
    """Resolve the user's templates directory from explicit arg → env → vault."""
    if template_dir is None:
        template_dir = os.environ.get('SW_TEMPLATES_DIR', '')
    if not template_dir:
        vault_tpl_dir = os.path.join(vault_root, 'Export Templates')
        if os.path.isdir(vault_tpl_dir):
            template_dir = vault_tpl_dir
    return template_dir


def find_user_lua_filters(template_dir):
    """Return sorted list of *.lua files in template_dir, skipping built-in names."""
    if not template_dir or not os.path.isdir(template_dir):
        return []
    builtin = {'sw-doc-title.lua', 'sw-export.lua', 'sw-poetry.lua', 'sw-zotero.lua'}
    result = []
    for f in sorted(os.listdir(template_dir)):
        if f.lower().endswith('.lua') and f not in builtin:
            result.append(os.path.join(template_dir, f))
    return result


def load_mappings(template_dir, mappings_json=None):
    """Return list of (source, styleName) pairs from --mappings JSON arg or
    mappings.json file (for backwards compatibility). The JSON arg takes
    priority. Returns [] when nothing is configured.

    --mappings JSON format (from TypeScript):
      [{"source": "arabic-poetry", "styleName": "Arabic poetry"}, ...]

    Legacy mappings.json file format:
      {"callouts": {"arabic-poetry": "ArabicPoetry"}, "divClasses": {...}}
    """
    import json
    if mappings_json:
        try:
            data = json.loads(mappings_json)
            result = [
                (m['source'], m['styleName'])
                for m in data
                if m.get('source') and m.get('styleName')
            ]
            if result:
                return result
        except Exception as e:
            print(f'WARNING: could not parse --mappings JSON: {e}')
    # Fall back to mappings.json file (legacy / manual workflow)
    if not template_dir:
        return []
    mappings_path = os.path.join(template_dir, 'mappings.json')
    if not os.path.exists(mappings_path):
        return []
    try:
        import json as _json
        with open(mappings_path, encoding='utf-8') as f:
            data = _json.load(f)
        all_styles = {**data.get('divClasses', {}), **data.get('callouts', {})}
        return list(all_styles.items())
    except Exception as e:
        print(f'WARNING: could not read mappings.json: {e}')
        return []


def preprocess_md_syntax(text: str) -> str:
    """Convert Markdown Attributes and Extended Markdown Syntax inline spans to
    pandoc bracketed spans ([text]{.class}) so the mappings Lua filter can
    apply character styles.

    Markdown Attributes plugin — class sits INSIDE the closing delimiter:
      *text{.cls}*        →  [*text*]{.cls}
      **text{.cls}**      →  [**text**]{.cls}
      ***text{.cls}***    →  [***text***]{.cls}
      `text{.cls}`        →  [`text`]{.cls}
      ==text{.cls}==      →  [==text==]{.cls}

    Extended Markdown Syntax plugin:
      !!{cls}text!!       →  [text]{.cls}
      ++text++            →  [text]{.inserted}
      =={color}text==     →  [text]{.highlight-<color>}
                             (leading '#' stripped; spaces → hyphens)
    """
    # Markdown Attributes: class INSIDE the closing delimiter.
    # Process longest delimiter first to avoid partial-match issues (*** > ** > *).
    # The inner group uses [^*\n]* rather than .*? to avoid "bleeding" across
    # other italic/bold markers in the same paragraph.  Without this restriction,
    # a line such as "*word1* more *word2{.cls}*" would match from the first *
    # all the way to {.cls}*, wrapping the entire intervening text in the span.
    for delim in ('***', '**', '*'):
        esc = re.escape(delim)
        text = re.sub(
            rf'{esc}([^*\n]*?)\{{\.([^}}\n]+)\}}{esc}',
            lambda m, d=delim: f'[{d}{m.group(1)}{d}]{{.{m.group(2)}}}',
            text
        )
    # Backtick code span with class.
    text = re.sub(
        r'`([^`\n]*?)\{\.([^}\n]+)\}`',
        lambda m: f'[`{m.group(1)}`]{{.{m.group(2)}}}',
        text
    )
    # Highlight with class (Markdown Attributes): ==text{.cls}== — class is
    # at the end of the content, before the closing ==.
    text = re.sub(
        r'==([^=\n]*?)\{\.([^}\n]+)\}==',
        lambda m: f'[=={m.group(1)}==]{{.{m.group(2)}}}',
        text
    )
    # Extended Markdown: !!{cls}text!! → [text]{.cls}
    text = re.sub(
        r'!!\{([^}\n]+)\}([^!\n]+)!!',
        lambda m: f'[{m.group(2)}]{{.{m.group(1)}}}',
        text
    )
    # Extended Markdown: ++text++ → [text]{.inserted}
    text = re.sub(
        r'\+\+([^+\n]+)\+\+',
        lambda m: f'[{m.group(1)}]{{.inserted}}',
        text
    )
    # Extended Markdown: =={color}text== → [text]{.highlight-color}
    # Strip leading '#'; replace spaces with hyphens for a valid CSS class name.
    text = re.sub(
        r'==\{([^}\n]+)\}([^=\n]+)==',
        lambda m: (
            f'[{m.group(2)}]'
            f'{{.highlight-{m.group(1).lstrip("#").replace(" ", "-")}}}'
        ),
        text
    )
    return text


def generate_mappings_filter(mappings_data, auto_name=True):
    """Given a list of (source, styleName) pairs, generate a temporary Lua
    filter that applies custom-style attributes. Returns the path to the temp
    .lua file, or None when mappings_data is empty and auto_name is False.

    source:    callout type or CSS class name (e.g. "arabic-poetry")
    styleName: human-readable style name as in the template (e.g. "Arabic poetry")
    auto_name: when True, any class/callout not in the explicit mapping table
               is auto-named by capitalising the first letter and replacing
               hyphens with spaces ("quran-quote" → "Quran quote").
    """
    import tempfile
    if not mappings_data and not auto_name:
        return None

    entries = '\n'.join(
        f'  ["{src}"] = "{sty}",'
        for src, sty in (mappings_data or [])
    )
    auto_name_lua = 'true' if auto_name else 'false'
    lua_code = f"""-- Auto-generated by ScholarWeave (style mappings)
local CLASS_STYLES = {{
{entries}
}}
local AUTO_NAME = {auto_name_lua}

-- Convert a CSS class to a human-readable style name when no explicit mapping
-- exists: capitalise the first character, replace hyphens with spaces.
local function auto_style_name(cls)
  return (cls:gsub("^%l", string.upper):gsub("%-", " "))
end

local function find_style(classes)
  for _, cls in ipairs(classes) do
    if CLASS_STYLES[cls] then return CLASS_STYLES[cls] end
    local bare = cls:match("^callout%-(.+)$")
    if bare and CLASS_STYLES[bare] then return CLASS_STYLES[bare] end
  end
  if AUTO_NAME then
    for _, cls in ipairs(classes) do
      if cls ~= '' then return auto_style_name(cls) end
    end
  end
  return nil
end

function Div(el)
  local style = find_style(el.classes)
  if not style then return nil end
  local result = {{}}
  pandoc.walk_block(el, {{
    Para = function(para)
      table.insert(result, pandoc.Div(
        {{pandoc.Para(para.content)}},
        pandoc.Attr("", {{}}, {{["custom-style"] = style}})
      ))
    end
  }})
  return #result > 0 and result or nil
end

-- Inline spans: applies the mapped (or auto-named) style as a character style.
function Span(el)
  local style = find_style(el.classes)
  if not style then return nil end
  el.attributes["custom-style"] = style
  -- *text*{{.class}} (Obsidian Markdown Attributes syntax) produces a Span
  -- whose sole content is an Emph or Strong node.  Unwrap it so the custom
  -- style is the ONLY formatting applied — not Emphasis + custom.  This
  -- mirrors the user's intent: the *...* is syntactic sugar for the span
  -- brackets, not a request for extra italic/bold on top of the custom style.
  if #el.content == 1 then
    local inner = el.content[1]
    if inner.t == 'Emph' or inner.t == 'Strong' then
      el.content = inner.content
    end
  end
  return el
end

-- Handle Obsidian callouts (> [!type] ...) that sw-poetry.lua did not claim
-- (i.e. non-poetry callouts mapped via explicit style mappings).
-- Pandoc 3.x may parse these as BlockQuote whose first block is a Header
-- (when the callout title line is rendered as a heading) or a Para.
function BlockQuote(el)
  local content = el.content
  if #content == 0 then return nil end
  local first = content[1]
  local s
  if first.t == "Para" or first.t == "Header" then
    s = pandoc.utils.stringify(first.content)
  else
    return nil
  end
  local marker = s:match("^%[!([^%]]+)%]")
  if not marker then return nil end
  local style = CLASS_STYLES[marker:lower()] or CLASS_STYLES["callout-" .. marker:lower()]
  if not style then return nil end
  local result = {{}}
  -- Wrap inlines in a custom-style Div.
  local function add_block(inlines)
    table.insert(result, pandoc.Div(
      {{pandoc.Para(inlines)}},
      pandoc.Attr("", {{}}, {{["custom-style"] = style}})
    ))
  end
  -- Recurse into any nesting depth to find leaf Para/Plain blocks.
  local function collect(blocks)
    for _, b in ipairs(blocks) do
      if b.t == "Para" or b.t == "Plain" then
        add_block(b.content)
      elseif b.t == "BulletList" or b.t == "OrderedList" then
        for _, item in ipairs(b.content) do
          collect(item)
        end
      elseif b.t == "BlockQuote" or b.t == "Div" then
        collect(b.content)
      end
    end
  end
  if #content == 1 and first.t == "Para" then
    -- All content is in one Para — skip past the marker (up to first SoftBreak)
    local after = pandoc.List()
    local past = false
    for _, inl in ipairs(first.content) do
      if not past then
        if inl.t == "SoftBreak" then past = true end
      else
        after:insert(inl)
      end
    end
    if #after > 0 then add_block(after) end
  else
    -- Multiple blocks: skip first (marker), recurse into the rest.
    local rest = {{}}
    for i = 2, #content do rest[#rest+1] = content[i] end
    collect(rest)
  end
  return #result > 0 and result or nil
end
"""
    tmp = tempfile.NamedTemporaryFile(
        mode='w', suffix='.lua', delete=False, encoding='utf-8')
    tmp.write(lua_code)
    tmp.close()
    print(f'Generated Lua mappings filter: {tmp.name}')
    return tmp.name


# Sentinel background colours — cycled across undefined styles so each one is
# visually distinct.  First colour (yellow) matches the previous single colour.
_SENTINEL_COLORS = ['#FFFF00', '#FFC080', '#80FF80', '#80DFFF', '#FFB0FF', '#FFDF80']

# Human-readable names of the paragraph styles that sw-poetry.lua always emits
# (regardless of explicit mappings). These must always be present in the
# reference doc so pandoc can apply them.
_SW_POETRY_PARAGRAPH_STYLES = ['Arabic poetry', 'English poetry']


def collect_span_styles(text, mappings_data, auto_name=True):
    """Scan preprocessed markdown text and return the deduplicated list of
    human-readable style names that will be applied by the Lua mappings filter —
    both explicit class→style mappings and auto-named styles from unrecognised
    CSS classes on bracketed spans and fenced divs.

    text:          the preprocessed citations markdown (after preprocess_md_syntax)
    mappings_data: list of (source, styleName) pairs from explicit mappings
    auto_name:     when True, unrecognised classes are auto-named (first letter
                   capitalised, hyphens → spaces), matching the Lua filter logic
    """
    cls_map = {}
    for src, sty in (mappings_data or []):
        cls_map[src] = sty
        if not src.startswith('callout-'):
            cls_map[f'callout-{src}'] = sty

    def auto_style_name(cls):
        return cls[0].upper() + cls[1:].replace('-', ' ') if cls else cls

    seen = set()
    styles = []

    def add_cls(cls):
        if not cls:
            return
        # Reject class names that don't look like valid CSS identifiers.
        # This prevents false matches from patterns like ][^1]{…} or ]{} that
        # could arise from footnote references or other markdown constructs.
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_-]*$', cls):
            return
        if cls in cls_map:
            sty = cls_map[cls]
        elif auto_name:
            sty = auto_style_name(cls)
        else:
            return
        if sty not in seen:
            seen.add(sty)
            styles.append(sty)

    # Bracketed spans: [text]{.class1 .class2} — first class wins (mirrors Lua)
    for m in re.finditer(r'\]\{([^}]+)\}', text):
        for part in m.group(1).split():
            if part.startswith('.') and len(part) > 1:
                add_cls(part[1:])
                break

    # Fenced divs: :::class  or  ::: {.class}
    for m in re.finditer(r'^:::\s*(?:\{\.([^}\s]+)\}|(\S+))', text, re.MULTILINE):
        add_cls(m.group(1) or m.group(2))

    return styles


def _classify_style_names(style_names, defined, para_referenced, char_referenced,
                          span_styles, to_key):
    """Classify explicitly-named styles into paragraph vs character usage sets.

    Walks style_names, converting each to its output-key form via to_key, then
    checks membership in para_referenced / char_referenced.  span_styles is a
    secondary signal for the fallback case (key not found in either set):
    styles listed there are classified as character; everything else as paragraph.

    Returns (type_para, type_char) as sets of *human-readable* style names (the
    original values from style_names, not the key form), so callers can use the
    display name directly when building format-specific injection XML.

    to_key examples:
      ODT  — lambda n: n.replace(' ', '_20_')
      DOCX — lambda n: re.sub(r'\\s+', '', n)
    """
    type_para: set = set()
    type_char: set = set()
    span_set = set(span_styles or [])
    for n in style_names:
        key = to_key(n)
        if key in defined:
            continue
        is_para = key in para_referenced
        is_char = key in char_referenced
        if is_para:
            type_para.add(n)
        if is_char:
            type_char.add(n)
        if not is_para and not is_char:
            # Not seen in the output XML.  Use span_styles as a secondary
            # signal: styles the markdown uses as inline spans are character
            # styles; everything else falls back to paragraph.
            (type_char if n in span_set else type_para).add(n)
    return type_para, type_char


def inject_missing_odt_styles(odt_path, style_names=None, template_odt_path=None, span_styles=None):
    """Post-processing: scan the ODT output for every style actually referenced
    in content.xml, determine which are absent from the reference template, and
    inject a sentinel paragraph + character style (cycling background colour) for
    each.  Styles already defined in the template are left alone.

    style_names: optional extra list of human-readable names to also check —
                 useful when the Lua filter applied styles the scanner might miss.
                 If None/empty, auto-detection alone is used.
    template_odt_path: ODT whose styles.xml is treated as 'already defined'.
                       When absent, the output's own styles.xml is used instead
                       (in that case pandoc-generated stubs count as defined and
                       the caller should pass style_names for belt-and-suspenders).
    """
    import zipfile, shutil, tempfile

    def to_odt_name(s):
        return s.replace(' ', '_20_')
    def to_human(odt_name):
        return odt_name.replace('_20_', ' ')

    odt_path = Path(odt_path)

    # ── 1. determine which styles are already defined ─────────────────────────
    # Scan BOTH the template (named styles, display names) AND the output
    # (pandoc auto-styles merged into content.xml) so that:
    # - styles stored by LibreOffice with their display name (style:display-name)
    #   rather than the _20_-encoded internal name are recognised; and
    # - built-in LibreOffice styles (e.g. "List Number Tight") that pandoc
    #   auto-defines in content.xml (not present in the template's styles.xml)
    #   are treated as defined and not flagged as missing.
    ref_sources = []
    if template_odt_path and os.path.exists(template_odt_path):
        ref_sources.append(template_odt_path)
    out_path_str = str(odt_path)
    if out_path_str not in ref_sources:
        ref_sources.append(out_path_str)
    ref_text = ''
    for _rp in ref_sources:
        try:
            with zipfile.ZipFile(_rp, 'r') as z:
                ref_text += ''.join(
                    z.read(n).decode('utf-8')
                    for n in ['styles.xml', 'content.xml']
                    if n in z.namelist()
                )
        except Exception as _e:
            print(f'WARNING: could not read reference ODT {_rp}: {_e}')
    try:
        # LibreOffice ODTs sometimes store style:name with actual spaces
        # ("Arabic poetry") rather than _20_ encoding ("Arabic_20_poetry").
        # Also scan style:display-name so styles whose internal name differs
        # from their display name (e.g. style:name="ListNumberTight" with
        # style:display-name="List Number Tight") are still recognised.
        _raw_defined = (set(re.findall(r'style:name="([^"]+)"', ref_text))
                        | set(re.findall(r'style:display-name="([^"]+)"', ref_text)))
        defined = set()
        for _dn in _raw_defined:
            defined.add(_dn)
            defined.add(_dn.replace(' ', '_20_'))                   # spaces → _20_
            defined.add(_dn.replace('_20_', ' '))                   # _20_ → spaces
            defined.add(_dn.replace(' ', '').replace('_20_', ''))   # "Footnote Reference" → "FootnoteReference"
    except Exception as e:
        print(f'WARNING: could not build defined-styles set for sentinel check: {e}')
        defined = set()

    # ── 2. collect all text:style-name references from the output ─────────────
    try:
        with zipfile.ZipFile(odt_path, 'r') as z:
            out_content = (z.read('content.xml').decode('utf-8')
                           if 'content.xml' in z.namelist() else '')
    except Exception as e:
        print(f'WARNING: could not read output ODT content.xml: {e}')
        out_content = ''

    # Distinguish paragraph-style references (on text:p/text:h elements) from
    # character-style references (on text:span elements) so each missing style is
    # injected only as the type(s) it is actually used as in the output.
    para_referenced = set(re.findall(r'<text:(?:p|h)\b[^>]*\btext:style-name="([^"]+)"', out_content))
    char_referenced = set(re.findall(r'<text:span\b[^>]*\btext:style-name="([^"]+)"', out_content))

    # ── 3. compute the missing set, split by usage type ───────────────────────
    # When style_names is provided (always the case in the export pipeline),
    # check ONLY those explicitly-named styles (avoids spurious hits on
    # built-in LibreOffice TOC/heading styles resolved at render time).
    # Fall back to auto-scan only when style_names is None/empty.
    if style_names:
        # Classify via shared helper; returns human-readable name sets.
        # ODT key form is _20_-encoded (to_odt_name).
        _para_h, _char_h = _classify_style_names(
            style_names, defined, para_referenced, char_referenced,
            span_styles=span_styles, to_key=to_odt_name,
        )
        # Convert back to ODT key form for injection and membership tests below.
        type_para = {to_odt_name(n) for n in _para_h}
        type_char = {to_odt_name(n) for n in _char_h}
        missing_odt = sorted(type_para | type_char)
    else:
        # Auto-scan: only flag styles with _20_ in their ODT name (the reliable
        # marker for user-created named styles vs pandoc's short auto-style IDs).
        type_para = {n for n in para_referenced if n not in defined and '_20_' in n}
        type_char = {n for n in char_referenced if n not in defined and '_20_' in n}
        missing_odt = sorted(type_para | type_char)
    if not missing_odt:
        return

    missing_human = [to_human(n) for n in missing_odt]
    print(f'Injecting sentinel ODT styles: {missing_odt}')

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        with zipfile.ZipFile(odt_path, 'r') as z:
            z.extractall(tmp_dir)
        styles_path = tmp_dir / 'styles.xml'
        if not styles_path.exists():
            print('WARNING: styles.xml not found in ODT — skipping sentinel injection.')
            return

        styles = styles_path.read_text(encoding='utf-8')
        color_map = {n: _SENTINEL_COLORS[i % len(_SENTINEL_COLORS)]
                     for i, n in enumerate(missing_odt)}
        injection_parts = []
        for n, h in zip(missing_odt, missing_human):
            color = color_map[n]
            if n in type_para:
                # Paragraph style — sentinel background at paragraph level.
                injection_parts.append(
                    f'<style:style style:name="{n}" style:display-name="{h}"'
                    f' style:family="paragraph"'
                    f' style:parent-style-name="Default_20_Paragraph_20_Style">'
                    f'<style:paragraph-properties fo:background-color="{color}"/>'
                    f'<style:text-properties fo:background-color="{color}"/>'
                    f'</style:style>'
                )
            if n in type_char:
                # Character style — sentinel background on text spans.
                injection_parts.append(
                    f'<style:style style:name="{n}" style:display-name="{h}"'
                    f' style:family="text">'
                    f'<style:text-properties fo:background-color="{color}"/>'
                    f'</style:style>'
                )
        injection = ''.join(injection_parts)
        # Named paragraph styles belong in <office:styles>, not automatic-styles.
        if '</office:styles>' in styles:
            styles = styles.replace(
                '</office:styles>',
                injection + '</office:styles>', 1)
            styles_path.write_text(styles, encoding='utf-8')
        else:
            print('WARNING: </office:styles> not found in styles.xml — skipping injection.')
            return

        # Repack (preserve mimetype uncompressed as required by the ODF spec)
        tmp_odt = odt_path.with_suffix('.tmp.odt')
        with zipfile.ZipFile(tmp_odt, 'w', zipfile.ZIP_DEFLATED) as zout:
            mimetype = tmp_dir / 'mimetype'
            if mimetype.exists():
                zout.write(mimetype, 'mimetype', compress_type=zipfile.ZIP_STORED)
            for item in sorted(tmp_dir.rglob('*')):
                if item.is_file() and item.name != 'mimetype':
                    zout.write(item, item.relative_to(tmp_dir), compress_type=zipfile.ZIP_DEFLATED)
        odt_path.unlink()
        tmp_odt.rename(odt_path)
        print(f'  Sentinel styles injected into {odt_path.name} — '
              f'define these in your template to remove the yellow highlight.')
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def inject_missing_docx_styles(docx_path, style_names=None, template_docx_path=None, span_styles=None):
    """Post-processing: scan the DOCX output for every paragraph and character
    style actually referenced in word/document.xml, determine which are absent
    from the reference template, and inject a yellow-shaded sentinel style for
    each.  Styles already defined in the template are left alone.

    style_names: optional extra list of human-readable names to also check —
                 useful when the Lua filter applied styles the scanner might miss.
    template_docx_path: DOCX whose word/styles.xml is treated as 'already defined'.
                        When absent, the output's own word/styles.xml is used.
    """
    import zipfile, shutil, tempfile

    docx_path = Path(docx_path)

    # ── 1. determine which styles are already defined ─────────────────────────
    # Scan BOTH the template and the output word/styles.xml so that built-in
    # styles emitted by pandoc into the output (e.g. "Footnote Reference") are
    # treated as defined and not flagged as missing — mirroring the ODT approach.
    ref_sources = []
    if template_docx_path and os.path.exists(template_docx_path):
        ref_sources.append(template_docx_path)
    if str(docx_path) not in ref_sources:
        ref_sources.append(str(docx_path))
    ref_styles = ''
    for _rp in ref_sources:
        try:
            with zipfile.ZipFile(_rp, 'r') as z:
                ref_styles += (z.read('word/styles.xml').decode('utf-8')
                               if 'word/styles.xml' in z.namelist() else '')
        except Exception as e:
            print(f'WARNING: could not read DOCX for sentinel check: {e}')
    defined_raw = set(re.findall(r'<w:name\s+w:val="([^"]+)"', ref_styles))
    # w:styleId values (e.g. "FootnoteReference") are the exact IDs pandoc
    # uses in rStyle/pStyle vals, so include them verbatim.
    defined_ids = set(re.findall(r'w:styleId="([^"]+)"', ref_styles))
    # Also add space-collapsed variants so built-in styles stored with a
    # display name ("Footnote Reference") also match their internal ID form
    # ("FootnoteReference") used in user-defined mappings.
    defined = set()
    for _dn in defined_raw:
        defined.add(_dn)
        defined.add(_dn.replace(' ', ''))   # "Footnote Reference" → "FootnoteReference"
    defined.update(defined_ids)

    # ── 2. collect all pStyle / rStyle references from the output ─────────────
    try:
        with zipfile.ZipFile(docx_path, 'r') as z:
            out_doc = (z.read('word/document.xml').decode('utf-8')
                       if 'word/document.xml' in z.namelist() else '')
    except Exception as e:
        print(f'WARNING: could not read output DOCX document.xml: {e}')
        out_doc = ''

    para_referenced = set(re.findall(r'<w:pStyle\s+w:val="([^"]+)"', out_doc))
    char_referenced = set(re.findall(r'<w:rStyle\s+w:val="([^"]+)"', out_doc))

    # ── 3. compute what's missing for each usage type ─────────────────────────
    # Pandoc strips spaces from rStyle vals ("Quran quote" → "Quranquote") but
    # may preserve them in pStyle vals — so the DOCX key form for checking
    # membership in para_referenced / char_referenced is the space-stripped ID.
    _to_sid = lambda name: re.sub(r'\s+', '', name)
    # Map space-stripped ID → human-readable name for display in <w:name>.
    sid_to_name = {_to_sid(n): n for n in (style_names or [])}

    if style_names:
        # Classify via shared helper; returns human-readable name sets.
        # DOCX key form is space-stripped (_to_sid).
        _para_h, _char_h = _classify_style_names(
            style_names, defined, para_referenced, char_referenced,
            span_styles=span_styles, to_key=_to_sid,
        )
        # para_missing: human names (used verbatim in <w:name w:val="..."/>),
        # combined with any auto-detected pStyle refs that have spaces in their
        # name (the reliable marker for user-created named styles not covered by
        # style_names).
        para_missing = sorted(
            _para_h
            | {n for n in para_referenced if n not in defined and ' ' in n}
        )
        # char_missing: space-stripped IDs (matching rStyle vals in document.xml);
        # sid_to_name recovers the display name at injection time.
        char_missing = sorted(
            {_to_sid(n) for n in _char_h}
            | {n for n in char_referenced if n not in defined}
        )
    else:
        # Auto-scan only (no explicit style list supplied).
        para_missing = sorted({n for n in para_referenced if n not in defined and ' ' in n})
        char_missing = sorted({n for n in char_referenced if n not in defined})
    if not para_missing and not char_missing:
        return

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        with zipfile.ZipFile(docx_path, 'r') as z:
            z.extractall(tmp_dir)
        styles_path = tmp_dir / 'word' / 'styles.xml'
        if not styles_path.exists():
            return

        styles_xml = styles_path.read_text(encoding='utf-8')

        # Assign colours consistently across both paragraph and character
        # sentinels so the same style name always gets the same colour.
        all_missing_names = sorted(set(para_missing) | set(char_missing))
        color_map = {
            name: _SENTINEL_COLORS[i % len(_SENTINEL_COLORS)].lstrip('#')
            for i, name in enumerate(all_missing_names)
        }
        print(f'Injecting sentinel DOCX paragraph styles: {para_missing}')
        print(f'Injecting sentinel DOCX character styles:  {char_missing}')

        sentinel_styles = []
        rStyle_rewrites = {}  # style_id → char_id, for post-processing document.xml

        for name in para_missing:
            color = color_map[name]
            style_id = re.sub(r'\s+', '', name)
            sentinel_styles.append(
                f'<w:style w:type="paragraph" w:styleId="{style_id}">'
                f'<w:name w:val="{name}"/>'
                f'<w:basedOn w:val="Normal"/>'
                f'<w:pPr><w:shd w:val="clear" w:color="auto" w:fill="{color}"/></w:pPr>'
                f'<w:rPr><w:shd w:val="clear" w:color="auto" w:fill="{color}"/></w:rPr>'
                f'</w:style>'
            )

        for name in char_missing:
            color = color_map[name]
            style_id = re.sub(r'\s+', '', name)
            # Recover the human-readable display name from the sid_to_name map.
            # pandoc strips spaces from style IDs in rStyle vals (e.g.
            # "Quranquote" for "Quran quote"), so `name` here is the stripped
            # form; prefer the original spaced form for <w:name>.
            display_name = sid_to_name.get(style_id, name)
            # Character styleId must differ from any paragraph style with the
            # same name (OOXML requires unique IDs across all style types).
            # Rewrite w:rStyle refs in document.xml below to match.
            char_id = style_id + 'Char'
            rStyle_rewrites[style_id] = char_id
            sentinel_styles.append(
                f'<w:style w:type="character" w:styleId="{char_id}">'
                f'<w:name w:val="{display_name}"/>'
                f'<w:rPr><w:shd w:val="clear" w:color="auto" w:fill="{color}"/></w:rPr>'
                f'</w:style>'
            )

        injection = ''.join(sentinel_styles)
        if '</w:styles>' in styles_xml:
            styles_xml = styles_xml.replace('</w:styles>', injection + '</w:styles>', 1)
            styles_path.write_text(styles_xml, encoding='utf-8')

        # Rewrite w:rStyle refs in document.xml: pandoc emits style_id, but
        # we need char_id (style_id + 'Char') to avoid duplicate styleIds.
        doc_path = tmp_dir / 'word' / 'document.xml'
        if doc_path.exists() and rStyle_rewrites:
            doc_xml = doc_path.read_text(encoding='utf-8')
            for sid, cid in rStyle_rewrites.items():
                doc_xml = re.sub(
                    rf'(<w:rStyle\s+w:val="){re.escape(sid)}"',
                    rf'\g<1>{cid}"',
                    doc_xml,
                )
            doc_path.write_text(doc_xml, encoding='utf-8')

        # Repack
        tmp_docx = docx_path.with_suffix('.tmp.docx')
        with zipfile.ZipFile(tmp_docx, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in tmp_dir.rglob('*'):
                if item.is_file():
                    zout.write(item, item.relative_to(tmp_dir))
        shutil.move(str(tmp_docx), str(docx_path))
        print(f'  Sentinel styles injected into {docx_path.name} — '
              f'define these in your template to remove the yellow highlight.')
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def find_soffice():
    """Find the LibreOffice soffice binary."""
    import shutil as _sh
    candidates = [
        os.environ.get('SW_SOFFICE', ''),
        'soffice',
        '/Applications/LibreOffice.app/Contents/MacOS/soffice',
        '/usr/bin/soffice',
        '/usr/local/bin/soffice',
    ]
    for c in candidates:
        if c and _sh.which(c):
            return c
    return None


def _parse_yaml_metadata(text, stem):
    """Parse all YAML frontmatter properties used by the export pipeline.

    Returns a dict with keys: tpl, title, author, subtitle, date_val, abstract,
    extra_sections, short_title.  Call once per export; pass the result to
    export_document (or use it directly when inspecting without exporting).
    """
    m = re.search(r'^template:[^\S\n]*["\']?([^"\'\n]+)', text, re.M)
    tpl = m.group(1).strip() if m else 'document'
    tpl = re.sub(r'\.(docx|odt)$', '', tpl, flags=re.IGNORECASE)
    if tpl.startswith('compile-'):
        tpl = tpl[len('compile-'):]

    m = re.search(r'^title:[^\S\n]*["\']?([^"\'\n]+)', text, re.M)
    title = m.group(1).strip() if m else stem

    m = re.search(r'^author:[^\S\n]*(.*)$', text, re.M)
    author = None
    if m:
        inline = m.group(1).strip()
        if inline:
            author = inline.strip('"\'')
        else:
            lm = re.search(r'^author:[^\S\n]*\n[^\S\n]*-\s*(.+)$', text, re.M)
            if lm:
                author = lm.group(1).strip().strip('"\'')
    if author:
        author = re.sub(r'\s+', ' ', author)

    m = re.search(r'^subtitle:[^\S\n]*["\']?([^"\'\n]+)', text, re.M)
    subtitle = m.group(1).strip() if m else None

    m = re.search(r'^date:[^\S\n]*["\']?([^"\'\n]+)', text, re.M)
    date_val = m.group(1).strip() if m else None

    m = re.search(r'^abstract:[^\S\n]*(?:["\']([^"\']+)["\']|(\S.*?))(?:\s*#.*)?$',
                  text, re.M)
    if m and (m.group(1) or m.group(2)):
        raw = (m.group(1) or m.group(2)).strip()
        abstract = _yaml_block(text, m.end(), raw) if re.match(r'^[|>]', raw) else raw
    else:
        abstract = None

    extra_sections = []
    for m in re.finditer(
            r'^(note|sw-[\w-]+):[^\S\n]*(?:["\']([^"\']+)["\']|(\S[^\n]*?))(?:\s*#[^\n]*)?\s*$',
            text, re.M):
        key = m.group(1)
        raw = (m.group(2) or m.group(3) or '').strip()
        val = _yaml_block(text, m.end(), raw) if re.match(r'^[|>]', raw) else raw
        if val:
            extra_sections.append([key, val])

    # Shorttitle: explicit 'shorttitle' prop → title before ':' → stem.
    # Markdown markers are preserved here; merge scripts strip them for plain
    # string fields but format them as rich runs in even-page headers.
    m = re.search(r'^shorttitle:[^\S\n]*["\']?([^"\'\n]+)', text, re.M)
    short_title = m.group(1).strip() if m else None
    if not short_title:
        m = re.search(r'^title:[^\S\n]*["\']?([^"\'\n]+)', text, re.M)
        if m:
            short_title = m.group(1).strip().split(':')[0].strip()
    if not short_title:
        short_title = stem

    return {
        'tpl':            tpl,
        'title':          title,
        'author':         author,
        'subtitle':       subtitle,
        'date_val':       date_val,
        'abstract':       abstract,
        'extra_sections': extra_sections,
        'short_title':    short_title,
    }


def export_document(fmt, compiled_md, vault_root=None, template=None, toc=False,
                    template_dir=None, output_dir=None, default_author=None,
                    new_page_headings=True, restart_footnotes=True,
                    mappings_data=None):
    """Unified export pipeline for DOCX and ODT.

    Both formats share: YAML metadata parsing, citation conversion, markdown
    pre-processing, Lua filter construction, pandoc invocation, template
    lookup, merge script invocation, and missing-style injection.

    Format-specific details are isolated to two narrow sections:

      DOCX — pandoc writes a clean OOXML file (no reference-doc needed);
             sw_export_merge.py handles template merging.

      ODT  — pandoc writes a clean ODF file using the template as
             --reference-doc (so custom-style attributes resolve correctly);
             sentinel styles are pre-injected into a temp copy first;
             sw_export_odt_merge.py handles template merging.
             Note: --toc is NOT forwarded to pandoc — the ODT merger manages
             the TOC element directly from the template structure.

    Both merge scripts share the same CLI interface, so the merge command
    construction is identical; only the script path differs.

    Returns the output file path (Path).
    """
    import subprocess
    compiled_md  = Path(compiled_md)
    vault_root   = vault_root or vault_rel()
    template_dir = resolve_template_dir(template_dir, vault_root)

    text = compiled_md.read_text(encoding='utf-8')
    meta = _parse_yaml_metadata(text, compiled_md.stem)

    # Caller may override the template name (e.g. from the export dialog).
    tpl = template if template is not None else meta['tpl']
    tpl = re.sub(r'\.(docx|odt)$', '', tpl, flags=re.IGNORECASE)
    if tpl.startswith('compile-'):
        tpl = tpl[len('compile-'):]

    doc_title      = meta['title']
    doc_author     = meta['author'] or default_author
    doc_subtitle   = meta['subtitle']
    doc_date       = meta['date_val']
    doc_abstract   = meta['abstract']
    extra_sections = meta['extra_sections']
    short_title    = meta['short_title']

    out_dir = Path(output_dir).expanduser() if output_dir else compiled_md.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    ext        = '.docx' if fmt == 'docx' else '.odt'
    clean_path = out_dir / f"{compiled_md.stem}.clean{ext}"
    out_path   = out_dir / f"{compiled_md.stem}{ext}"

    # ── Citation conversion (identical for both formats) ───────────────────────
    citations_md = compiled_md.with_suffix('.citations.md')
    conv_script  = plugin_script_path('convert-citations.mjs')
    node_bin     = os.environ.get('SW_NODE', 'node')
    subprocess.run([node_bin, conv_script, str(compiled_md), str(citations_md)],
                   check=True)

    # ── Markdown pre-processing (identical for both formats) ───────────────────
    cit_text = citations_md.read_text(encoding='utf-8')
    cit_text = rewrite_poetry_callouts(cit_text)
    cit_text = preprocess_md_syntax(cit_text)
    citations_md.write_text(cit_text, encoding='utf-8')

    # ── Lua filter construction (identical for both formats) ───────────────────
    filters = [
        plugin_script_path('sw-doc-title.lua'),
        plugin_script_path('sw-export.lua'),
        plugin_script_path('sw-poetry.lua'),
        plugin_script_path('sw-zotero.lua'),
    ]
    filters += find_user_lua_filters(template_dir)
    active_mappings = mappings_data or load_mappings(template_dir)
    # Only include poetry styles when the document actually contains poetry callouts.
    _arabic_re = re.compile(r'^\s*>\s*\[!arabic-poetry', re.M | re.I)
    _english_re = re.compile(r'^\s*>\s*\[!(?:poetry|english-poetry)', re.M | re.I)
    poetry_styles = []
    if _arabic_re.search(cit_text):
        poetry_styles.append('Arabic poetry')
    if _english_re.search(cit_text):
        poetry_styles.append('English poetry')
    _span_styles = collect_span_styles(cit_text, active_mappings)
    all_styles = list(dict.fromkeys(
        poetry_styles
        + [sty for _, sty in active_mappings]
        + _span_styles
    ))
    mappings_filter = generate_mappings_filter(active_mappings)
    if mappings_filter:
        filters.append(mappings_filter)
    filter_args = []
    for f in filters:
        filter_args += ['--lua-filter', f]

    # ── Template path lookup (identical candidate chain for both formats) ──────
    candidates = []
    if template_dir:
        candidates.append(os.path.join(template_dir, f'{tpl}{ext}'))
    candidates.append(plugin_template_path(f'{tpl}{ext}'))
    if template_dir:
        candidates.append(os.path.join(template_dir, f'document{ext}'))
    candidates.append(plugin_template_path(f'document{ext}'))
    template_path = next((c for c in candidates if os.path.exists(c)), candidates[-1])
    if not os.path.exists(template_path):
        print(f"WARNING: no template found for '{tpl}' (and no document{ext} fallback). "
              f"Tried: {candidates}")
    elif not template_path.endswith(f'{tpl}{ext}'):
        print(f"WARNING: template '{tpl}{ext}' not found; using {template_path} as fallback.")

    # ── pandoc invocation (format-specific) ────────────────────────────────────
    if fmt == 'docx':
        # DOCX: clean output with no reference-doc; merge script applies template.
        cmd = [os.environ.get('SW_PANDOC', 'pandoc'), str(citations_md),
               '-t', 'docx',
               '-f', 'markdown+wikilinks_title_after_pipe+lists_without_preceding_blankline',
               *filter_args,
               '--metadata', f'source-note={compiled_md.stem}',
               '-o', str(clean_path)]
        print('Running pandoc:', ' '.join(cmd))
        subprocess.run(cmd, check=True)
        _tmp_ref_dir = None

    else:  # odt
        # ODT: use the template as --reference-doc so pandoc resolves custom-style
        # attributes correctly.  Sentinel styles are pre-injected into a temp copy
        # before pandoc runs so styles the template doesn't define yet are visible.
        # --toc is NOT forwarded: the ODT merger manages TOC from template structure.
        _tmp_ref_dir = None
        pandoc_ref_doc = template_path if os.path.exists(template_path) else None
        if pandoc_ref_doc and all_styles:
            pandoc_ref_doc, _tmp_ref_dir = _prep_reference_odt(pandoc_ref_doc, all_styles)
        ref_doc_args = ['--reference-doc', pandoc_ref_doc] if pandoc_ref_doc else []
        cmd = [os.environ.get('SW_PANDOC', 'pandoc'), str(citations_md),
               '-t', 'odt',
               '-f', 'markdown+wikilinks_title_after_pipe+lists_without_preceding_blankline',
               *filter_args,
               *ref_doc_args,
               '-o', str(clean_path)]
        print('Running pandoc:', ' '.join(cmd))
        subprocess.run(cmd, check=True)
        if _tmp_ref_dir:
            import shutil as _sh2
            _sh2.rmtree(_tmp_ref_dir, ignore_errors=True)

    import shutil
    shutil.copy(str(citations_md), str(citations_md.parent / 'DEBUG_citations.md'))
    citations_md.unlink(missing_ok=True)

    # ── Merge step (format-specific script; identical CLI interface) ───────────
    # Both sw_export_merge.py and sw_export_odt_merge.py accept the same set of
    # arguments, so the command construction is the same — only the script differs.
    merge_script = plugin_script_path(
        'sw_export_merge.py' if fmt == 'docx' else 'sw_export_odt_merge.py')
    merge_cmd = [os.environ.get('SW_PYTHON', 'python3'), merge_script,
                 '--template',   template_path,
                 '--input',      str(clean_path),
                 '--output',     str(out_path),
                 '--title',      doc_title,
                 '--shorttitle', short_title,
                 '--basename',   compiled_md.stem]
    if doc_author:    merge_cmd += ['--author',   doc_author]
    if doc_subtitle:  merge_cmd += ['--subtitle', doc_subtitle]
    if doc_date:      merge_cmd += ['--date',     doc_date]
    if doc_abstract:  merge_cmd += ['--abstract', doc_abstract]
    if extra_sections:
        merge_cmd += ['--extra-sections', json.dumps(extra_sections, ensure_ascii=False)]
    if not new_page_headings:
        merge_cmd.append('--no-new-page-headings')
    merge_cmd.append('--no-global-footnotes' if restart_footnotes
                     else '--global-footnotes')
    if toc:
        merge_cmd.append('--toc')
    print('Merging:', ' '.join(merge_cmd))
    subprocess.run(merge_cmd, check=True)
    clean_path.unlink(missing_ok=True)

    # ── Missing-style injection (belt-and-suspenders for both formats) ─────────
    if fmt == 'docx':
        inject_missing_docx_styles(out_path, style_names=all_styles,
                                   template_docx_path=template_path,
                                   span_styles=_span_styles)
    else:
        inject_missing_odt_styles(out_path, style_names=all_styles,
                                  template_odt_path=template_path,
                                  span_styles=_span_styles)

    print(f'Template: {template_path}')
    print(f'Exported [{tpl}] → {out_path}')
    print(out_path)   # last line — parsed by exportCompiler.ts as the output path
    return out_path


def export_docx(compiled_md, vault_root=None, template=None, toc=False,
                template_dir=None, output_dir=None, default_author=None,
                new_page_headings=True, restart_footnotes=True,
                mappings_data=None):
    """Export compiled markdown to DOCX. Thin wrapper around export_document."""
    return export_document('docx', compiled_md,
                           vault_root=vault_root, template=template, toc=toc,
                           template_dir=template_dir, output_dir=output_dir,
                           default_author=default_author,
                           new_page_headings=new_page_headings,
                           restart_footnotes=restart_footnotes,
                           mappings_data=mappings_data)


def postprocess_odt(odt_path: Path, ref_doc_path=None, toc=False,
                    new_page_headings=True, restart_footnotes=True,
                    extra_sections=None, abstract=None) -> None:
    """Post-process pandoc ODT output to mirror the docx merge pipeline:

    1. Remap First_20_paragraph / Default Paragraph Style / Block_20_Text
       -> Text_20_body / Quotations so all body paragraphs share consistent
       styles (mirrors BlockText->Blockquote, FirstParagraph->BodyText in
       sw_export_merge.py).
    2. When toc=True and the reference ODT has a TOCHeading paragraph, inject
       it directly before the <text:table-of-content> field so the heading
       comes from the template (mirrors sw_export_merge.py for docx).
       The TOCHeading style definition is also copied from the reference ODT
       if absent from the export.
    3. When new_page_headings=True, insert fo:break-before="page" on every
       Heading 1 paragraph after the first by assigning an automatic paragraph
       style that inherits from Heading_20_1 (mirrors _make_chapter_break in
       sw_export_merge.py).
    4. When restart_footnotes=True, set text:start-numbering-at="chapter" in
       the footnote notes-configuration so footnotes restart at 1 per chapter
       (mirrors the eachSect sectPr in sw_export_merge.py).
    Only repacks the ODT when something actually changed.
    """
    import zipfile, tempfile, shutil as _shutil, re as _re
    REMAPS = {
        'text:style-name="First_20_paragraph"':            'text:style-name="Text_20_body"',
        'text:style-name="Default_20_Paragraph_20_Style"': 'text:style-name="Text_20_body"',
        'text:style-name="Default Paragraph Style"':       'text:style-name="Text_20_body"',
        'text:style-name="Block_20_Text"':                 'text:style-name="Quotations"',
    }
    tmp = Path(tempfile.mkdtemp())
    try:
        with zipfile.ZipFile(odt_path, 'r') as z:
            z.extractall(tmp)

        content_xml = tmp / 'content.xml'
        styles_xml  = tmp / 'styles.xml'
        orig_content = content_xml.read_text(encoding='utf-8')
        orig_styles  = styles_xml.read_text(encoding='utf-8')
        content = orig_content
        styles  = orig_styles

        # 1. Body-style remaps
        for src, dst in REMAPS.items():
            content = content.replace(src, dst)

        # 2. TOC heading from reference ODT, plus page breaks around the TOC.
        TOC_MARKER = '<text:table-of-content>'
        if toc and TOC_MARKER in content:
            # Insert a page break before the TOC so it starts on its own page
            # rather than immediately following the title block.
            _SW_PB = 'SW_TOC_Pagebreak'
            _pb_para = f'<text:p text:style-name="{_SW_PB}"/>\n'
            if f'style:name="{_SW_PB}"' not in content:
                _pb_def = (
                    f'<style:style style:name="{_SW_PB}" style:family="paragraph">'
                    f'<style:paragraph-properties fo:break-before="page"/>'
                    f'</style:style>'
                )
                content = content.replace(
                    '</office:automatic-styles>',
                    _pb_def + '</office:automatic-styles>', 1)
            content = content.replace(TOC_MARKER, _pb_para + TOC_MARKER, 1)
            print('ODT: inserted page break before TOC')
        if toc and ref_doc_path and Path(ref_doc_path).exists() and TOC_MARKER in content:
            with zipfile.ZipFile(ref_doc_path, 'r') as rz:
                ref_content = rz.read('content.xml').decode('utf-8')
                ref_styles  = rz.read('styles.xml').decode('utf-8')
            m = _re.search(
                r'<text:p[^>]*text:style-name="TOCHeading"[^>]*>.*?</text:p>',
                ref_content, _re.DOTALL)
            if m and 'TOCHeading' not in content:
                content = content.replace(TOC_MARKER, m.group(0) + '\n' + TOC_MARKER, 1)
                print('Inserted TOCHeading paragraph from reference ODT')
            if 'TOCHeading' not in styles:
                ms = (_re.search(
                    r'<style:style[^>]*style:name="TOCHeading"[^>]*/>', ref_styles)
                    or _re.search(
                    r'<style:style[^>]*style:name="TOCHeading"[^>]*>.*?</style:style>',
                    ref_styles, _re.DOTALL))
                if ms:
                    styles = styles.replace('</office:styles>',
                                            ms.group(0) + '</office:styles>', 1)
                    print('Copied TOCHeading style from reference ODT')

        # 3. New-page headings: add fo:break-before="page" to every Heading 1
        #    paragraph after the first, via an automatic paragraph style.
        if new_page_headings:
            # ODT headings are <text:h> elements with text:outline-level,
            # NOT <text:p> elements.
            _H1   = 'Heading_20_1'
            _AUTO = 'SW_Heading1_Pagebreak'
            _h1_re = _re.compile(
                r'(<text:h\b[^>]*\btext:outline-level="1"[^>]*>)'
            )
            _hits = list(_h1_re.finditer(content))
            if _hits:
                # Apply page-break style to ALL Heading 1 elements —
                # documents begin with a title/metadata block, not a heading,
                # so even the first chapter heading needs a break.
                for _m in reversed(_hits):
                    old_tag = _m.group(1)
                    # Replace whatever style-name is on the heading with our auto-style
                    if 'text:style-name=' in old_tag:
                        new_tag = _re.sub(
                            r'text:style-name="[^"]*"',
                            f'text:style-name="{_AUTO}"',
                            old_tag, 1)
                    else:
                        new_tag = old_tag.replace('<text:h ',
                            f'<text:h text:style-name="{_AUTO}" ', 1)
                    content = content[:_m.start(1)] + new_tag + content[_m.end(1):]
                # Check for the style *definition* (style:name=), not just
                # any occurrence of the name (text:style-name= was just written above).
                if f'style:name="{_AUTO}"' not in content:
                    _auto_def = (
                        f'<style:style style:name="{_AUTO}" '
                        f'style:family="paragraph" '
                        f'style:parent-style-name="{_H1}">'
                        f'<style:paragraph-properties fo:break-before="page"/>'
                        f'</style:style>'
                    )
                    content = content.replace(
                        '</office:automatic-styles>',
                        _auto_def + '</office:automatic-styles>',
                        1)
                print(f'ODT: page-break added before {len(_hits)} Heading 1 paragraph(s)')

        # 4. Footnote restart per chapter
        if restart_footnotes:
            def _fix_fn_config(xml):
                _changed = False
                def _fn_replacer(m):
                    nonlocal _changed
                    elem = m.group(0)
                    if 'text:note-class="footnote"' not in elem:
                        return elem
                    if 'text:start-numbering-at=' in elem:
                        new_elem = _re.sub(
                            r'text:start-numbering-at="[^"]*"',
                            'text:start-numbering-at="chapter"',
                            elem)
                    else:
                        # Add before self-closing />
                        new_elem = elem[:-2].rstrip() + ' text:start-numbering-at="chapter"/>'
                    if new_elem != elem:
                        _changed = True
                    return new_elem
                new_xml = _re.sub(r'<text:notes-configuration\b[^>]*/>', _fn_replacer, xml)
                return new_xml, _changed
            _new_styles, _schanged = _fix_fn_config(styles)
            if _schanged:
                styles = _new_styles
                print('ODT: set footnote numbering to restart per chapter')
            else:
                _new_content, _cchanged = _fix_fn_config(content)
                if _cchanged:
                    content = _new_content
                    print('ODT: set footnote numbering to restart per chapter (content.xml)')

        # 5. Abstract and extra sections from note/sw-* YAML properties.
        #    Inserted just before the TOC SECTION (i.e. before the TOC heading
        #    paragraph that precedes <text:table-of-content>), so they fall
        #    between the document body and the table of contents.  Falls back to
        #    before the first chapter heading, or to the end of <office:text>.
        #    Mirrors _append_extra_sections / abstract injection in sw_export_merge.py.
        if abstract or extra_sections:
            import html as _html_mod
            # Find the insertion point: the last <text:h> opening tag that appears
            # before the first <text:table-of-content> block.  That last heading IS
            # the "Table of Contents" heading paragraph — inserting before it puts
            # new content between the body text and the whole TOC section.
            toc_start = content.find('<text:table-of-content')
            if toc_start >= 0:
                preceding_text = content[:toc_start]
                # Find all <text:h ...> opening positions before the TOC.
                last_h_pos = None
                for _hm in _re.finditer(r'<text:h\b', preceding_text):
                    last_h_pos = _hm.start()
                ins = last_h_pos if last_h_pos is not None else toc_start
            else:
                first_h = _re.search(r'<text:h\b', content)
                if first_h:
                    ins = first_h.start()
                else:
                    end_body = _re.search(r'</office:text>', content)
                    ins = end_body.start() if end_body else len(content)

            # ODT style names: spaces → _20_, & → _26_ (ODF encoding convention).
            _AKH = 'Abstract_20__26__20_keywords_20_heading'
            _TB  = 'Text_20_body'

            inject_xml = ''
            # Abstract first (mirrors position in template title blocks).
            if abstract:
                inject_xml += (
                    f'<text:p text:style-name="{_AKH}">{_html_mod.escape("Abstract")}</text:p>\n'
                    f'<text:p text:style-name="{_TB}">{_html_mod.escape(abstract)}</text:p>\n'
                )
            # Then note/sw-* extra sections.
            for key, value in (extra_sections or []):
                label = key.removeprefix('sw-').replace('-', ' ').title()
                inject_xml += (
                    f'<text:p text:style-name="{_AKH}">{_html_mod.escape(label)}</text:p>\n'
                    f'<text:p text:style-name="{_TB}">{_html_mod.escape(value)}</text:p>\n'
                )

            if inject_xml:
                content = content[:ins] + inject_xml + content[ins:]
                n_extra = len(extra_sections) if extra_sections else 0
                print(f'ODT: inserted abstract={bool(abstract)}, {n_extra} extra section(s) before TOC heading')

        if content == orig_content and styles == orig_styles:
            print('ODT post-process: nothing changed, skipping repack.')
            return

        if content != orig_content:
            content_xml.write_text(content, encoding='utf-8')
        if styles != orig_styles:
            styles_xml.write_text(styles, encoding='utf-8')

        tmp_odt = odt_path.with_suffix('.tmp.odt')
        with zipfile.ZipFile(tmp_odt, 'w') as zout:
            mimetype = tmp / 'mimetype'
            if mimetype.exists():
                zout.write(mimetype, 'mimetype', compress_type=zipfile.ZIP_STORED)
            for f in sorted(tmp.rglob('*')):
                if f.is_file() and f.name != 'mimetype':
                    zout.write(f, f.relative_to(tmp), compress_type=zipfile.ZIP_DEFLATED)
        odt_path.unlink()
        tmp_odt.rename(odt_path)
        print('ODT post-processing complete.')
    finally:
        _shutil.rmtree(tmp)

def _prep_reference_odt(ref_doc_path, style_names):
    """Return a temp copy of ref_doc_path with sentinel styles pre-injected into
    styles.xml's <office:styles> section, so pandoc can apply custom-style
    attributes for styles not yet defined in the user's template.

    Returns (path_to_use, temp_dir_to_cleanup).  If no injection is needed
    (all styles present, or ref_doc_path is None) returns (ref_doc_path, None).
    """
    import zipfile, shutil as _sh, tempfile
    if not ref_doc_path or not os.path.exists(ref_doc_path) or not style_names:
        return ref_doc_path, None

    def to_odt_name(s):
        return s.replace(' ', '_20_')

    # Which styles are missing from the template?
    try:
        with zipfile.ZipFile(ref_doc_path, 'r') as z:
            tpl_text = ''.join(
                z.read(n).decode('utf-8')
                for n in ['styles.xml', 'content.xml']
                if n in z.namelist()
            )
        # Normalise: LibreOffice may store style:name with actual spaces or _20_,
        # and some styles are stored with a distinct style:display-name that
        # differs from the internal style:name (e.g. "ListNumberTight" / "List
        # Number Tight"). Scan both so we don't pre-inject a style that's
        # already in the template under the alternate encoding or display name.
        _raw_tpl = (set(re.findall(r'style:name="([^"]+)"', tpl_text))
                    | set(re.findall(r'style:display-name="([^"]+)"', tpl_text)))
        defined = set()
        for _dn in _raw_tpl:
            defined.add(_dn)
            defined.add(_dn.replace(' ', '_20_'))
            defined.add(_dn.replace('_20_', ' '))
            defined.add(_dn.replace(' ', '').replace('_20_', ''))   # "Footnote Reference" → "FootnoteReference"
        missing_human = [n for n in style_names if to_odt_name(n) not in defined]
    except Exception as e:
        print(f'WARNING: could not inspect reference ODT for pre-injection: {e}')
        return ref_doc_path, None

    if not missing_human:
        return ref_doc_path, None

    missing_odt = [to_odt_name(n) for n in missing_human]
    print(f'Pre-injecting sentinel styles into reference ODT copy: {missing_odt}')

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        extract_dir = tmp_dir / 'extracted'
        extract_dir.mkdir()
        with zipfile.ZipFile(ref_doc_path, 'r') as z:
            z.extractall(extract_dir)

        styles_path = extract_dir / 'styles.xml'
        if not styles_path.exists():
            _sh.rmtree(tmp_dir, ignore_errors=True)
            return ref_doc_path, None

        styles = styles_path.read_text(encoding='utf-8')
        injection = ''.join(
            # Paragraph style — background at paragraph level.
            f'<style:style style:name="{n}" style:display-name="{h}"'
            f' style:family="paragraph"'
            f' style:parent-style-name="Default_20_Paragraph_20_Style">'
            f'<style:paragraph-properties fo:background-color="{color}"/>'
            f'<style:text-properties fo:background-color="{color}"/>'
            f'</style:style>'
            # Character style — same name, family="text", used by Span handler.
            f'<style:style style:name="{n}" style:display-name="{h}"'
            f' style:family="text">'
            f'<style:text-properties fo:background-color="{color}"/>'
            f'</style:style>'
            for i, (n, h) in enumerate(zip(missing_odt, missing_human))
            for color in [_SENTINEL_COLORS[i % len(_SENTINEL_COLORS)]]
        )
        if '</office:styles>' not in styles:
            _sh.rmtree(tmp_dir, ignore_errors=True)
            return ref_doc_path, None
        styles = styles.replace('</office:styles>', injection + '</office:styles>', 1)
        styles_path.write_text(styles, encoding='utf-8')

        tmp_odt = tmp_dir / 'reference_with_sentinels.odt'
        with zipfile.ZipFile(tmp_odt, 'w', zipfile.ZIP_DEFLATED) as zout:
            mimetype = extract_dir / 'mimetype'
            if mimetype.exists():
                zout.write(mimetype, 'mimetype', compress_type=zipfile.ZIP_STORED)
            for item in sorted(extract_dir.rglob('*')):
                if item.is_file() and item.name != 'mimetype':
                    zout.write(item, item.relative_to(extract_dir))

        return str(tmp_odt), str(tmp_dir)
    except Exception as e:
        print(f'WARNING: pre-injection of reference ODT failed: {e}')
        _sh.rmtree(tmp_dir, ignore_errors=True)
        return ref_doc_path, None


def export_odt(compiled_md, vault_root=None, template=None, toc=False,
               template_dir=None, output_dir=None, default_author=None,
               new_page_headings=True, restart_footnotes=True,
               mappings_data=None):
    """Export compiled markdown to ODT. Thin wrapper around export_document."""
    return export_document('odt', compiled_md,
                           vault_root=vault_root, template=template, toc=toc,
                           template_dir=template_dir, output_dir=output_dir,
                           default_author=default_author,
                           new_page_headings=new_page_headings,
                           restart_footnotes=restart_footnotes,
                           mappings_data=mappings_data)



def export_pdf(compiled_md, vault_root=None, template=None, toc=False,
               template_dir=None, output_dir=None,
               new_page_headings=True, restart_footnotes=True,
               intermediate_format=None, keep_intermediate=False,
               mappings_data=None):
    """Export to PDF via an intermediate ODT or DOCX file.

    The intermediate format is auto-determined from the template: ODT is
    preferred when an ODT template file exists, otherwise DOCX is used.
    Pass intermediate_format='docx' or 'odt' to override.

    Uses LibreOffice headless for conversion when available (produces a
    layout-faithful PDF matching what you see in the word processor).
    Falls back to pandoc if LibreOffice is not found (requires a PDF engine
    such as xelatex or weasyprint to be installed separately).
    """
    import tempfile, shutil as _sh
    compiled_md = Path(compiled_md)
    vault_root = vault_root or vault_rel()
    out_dir = Path(output_dir).expanduser() if output_dir else compiled_md.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # Auto-determine intermediate format when not explicitly specified.
    if intermediate_format is None:
        text = compiled_md.read_text(encoding='utf-8')
        meta = _parse_yaml_metadata(text, compiled_md.stem)
        tpl  = template if template is not None else meta['tpl']
        template_dir_r = resolve_template_dir(template_dir, vault_root)
        odt_candidates = []
        if template_dir_r:
            odt_candidates.append(os.path.join(template_dir_r, f'{tpl}.odt'))
        odt_candidates.append(plugin_template_path(f'{tpl}.odt'))
        if any(os.path.exists(c) for c in odt_candidates):
            intermediate_format = 'odt'
        else:
            intermediate_format = 'docx'
        print(f'PDF intermediate format auto-determined: {intermediate_format}')

    # Export to the intermediate format in a temp directory.
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        common_kwargs = dict(
            vault_root=vault_root, template=template, toc=toc,
            template_dir=template_dir, output_dir=str(tmp_dir),
            new_page_headings=new_page_headings,
            restart_footnotes=restart_footnotes,
            mappings_data=mappings_data)
        if intermediate_format == 'docx':
            inter_path = Path(export_docx(compiled_md, **common_kwargs))
        else:
            inter_path = Path(export_odt(compiled_md, **common_kwargs))

        out_pdf = out_dir / f"{compiled_md.stem}.pdf"

        # Prefer LibreOffice headless — it renders the document identically to
        # what the user sees on screen, honouring all template styles.
        soffice = find_soffice()
        if soffice:
            import subprocess
            cmd = [soffice, '--headless', '--convert-to', 'pdf',
                   '--outdir', str(out_dir), str(inter_path)]
            print('Converting to PDF via LibreOffice:', ' '.join(cmd))
            subprocess.run(cmd, check=True)
            # LibreOffice names the output <stem>.pdf in outdir.
            lo_out = out_dir / f"{inter_path.stem}.pdf"
            if lo_out.exists() and lo_out != out_pdf:
                lo_out.rename(out_pdf)
        else:
            # Fallback: pandoc (needs xelatex / weasyprint / wkhtmltopdf).
            import subprocess
            pandoc = os.environ.get('SW_PANDOC', 'pandoc')
            cmd = [pandoc, str(inter_path), '-o', str(out_pdf)]
            print('Converting to PDF via pandoc:', ' '.join(cmd))
            subprocess.run(cmd, check=True)

        if keep_intermediate:
            dest = out_dir / inter_path.name
            if inter_path != dest:
                _sh.copy2(str(inter_path), str(dest))
            print(f'Intermediate {intermediate_format.upper()} kept at: {dest}')

        print(f'\nExported PDF written to {out_pdf}')
        return str(out_pdf)
    finally:
        _sh.rmtree(tmp_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description='Compile an Obsidian book outline to markdown (and optionally export to docx)')
    parser.add_argument('master_file', help='Path to the master markdown file (outline or compiled)')
    parser.add_argument('--global-footnotes', action='store_true',
                       help='Use global footnote numbering (default for article* templates; override for book* templates)')
    parser.add_argument('--no-global-footnotes', action='store_true',
                       help='Restart footnote numbering per chapter (default for book* templates; override for article* templates)')
    parser.add_argument('--export', action='store_true',
                       help='Also export to docx/odt via pandoc (see --format)')
    parser.add_argument('--format', dest='export_format', default='docx',
                       choices=['docx', 'odt', 'pdf'],
                       help='Export format when --export is set: docx (default), odt, or pdf')
    parser.add_argument('--keep-intermediate', action='store_true',
                       help='Keep the intermediate docx/odt when exporting to PDF')
    parser.add_argument('--template', default=None,
                       help='Override the template (default: read "template" YAML from the master file)')
    parser.add_argument('--toc', action='store_true',
                       help='Include a TOC field in the exported docx (default for book* templates)')
    parser.add_argument('--no-toc', action='store_true',
                       help='Omit the TOC field (default for article* templates; override for book* templates)')
    parser.add_argument('--new-page-headings', action='store_true', default=True,
                       help='Start each heading section on a new page (default: on)')
    parser.add_argument('--no-new-page-headings', action='store_true',
                       help='Do not insert page breaks before headings')
    parser.add_argument('--default-author', default=None,
                       help='Fallback author name when the document has no author property')
    parser.add_argument('--templates-dir', default=None,
                       help='Directory of user .docx/.odt export templates (default: <vault>/Export Templates/, '
                            'then the plugin\'s bundled templates/)')
    parser.add_argument('--output-dir', default=None,
                       help='Directory for the compiled markdown and exported file (default: the source '
                            'file\'s own folder)')
    parser.add_argument('--mappings', default=None,
                       help='JSON array of {source, styleName} objects for style mappings '
                            '(overrides mappings.json in the templates directory)')

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
    effective_tpl = re.sub(r'\.(docx|odt)$', '', effective_tpl or 'document', flags=re.IGNORECASE)

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
        print(f"Template: {effective_tpl} — TOC {'on' if use_toc else 'off'}, "
              f"footnotes {'global' if use_global else 'per-chapter'}")
        compiled = master_file

    if args.export:
        active_mappings = load_mappings(args.templates_dir, args.mappings)
        _common = dict(
            template=args.template or effective_tpl, toc=use_toc,
            template_dir=args.templates_dir, output_dir=args.output_dir,
            new_page_headings=not args.no_new_page_headings,
            restart_footnotes=not use_global,
            mappings_data=active_mappings)
        if args.export_format == 'pdf':
            export_pdf(compiled, keep_intermediate=args.keep_intermediate, **_common)
        elif args.export_format == 'odt':
            export_odt(compiled, **_common)
        else:
            export_docx(compiled, default_author=args.default_author, **_common)

if __name__ == "__main__":
    import sys
    main()
