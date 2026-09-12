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
import sys
from pathlib import Path
from collections import defaultdict
import argparse

# ── vault / plugin path resolution ───────────────────────────────────────────
# The script lives in <vault>/.obsidian/plugins/scholar-weave/scripts/ (or a
# copy/symlink of it). The plugin passes the vault root explicitly in the
# SW_VAULT env var (from adapter.getBasePath()); when the script is run
# standalone we derive it from THIS FILE's real location instead
# (os.path.realpath resolves symlinks), walking up
#   scripts → scholar-weave → plugins → .obsidian → <vault>.

_PLUGIN_SCRIPTS_DIR = Path(os.path.dirname(os.path.realpath(os.path.abspath(__file__))))
_PLUGIN_DIR = _PLUGIN_SCRIPTS_DIR.parent            # …/plugins/scholar-weave

sys.path.insert(0, str(_PLUGIN_SCRIPTS_DIR))
# Shared, format-agnostic helpers also used by the DOCX/ODT merge scripts —
# cover-value resolution (title/subtitle/author/date-default) and the
# roman-frontmatter reset-heading finder. The LaTeX export path (no merge
# step of its own) reuses these directly rather than reimplementing them.
from sw_merge_helpers import (  # noqa: E402
    resolve_cover, find_page_reset_index, looks_like_caption, strip_figure_prefix,
    append_extra_sections, resolve_note_sections,
    strip_bibliography_heading_from_markdown,
)


def _find_vault_root():
    env = os.environ.get('SW_VAULT')
    if env and (Path(env) / '.obsidian').is_dir():
        return Path(env)
    # Walk up from the script (installed: …/.obsidian/plugins/scholar-weave/
    # scripts; dev checkout: …/<vault>/src/ScholarWeave/scripts) and from the
    # cwd, looking for the directory that contains a `.obsidian/` folder.
    for start in (_PLUGIN_SCRIPTS_DIR, Path.cwd()):
        for d in (start, *start.parents):
            if (d / '.obsidian').is_dir():
                return d
    raise SystemExit(
        'Could not locate the Obsidian vault. Run this from inside a vault, '
        "or set SW_VAULT to the vault's root path. "
        f'(script at {_PLUGIN_SCRIPTS_DIR}, cwd {Path.cwd()})')


_VAULT_ABS = _find_vault_root()

# Propagate to child processes (pandoc + sw-export.lua read SW_VAULT).
os.environ.setdefault('SW_VAULT', str(_VAULT_ABS))

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

def _resolve_template_path(tpl, ext, template_dir):
    """Template file candidate chain, shared by every export format: the
    user's own templates dir (if any) → the plugin's bundled templates/ →
    same two locations for 'document<ext>' as a final fallback. Returns the
    first candidate that exists, else the last candidate (so callers can
    still report a clear "not found" using the same path they tried)."""
    candidates = []
    if template_dir:
        candidates.append(os.path.join(template_dir, f'{tpl}{ext}'))
    candidates.append(plugin_template_path(f'{tpl}{ext}'))
    if template_dir:
        candidates.append(os.path.join(template_dir, f'document{ext}'))
    candidates.append(plugin_template_path(f'document{ext}'))
    path = next((c for c in candidates if os.path.exists(c)), candidates[-1])
    if not os.path.exists(path):
        print(f"WARNING: no template found for '{tpl}' (and no document{ext} fallback). "
              f"Tried: {candidates}")
    elif not path.endswith(f'{tpl}{ext}'):
        print(f"WARNING: template '{tpl}{ext}' not found; using {path} as fallback.")
    return path

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

# Excalidraw drawings are embedded as ![[Name.excalidraw]] (Obsidian omits the
# trailing .md). The Excalidraw plugin keeps sidecar image files next to the
# drawing when autoexportSVG / autoexportPNG are on ("Name.excalidraw.svg" /
# "Name.excalidraw.png", kept in sync with the drawing). We embed the sidecar
# instead of the drawing source. PNG is tried first: it is a faithful raster of
# what Obsidian shows, whereas Word and LibreOffice render the SVG with a
# substitute font (the Excalidraw hand-drawn font isn't installed) and
# LibreOffice drops some shape fills. SVG is the fallback when no PNG sidecar
# exists.
_EXCALIDRAW_SIDECAR_FORMATS = ('png', 'svg')


def _parse_embed_pipe(parts):
    """Split the segments after the first '|' of an Obsidian embed into
    (alt_text, width, height). A purely numeric segment (optionally NxN) is a
    pixel size; anything else is alt text."""
    alt_bits, width, height = [], None, None
    for seg in parts[1:]:
        dm = re.fullmatch(r'(\d+)(?:x(\d+))?', seg)
        if dm:
            width, height = dm.group(1), dm.group(2)
        elif seg:
            alt_bits.append(seg)
    return ' '.join(alt_bits), width, height


def _embed_markdown(resolved, alt, width, height):
    """Build the pandoc image markdown for a resolved attachment path."""
    rel = os.path.relpath(resolved, os.getcwd())
    attrs = ''
    if width:
        dims = ['width=%spx' % width]
        if height:
            dims.append('height=%spx' % height)
        # No space before '{' — pandoc only reads it as an image attribute
        # when it is directly adjacent to the ')'.
        attrs = '{%s}' % ' '.join(dims)
    return f'![{alt}]({rel}){attrs}'


def resolve_embed_links(content: str) -> str:
    """Rewrite Obsidian image embeds (![[name.ext]]) to vault-relative paths
    so pandoc can fetch them. Without this, pandoc looks relative to the
    compiled file's directory and replaces the image with its filename.
    Excalidraw embeds (![[name.excalidraw]]) resolve to their exported sidecar
    image. Non-image embeds (![[other note]]) are left untouched (wikilinks).
    """
    img_ext = r'(?:png|jpe?g|gif|bmp|tiff?|webp|svg)'
    def repl(m):
        # Obsidian pipe syntax: ![[img.png|alt text]] (alt) and
        # ![[img.png|454]] / ![[img.png|454x300]] (display size in px).
        # Split on '|' FIRST — the filename/extension is only in the first
        # segment; a purely numeric (optionally NxN) segment is a size, any
        # other segment is alt text. Without this the whole "name|454" string
        # fails the extension check, "454" leaks into the caption, and pandoc
        # renders no image.
        parts = [p.strip() for p in m.group(1).split('|')]
        target = parts[0].strip()

        # Excalidraw: ![[Name.excalidraw]] / [[...excalidraw.md]] / with a
        # #frame or #^ref fragment — always embed the whole-drawing sidecar.
        exc = re.match(r'(?i)^(.*\.excalidraw)(?:\.md)?(?:#.*)?$', target)
        if exc:
            base = exc.group(1)
            resolved = next(
                (p for ext in _EXCALIDRAW_SIDECAR_FORMATS
                 for p in (resolve_attachment_path(f'{base}.{ext}'),) if p),
                None,
            )
            if resolved is None:
                # Leaving the raw ![[...]] wikilink in place used to look
                # like a mysterious parsing bug: pandoc's own wikilinks
                # extension (-f markdown+wikilinks_title_after_pipe, needed
                # elsewhere) still parses an unresolved image embed as
                # ![pipe-part](target) — so a size like "|500" silently
                # became the alt text and the raw wikilink target became a
                # literal (nonexistent) file path, with no visible warning.
                print(f'WARNING: Excalidraw drawing "{base}" has no '
                      f'exported PNG/SVG sidecar next to it (re-export the '
                      f'drawing — e.g. open it in Excalidraw and save — or '
                      f'check it hasn\'t been renamed since the sidecar was '
                      f'last generated). Substituting a placeholder.',
                      file=sys.stderr)
                return f'**[Missing image: {base}]**'
            alt, width, height = _parse_embed_pipe(parts)
            return _embed_markdown(resolved, alt or re.sub(r'\.excalidraw$', '', base, flags=re.I),
                                   width, height)

        if not re.search(rf'\.{img_ext}$', target, re.IGNORECASE):
            return m.group(0)
        alt, width, height = _parse_embed_pipe(parts)
        resolved = resolve_attachment_path(target)
        if resolved is None:
            print(f'WARNING: Image "{target}" not found in vault attachments. '
                  f'Substituting a placeholder.', file=sys.stderr)
            return f'**[Missing image: {target}]**'
        return _embed_markdown(resolved, alt or target, width, height)
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


def strip_wikilinks(text: str) -> str:
    """Replace Obsidian wikilinks with their display text.
    [[Target|alias]] -> alias
    [[Target]]       -> Target (leading order-numbers stripped, extension dropped)
    ![[...]]         -> left untouched (image embeds handled by resolve_embed_links)
    [[@key...]]      -> left untouched (citations already converted before this runs)

    Wikilinks inside fenced code blocks and inline code spans are left VERBATIM
    (pandoc renders code literally, so a `[[Note]]` example must survive).
    """
    def _replace_wl(inner):
        if '|' in inner:
            return inner.split('|', 1)[1]
        name = re.sub(r'\.(md|markdown)$', '', inner, flags=re.I)
        name = re.sub(r'^\d[\d.\-]*\s+', '', name)
        return name

    # One pass over: fenced block | inline code span | wikilink. Only the
    # wikilink alternative is rewritten; code is emitted unchanged.
    token = re.compile(
        r'(?P<fence>^[ \t]*(?P<f>`{3,}|~{3,})[^\n]*\n.*?^[ \t]*(?P=f)[ \t]*$)'
        r'|(?P<code>(?P<tick>`+)(?:(?!(?P=tick)).)+(?P=tick))'
        r'|(?<!!)\[\[(?P<wl>[^\[\]]+)\]\]',
        re.M | re.S)

    def _sub(m):
        if m.group('wl') is None:      # matched a fenced block or a code span
            return m.group(0)
        return _replace_wl(m.group('wl'))

    return token.sub(_sub, text)

def linkify_bare_urls(text: str) -> str:
    """Convert bare http(s) URLs to [url](url) markdown links.
    Strips trailing sentence punctuation. Skips URLs already in link syntax.
    """
    _BARE_URL = re.compile(r'(?<![\\[(])(https?://[^\s<>\)\]]+)(?![\)\]])')
    _TRAIL    = re.compile(r'[.,;:!?]+$')
    def _sub(m):
        url = m.group(1)
        trail_m = _TRAIL.search(url)
        if trail_m:
            trail = trail_m.group(0)
            url   = url[:-len(trail)]
            suffix = trail
        else:
            suffix = ''
        return '[' + url + '](' + url + ')' + suffix
    return _BARE_URL.sub(_sub, text)


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

_OUTLINE_LIST_ITEM_RE = re.compile(r'^([-*+]|\d+[.)])\s')
_OUTLINE_INCLUDE_RE = re.compile(r'^\[\[([^\[\]]+)\]\]$')


def outline_bullet_is_include(item: str) -> bool:
    """True when a bullet's text is a single non-citekey wikilink — the same
    rule parse_outline uses to decide a bullet is a note include. `item` must
    already have its list marker stripped."""
    rest = re.sub(r'^x\s*', '', item)
    if rest.startswith('@@'):
        rest = rest[2:].strip()
    m = _OUTLINE_INCLUDE_RE.match(rest.strip())
    return bool(m and not m.group(1).strip().startswith('@'))


def detect_outline(text: str) -> bool:
    """Structural check for an OUTLINE (needs compiling) vs a compiled note or
    a plain note that should just be rendered.

    An outline is a document whose body is made up ENTIRELY of list items —
    no ordinary prose paragraphs — and that contains at least one note
    INCLUDE: a bullet that is a single `[[wikilink]]` (the includes may sit
    beneath plain-text heading bullets). A bullet list inside an ordinary note
    is NOT an outline: a prose paragraph anywhere disqualifies the document,
    and a list of only plain-text/topic bullets has no includes, hence no note
    content to compile. Explicit `template: compile-<name>` still overrides
    this (see main())."""
    _, body = extract_yaml(text)
    has_include = False
    for raw in body.splitlines():
        if not raw.strip():
            continue
        stripped = raw.lstrip()
        # Any ATX heading means the document is already compiled.
        if stripped.startswith('#'):
            return False
        # Indented continuation of a list item (or a block scalar) — not prose.
        if raw[:1] in (' ', '\t') and not _OUTLINE_LIST_ITEM_RE.match(stripped):
            continue
        if _OUTLINE_LIST_ITEM_RE.match(stripped):
            item = _OUTLINE_LIST_ITEM_RE.sub('', stripped, count=1).strip()
            if outline_bullet_is_include(item):
                has_include = True
            continue
        # Footnote definitions and horizontal rules don't disqualify.
        if stripped.startswith('[^') or re.fullmatch(r'([-*_])\1{2,}', stripped):
            continue
        # Any other non-indented line is an ordinary prose paragraph.
        return False
    return has_include

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
    builtin = {'sw-doc-title.lua', 'sw-export.lua', 'sw-poetry.lua', 'sw-bidi.lua', 'sw-zotero.lua'}
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


def ensure_blank_before_headings(text: str) -> str:
    """Obsidian renders any line starting with '# ' as a heading regardless of
    what precedes it. Pandoc (treating single newlines as soft breaks) folds a
    heading that directly follows a paragraph or list item into that block as
    literal text — "# Research Process" ends up mid-sentence. Insert a blank
    line before every ATX heading that isn't already preceded by one. (Pandoc's
    +lists_without_preceding_blankline extension already covers the mirror-image
    problem for lists.) Lines inside fenced code blocks are left untouched so a
    '# comment' in a code sample is never treated as a heading.
    """
    out = []
    fence = None          # opening fence marker while inside a code block
    prev_blank = True     # start of file behaves as if preceded by a blank line
    for line in text.split('\n'):
        fence_m = re.match(r'\s*(`{3,}|~{3,})', line)
        if fence is not None:
            out.append(line)
            if fence_m and fence_m.group(1)[0] == fence[0] \
                    and len(fence_m.group(1)) >= len(fence):
                fence = None
            prev_blank = False
            continue
        if fence_m:
            fence = fence_m.group(1)
            out.append(line)
            prev_blank = False
            continue
        if re.match(r'#{1,6} ', line) and not prev_blank:
            out.append('')
        out.append(line)
        prev_blank = not line.strip()
    return '\n'.join(out)


#: A bracketed pandoc citation containing an @citekey — e.g. [@key],
#: [see @key, 45], [@a; @b], [-@key]. Excludes ] and newlines (pandoc
#: citations never contain either).
_BRACKET_CITE_RE = re.compile(
    r'[ \t]*'
    r'(?P<cite>\[[^\[\]\n]*?-?@[A-Za-z][\w:.#$%&+?<>~/-]*[^\[\]\n]*\])'
    r'(?P<trail>["\'“”‘’]?[.,;:!?]*["\'”’]?)'
)


def _sub_line_citations(line, defs, counter):
    """Rewrite bracketed citations on one line to footnote references,
    skipping inline-code spans. Appends `[^zoterociteN]: <citation>` strings
    to `defs`."""
    def repl(m):
        counter[0] += 1
        label = 'zoterocite%d' % counter[0]
        defs.append('[^%s]: %s' % (label, m.group('cite')))
        # Trailing sentence punctuation moves BEFORE the marker (Zotero's own
        # note conversion leaves it after — this is the fix).
        return '%s[^%s]' % (m.group('trail'), label)

    parts = re.split(r'(`+[^`]*`+)', line)  # keep code spans as odd segments
    for i in range(0, len(parts), 2):
        parts[i] = _BRACKET_CITE_RE.sub(repl, parts[i])
    return ''.join(parts)


def citations_to_footnotes(text):
    """For a note/footnote citation style: move every in-text bracketed
    citation into a real footnote — `word [@key].` becomes `word.[^zoterociteN]`
    plus an appended `[^zoterociteN]: [@key]` definition. Pandoc then renders
    proper footnotes, and sw-zotero.lua converts the [@key] inside each one to
    a Zotero field — matching the structure Zotero produces when a document is
    switched to a note style, and fixing the punctuation placement Zotero
    leaves wrong.

    Skips fenced code, inline code, existing footnote definitions (a citation
    there is already in a note), and heading lines.
    """
    lines = text.split('\n')
    out, defs, counter = [], [], [0]
    fence = None
    in_fndef = False
    for line in lines:
        stripped = line.lstrip()
        fence_m = re.match(r'(`{3,}|~{3,})', stripped)
        if fence is not None:
            out.append(line)
            if fence_m and stripped.startswith(fence):
                fence = None
            continue
        if fence_m:
            fence = fence_m.group(1)
            out.append(line)
            continue
        if re.match(r'[ \t]*\[\^[^\]\n]+\]:', line):
            in_fndef = True
            out.append(line)
            continue
        if in_fndef:
            if line.strip() == '' or line[:1] in (' ', '\t'):
                out.append(line)
                continue
            in_fndef = False
        if re.match(r'#{1,6} ', line):
            out.append(line)
            continue
        out.append(_sub_line_citations(line, defs, counter))
    if defs:
        if out and out[-1].strip() != '':
            out.append('')
        out.extend(defs)
    return '\n'.join(out)


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


def find_latex_engine():
    """Find the lualatex binary (pandoc's --pdf-engine for LaTeX export).

    LuaLaTeX is required for Arabic/RTL content: the templates use babel's
    `bidi=basic` (LuaTeX's node-based UAX#9 implementation — no macro
    expansion). XeLaTeX's macro-based bidi/polyglossia alternatives crash a
    real multi-footnote document with "TeX capacity exceeded [main memory
    size=5000000]" inside hyperref's bidi-compatibility code at the first
    \\footnote (confirmed empirically); babel bidi=basic has no such limit and
    also auto-detects inline Arabic runs (see the templates' Arabic note)."""
    import shutil as _sh
    candidates = [
        os.environ.get('SW_LUALATEX', ''),
        'lualatex',
        '/Library/TeX/texbin/lualatex',            # MacTeX / BasicTeX
        '/usr/bin/lualatex',                        # Linux (distro TeX Live)
        '/usr/local/bin/lualatex',
        r'C:\Program Files\MiKTeX\miktex\bin\x64\lualatex.exe',
        r'C:\texlive\2026\bin\windows\lualatex.exe',
    ]
    for c in candidates:
        if c and _sh.which(c):
            return c
    return None


#: Plain-text -> LaTeX special-character escaping, for values (author name,
#: short title) substituted into a .tex template's raw preamble — these are
#: NOT markdown, so pandoc's own escaping never sees them.
_LATEX_ESCAPE_MAP = {
    '\\': r'\textbackslash{}',
    '&': r'\&', '%': r'\%', '$': r'\$', '#': r'\#',
    '_': r'\_', '{': r'\{', '}': r'\}',
    '~': r'\textasciitilde{}', '^': r'\textasciicircum{}',
}
_LATEX_ESCAPE_RE = re.compile('|'.join(re.escape(c) for c in _LATEX_ESCAPE_MAP))


def _latex_escape(text):
    if not text:
        return ''
    return _LATEX_ESCAPE_RE.sub(lambda m: _LATEX_ESCAPE_MAP[m.group(0)], text)


#: A solo image markdown line — `![alt](path)` optionally followed by a
#: pandoc attribute block on the same line, e.g. `{width=200px}`.
_LATEX_SOLO_IMAGE_RE = re.compile(
    r'^(!\[)[^\]]*(\]\([^)\n]+\)(?:\{[^}\n]*\})?)[ \t]*\n'
    r'(?:\n+([^\n]+(?:\n[^\n]+)*))?',
    re.M,
)


def latex_process_images(text):
    """Make every solo image embed a floating, centered figure, and give it
    a real caption ONLY when an explicit 'Figure...' paragraph follows it —
    same vault convention DOCX/ODT apply (centered image styles +
    looks_like_caption/strip_figure_prefix in the merge step); there is no
    merge step for LaTeX, so this runs on the compiled markdown instead,
    before pandoc.

    resolve_embed_links() always gives an image non-empty alt text (the
    Obsidian embed's own alt, or else the filename) so pandoc's own image
    handling has something to work with. But pandoc's `implicit_figures`
    extension treats ANY solo image with non-empty alt text as a captioned,
    numbered figure using that alt text as the caption — so left alone,
    every image would get a "dummy" filename-derived caption, whether or
    not the note actually captioned it.

    When a 'Figure...' paragraph follows, the alt text becomes the real
    (unnumbered — pandoc computes the figure number) description and the
    now-redundant caption paragraph is dropped, so implicit_figures produces
    one correctly-captioned figure. Otherwise the alt text is cleared so
    implicit_figures does not trigger — no numbering, no caption, matching
    DOCX/ODT's "no Figure paragraph -> no caption" behaviour — but the image
    is still wrapped in a plain (uncaptioned) `figure` environment rather
    than left as an ordinary inline \\includegraphics: without SOME float
    environment, a large image that doesn't fit the remaining space on a
    page still can't split, so it just pushes everything after it onto the
    next page, leaving the current page short — exactly the kind of gap
    \\raggedbottom (see the templates) accepts for text but an image doesn't
    need to cause, since a float can instead move up to fill that space with
    a nearby smaller block of text, or move itself to the top/bottom of the
    best-fitting page. A caption-less `figure` gets no number and no ToF
    entry — \\caption{} is what creates both — so this is purely a placement
    mechanism, invisible in the output except for where the image ends up.
    Returns (text, has_figures) — has_figures is True when at least one
    image got a real caption, so the caller can skip \\listoffigures (tof)
    the same way DOCX/ODT skip an empty Table of Figures when there's
    nothing to list.
    """
    found_caption = [False]
    def repl(m):
        prefix, rest, maybe_next = m.group(1), m.group(2), m.group(3)
        if maybe_next and looks_like_caption(maybe_next):
            found_caption[0] = True
            desc = ' '.join(strip_figure_prefix(maybe_next).split())
            desc = desc.replace(']', '\\]')
            return f'{prefix}{desc}{rest}\n'
        img = (f'```{{=latex}}\n\\begin{{figure}}\n\\centering\n```\n'
               f'{prefix}{rest}\n```{{=latex}}\n\\end{{figure}}\n```\n')
        if maybe_next:
            return f'{img}\n{maybe_next}'
        return img
    text = _LATEX_SOLO_IMAGE_RE.sub(repl, text)
    return text, found_caption[0]


_CITEKEY_TOKEN_RE = re.compile(r'(?<![\w@.])-?@([A-Za-z][\w:.#$%&+?<>~/-]*)')


def _extract_citekeys(text):
    """Best-effort scan for pandoc citation keys (@key) in compiled markdown,
    used to pre-fetch a static CSL-JSON bibliography before pandoc runs.
    Mirrors the character class the plugin's own parser uses for a citation
    token (src/parser/parser.ts's /@[^\\s,;\\]\\[]*/), tightened to avoid
    matching emails/handles in prose. A stray false positive just costs a
    harmless "not found" lookup; missing a real key would silently drop that
    citation instead, so this stays intentionally permissive.
    """
    keys = set()
    for m in _CITEKEY_TOKEN_RE.finditer(text):
        key = m.group(1).rstrip('.,;:\'")]')
        if key:
            keys.add(key)
    return sorted(keys)


def _parse_zotero_meta(text):
    """Read the 'zotero:' YAML frontmatter block (csl-style, client, library)
    that sw-zotero.lua normally reads itself — needed here only for the
    static-citation (PDF) path, where Python builds the bibliography instead
    of the Lua filter building live Zotero fields.
    """
    import yaml
    yaml_block, _ = extract_yaml(text)
    z = {}
    if yaml_block:
        try:
            data = yaml.safe_load(yaml_block)
            if isinstance(data, dict):
                z = data.get('zotero') or {}
        except yaml.YAMLError:
            z = {}
    csl_style = z.get('csl-style')
    if csl_style == 'apa7':
        csl_style = 'apa'
    return {
        'csl_style': csl_style or 'apa',
        'client':    z.get('client') or 'zotero',
        'library':   z.get('library'),
    }


def _fetch_zotero_csl_items(citekeys, csl_style, client='zotero', library=None):
    """Fetch CSL-JSON bibliography data for citekeys via Better BibTeX's
    JSON-RPC — the same endpoint/method sw-zotero.lua's load_items() uses
    (item.pandoc_filter). Returns a list of CSL-JSON item dicts ready for
    pandoc's --bibliography: Better BibTeX already sets each item's own "id"
    field to the citekey itself (confirmed via a live call to this endpoint),
    so no remapping is needed the way sw-zotero.lua must remap to Zotero's
    internal numeric item ID for its live-field use case.

    Missing/duplicate citekeys are reported but don't fail the export;
    pandoc will just leave those citations unresolved.
    """
    if not citekeys:
        return []
    import urllib.request
    import urllib.parse
    import urllib.error
    port = 24119 if client == 'jurism' else 23119
    base_url = f'http://127.0.0.1:{port}/better-bibtex/json-rpc?'
    payload = {
        'jsonrpc': '2.0',
        'method':  'item.pandoc_filter',
        'params': {
            'citekeys': citekeys,
            'style':    csl_style or 'apa',
            'asCSL':    True,
        },
    }
    if library:
        payload['params']['libraryID'] = library
    url = base_url + urllib.parse.quote(json.dumps(payload))
    # A book-length document can have 100+ citekeys in one request; Better
    # BibTeX resolving all of them against a large library can genuinely take
    # a while, and this is a one-time build step — waiting longer is far
    # better than failing outright. A quick "not running" call (no citekeys
    # queued for a live document) fails via immediate connection-refused
    # well before this, so a long timeout here doesn't slow that case down.
    try:
        with urllib.request.urlopen(url, timeout=90) as resp:
            body = resp.read().decode('utf-8')
    except (OSError, urllib.error.URLError) as e:
        raise RuntimeError(
            'PDF export needs Zotero running (with Better BibTeX installed) '
            f'to fetch citation data: {e}') from e
    response = json.loads(body)
    if response.get('error'):
        raise RuntimeError(
            f"Could not fetch Zotero items: {response['error'].get('message')}")
    result = response.get('result') or {}
    errors = result.get('errors') or {}
    for key, code in errors.items():
        print(f"@{key}: {'not found' if code == 0 else 'duplicates found'} in Zotero")
    items = result.get('items') or {}
    csl_items = []
    for key, item in items.items():
        clean = dict(item)
        clean.pop('custom', None)
        csl_items.append(clean)
    return csl_items


_DEFAULT_STATIC_CSL_STYLE = 'chicago-author-date'


def _read_template_csl_style(template_path, fmt):
    """Look for a real Zotero "Document Preferences" payload already stored
    in the export template file itself — the same ZOTERO_PREF_1/_2/...
    custom document properties Zotero's own Word/LibreOffice integration
    writes when a user runs "Document Preferences" (or inserts a citation)
    on that file directly, chunked across multiple numbered properties when
    the payload is long.

    Different templates legitimately target different journals/publishers
    with different required styles, so the template's own stored preference
    (when present) takes priority over any single global default — that's
    the whole point of checking here rather than using one fixed style.

    Returns a short CSL style id (e.g. "chicago-author-date") or None if the
    template has never had Zotero's own document preferences set on it.
    """
    import zipfile
    try:
        with zipfile.ZipFile(template_path) as z:
            if fmt == 'docx':
                raw = z.read('docProps/custom.xml').decode('utf-8', 'ignore')
                parts = re.findall(
                    r'name="ZOTERO_PREF_\d+"[^>]*><vt:lpwstr>(.*?)</vt:lpwstr>',
                    raw, re.S)
            else:
                raw = z.read('meta.xml').decode('utf-8', 'ignore')
                parts = re.findall(
                    r'meta:name="ZOTERO_PREF_\d+"[^>]*>(.*?)</meta:user-defined>',
                    raw, re.S)
    except (KeyError, FileNotFoundError, OSError, zipfile.BadZipFile):
        return None
    if not parts:
        return None
    import html
    payload = html.unescape(''.join(parts))
    m = re.search(r'<style\s+id="([^"]+)"', payload)
    if not m:
        return None
    return m.group(1).rstrip('/').rsplit('/', 1)[-1]


def _note_frontmatter_csl(text):
    """The note's own top-level `csl:` / `citation-style:` YAML property
    (a bare style name, a path, or a URL), or None. This is the same key the
    live Obsidian renderer honours; here it feeds export style selection for
    direct CLI use (the plugin resolves it itself and passes --csl-style)."""
    yaml_block, _ = extract_yaml(text)
    if not yaml_block:
        return None
    for key in ('csl', 'citation-style'):
        val = read_yaml_prop(yaml_block, key)
        if val:
            return val.strip()
    return None


def _csl_short_name(value):
    """Reduce a style name / path / URL / style-id to its short id
    ('.../styles/chicago-author-date' or '/x/apa.csl' -> 'chicago-author-date' /
    'apa'). A bare name passes through unchanged."""
    if not value:
        return value
    v = value.strip().rstrip('/')
    v = re.sub(r'\.csl$', '', v, flags=re.IGNORECASE)
    return v.rsplit('/', 1)[-1]


def _resolve_export_csl_style(text, template_path, fmt, override=None,
                              from_template=False):
    """Decide which CSL style an export should use.

    Returns (short_style_name, is_explicit_override). `is_explicit_override`
    is True when the style came from a deliberate choice (the export dialog's
    override checkbox, or the note's own `csl:` property) rather than a
    template/global default — only then is the style written into the output
    file's Zotero document preferences.

    Priority:
      1. `override`            (dialog: "apply the selected style")
      2. `from_template`       (dialog: checkbox off) -> template pref / global
                               default, skipping the note's csl: property
      3. note `csl:` property
      4. template ZOTERO_PREF
      5. SW_DEFAULT_CSL        (the plugin's configured live-render style)
      6. _DEFAULT_STATIC_CSL_STYLE ('chicago-author-date')
    """
    tpl_style = (_read_template_csl_style(template_path, fmt)
                 if template_path and os.path.exists(template_path) else None)
    env_default = _csl_short_name(os.environ.get('SW_DEFAULT_CSL', '').strip()) \
        or None

    if override:
        return _csl_short_name(override), True
    if from_template:
        return (tpl_style or env_default or _DEFAULT_STATIC_CSL_STYLE), False
    fm = _note_frontmatter_csl(text)
    if fm:
        return _csl_short_name(fm), True
    if tpl_style:
        return tpl_style, False
    return (env_default or _DEFAULT_STATIC_CSL_STYLE), False


#: Pandoc's citeproc bundles this style as its built-in default. When the
#: resolved style IS this one, skip the network fetch entirely and let pandoc
#: use its own copy (emit no --csl argument).
_PANDOC_DEFAULT_CSL_STYLE = 'chicago-author-date'


def _https_get(url, timeout):
    """GET *url*, tolerating the python.org macOS build's un-wired CA store
    (a very common setup: the installer ships certs but leaves them inactive
    until "Install Certificates.command" is run). Tries the normal verified
    context, then certifi's bundle if importable, then falls back to an
    unverified context with a warning — the payload here is a public, static,
    non-executed file that we cache and hand to pandoc, so an unverified TLS
    fetch is an acceptable last resort. Non-certificate errors (offline, 404)
    are not retried."""
    import ssl
    import urllib.request
    import urllib.error

    attempts = [None]  # verified default context
    try:
        import certifi
        attempts.append(ssl.create_default_context(cafile=certifi.where()))
    except ImportError:
        pass
    unverified = ssl._create_unverified_context()
    attempts.append(unverified)

    last_err = None
    for ctx in attempts:
        try:
            with urllib.request.urlopen(url, timeout=timeout, context=ctx) as resp:
                data = resp.read()
            if ctx is unverified:
                print('WARNING: fetched %s over an UNVERIFIED HTTPS connection — '
                      'your Python has no active CA certificates. Run the '
                      '"Install Certificates.command" that ships with python.org '
                      'Python, or `pip install certifi`.' % url)
            return data
        except urllib.error.URLError as e:
            last_err = e
            if not isinstance(getattr(e, 'reason', None),
                              ssl.SSLCertVerificationError):
                break  # offline / DNS / HTTP error — retrying won't help
    raise last_err


def _zotero_style_dirs(client=None):
    """Candidate `<data-dir>/styles` folders of an installed Zotero / Jurism,
    most-preferred first. Honours a custom data directory set in prefs.js
    (`extensions.zotero.dataDir`); otherwise the default `~/Zotero` (`~/Jurism`).
    `client` ('zotero' | 'jurism', from the note's zotero: block) just reorders
    the two — both are always checked."""
    dirs, seen = [], set()
    env_dir = os.environ.get('SW_ZOTERO_DIR', '').strip()
    if env_dir:
        p = Path(env_dir).expanduser()
        # Accept either the data dir or the styles dir itself.
        for styles in ((p if p.name == 'styles' else p / 'styles'), p / 'styles'):
            if styles not in seen:
                seen.add(styles)
                dirs.append(styles)
    names = ['Jurism', 'Zotero'] if client == 'jurism' else ['Zotero', 'Jurism']
    for name in names:
        base = Path.home() / name
        candidates = []
        try:
            prefs = (base / 'prefs.js').read_text(encoding='utf-8', errors='ignore')
            m = re.search(
                r'user_pref\("extensions\.zotero\.dataDir",\s*"((?:[^"\\]|\\.)*)"\)',
                prefs)
            if m:
                candidates.append(Path(m.group(1).encode()
                                       .decode('unicode_escape')))
        except OSError:
            pass
        candidates.append(base)
        for d in candidates:
            styles = d / 'styles'
            if styles not in seen:
                seen.add(styles)
                dirs.append(styles)
    return dirs


def _installed_csl_path(csl_style, client=None):
    """Path to `csl_style` in an installed Zotero/Jurism styles folder, or None.
    This copy is the user's canonical, Zotero-managed version — prefer it over
    a download or pandoc's bundled default."""
    for d in _zotero_style_dirs(client):
        p = d / f'{csl_style}.csl'
        if p.is_file():
            return str(p)
    return None


def _fetch_csl_style_file(csl_style, client=None):
    """Return a local path to the given CSL style's XML, or None when no file
    is needed (pandoc's built-in default). Resolution order:

      1. An installed Zotero/Jurism style (`~/Zotero/styles/<style>.csl`, or a
         custom data dir) — the user's own, up-to-date, Zotero-managed copy.
      2. Pandoc's built-in default — return None, pandoc uses its own copy.
      3. This session's on-disk cache from a previous download.
      4. A one-time download from citation-style-language/styles, then cached.

    No freshness check: CSL styles change rarely, so a cached copy is fine;
    a newer one is picked up whenever the cache is next cleared or Zotero
    updates its own folder.
    """
    local = _installed_csl_path(csl_style, client)
    if local:
        return local
    if csl_style == _PANDOC_DEFAULT_CSL_STYLE:
        return None
    import tempfile
    import urllib.error
    cache_dir = Path(tempfile.gettempdir()) / 'scholarweave-csl-cache'
    cache_path = cache_dir / f'{csl_style}.csl'
    if not cache_path.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        url = (
            'https://raw.githubusercontent.com/citation-style-language/'
            f'styles/master/{csl_style}.csl'
        )
        try:
            cache_path.write_bytes(_https_get(url, timeout=20))
        except (OSError, urllib.error.URLError) as e:
            raise RuntimeError(
                f'Could not find CSL style "{csl_style}" in the Zotero styles '
                f'folder or download it from citation-style-language/styles: '
                f'{e}') from e
    return str(cache_path)


def _csl_is_note_style(csl_path):
    """True when the CSL file at `csl_path` is a note/footnote style
    (`<category citation-format="note"/>`). None / unreadable / a dependent
    style with no own category → False (safe default: no restructuring)."""
    if not csl_path:
        return False
    try:
        with open(csl_path, 'r', encoding='utf-8', errors='ignore') as fh:
            head = fh.read(8000)
    except OSError:
        return False
    return bool(re.search(r'<category\s+citation-format="note"', head))


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

    # abstract and note:/sw-* extra sections are extracted in ONE
    # position-ordered pass (re.finditer yields matches in source order) so
    # extra_sections preserves the note's own YAML order — a 'note:'
    # property between two sw-* properties stays between them, rather than
    # abstract always being forced first regardless of where it actually
    # appears. abstract is also kept as its own field below (unchanged
    # value, just no longer separately-positioned) since some callers want
    # it standalone.
    abstract = None
    extra_sections = []
    for m in re.finditer(
            r'^(abstract|note|sw-[\w-]+):[^\S\n]*(?:["\']([^"\']+)["\']|(\S[^\n]*?))(?:\s*#[^\n]*)?\s*$',
            text, re.M):
        key = m.group(1)
        raw = (m.group(2) or m.group(3) or '').strip()
        val = _yaml_block(text, m.end(), raw) if re.match(r'^[|>]', raw) else raw
        if not val:
            continue
        if key == 'abstract':
            abstract = val
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


def _resolve_output_stem(output_name, default_stem):
    """Filename stem for an export. `output_name` is the export dialog's
    'Desired output filename' (may carry an extension, which is dropped here —
    the caller re-adds the format's own). Blank/None → the source note's stem."""
    if not output_name:
        return default_stem
    stem = Path(str(output_name)).name
    stem = re.sub(r'\.(docx|odt|pdf|md)$', '', stem, flags=re.IGNORECASE)
    return stem.strip() or default_stem


def export_document(fmt, compiled_md, vault_root=None, template=None, toc=False,
                    tof=False, template_dir=None, output_dir=None,
                    default_author=None, new_page_headings=True,
                    restart_footnotes=True, mappings_data=None, generate_date=True,
                    roman_frontmatter=False, page1_starts_with='',
                    static_citations=False, csl_style_override=None,
                    csl_from_template=False, output_name=None):
    """Unified export pipeline for DOCX and ODT.

    static_citations: when True, skip sw-zotero.lua's live-Zotero-field
    generation and let pandoc's own --citeproc render final citation text
    and a real bibliography instead, using a CSL-JSON bibliography fetched
    from Zotero/Better BibTeX up front. Used by export_pdf(), since a PDF
    has no live document to refresh fields in later; DOCX/ODT exports keep
    the default (live fields, refreshable in Word/LibreOffice).

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
    # A caller-supplied name (export dialog) is written to DIRECTLY — never
    # generate the default-named file first (that would clobber a real export
    # the user keeps under the note's own name).
    out_stem   = _resolve_output_stem(output_name, compiled_md.stem)
    out_path   = out_dir / f"{out_stem}{ext}"

    # ── Citation conversion (identical for both formats) ───────────────────────
    citations_md = compiled_md.with_suffix('.citations.md')
    conv_script  = plugin_script_path('convert-citations.mjs')
    node_bin     = os.environ.get('SW_NODE', 'node')
    subprocess.run([node_bin, conv_script, str(compiled_md), str(citations_md)],
                   check=True)

    # ── Markdown pre-processing (identical for both formats) ───────────────────
    cit_text = citations_md.read_text(encoding='utf-8')
    cit_text = ensure_blank_before_headings(cit_text)
    cit_text = rewrite_poetry_callouts(cit_text)
    cit_text = preprocess_md_syntax(cit_text)
    cit_text = resolve_embed_links(cit_text)  # fixes images in direct-note exports
    cit_text = strip_wikilinks(cit_text)       # strips vault-internal wikilinks
    cit_text = linkify_bare_urls(cit_text)     # converts bare URLs to markdown links
    citations_md.write_text(cit_text, encoding='utf-8')

    # ── Lua filter construction (identical for both formats) ───────────────────
    filters = [
        plugin_script_path('sw-doc-title.lua'),
        plugin_script_path('sw-export.lua'),
        plugin_script_path('sw-poetry.lua'),
        plugin_script_path('sw-bidi.lua'),
    ]
    if not static_citations:
        filters.append(plugin_script_path('sw-zotero.lua'))
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

    # ── Template path lookup (identical candidate chain for every format) ──────
    template_path = _resolve_template_path(tpl, ext, template_dir)

    # ── CSL style resolution (see _resolve_export_csl_style for priority).
    # `_csl_is_override` marks a deliberate choice (dialog checkbox or the
    # note's own csl: property) — only then do we write the style into the
    # output file's Zotero document preferences (merge --csl-style).
    zmeta = _parse_zotero_meta(text)
    csl_style, _csl_is_override = _resolve_export_csl_style(
        text, template_path, fmt,
        override=csl_style_override, from_template=csl_from_template)

    # ── Note/footnote citation style, live-field path: move each in-text
    # citation into a real footnote before pandoc runs (see
    # citations_to_footnotes). The static path skips this — pandoc's own
    # --citeproc already produces footnotes for a note style.
    if not static_citations:
        try:
            _csl_path_for_notes = _fetch_csl_style_file(csl_style, zmeta['client'])
        except (RuntimeError, OSError):
            _csl_path_for_notes = None
        if _csl_is_note_style(_csl_path_for_notes):
            cit_text = citations_to_footnotes(cit_text)
            citations_md.write_text(cit_text, encoding='utf-8')
            print('Note citation style — moved in-text citations into footnotes')

    # ── Static-citation mode (PDF path): fetch a CSL-JSON bibliography and
    # the CSL style file up front, and hand citation processing to pandoc's
    # own --citeproc instead of sw-zotero.lua's live-field generation.
    citeproc_args = []
    _tmp_biblio_dir = None
    if static_citations:
        citekeys = _extract_citekeys(cit_text)
        csl_items = _fetch_zotero_csl_items(
            citekeys, csl_style, zmeta['client'], zmeta['library'])
        csl_path = _fetch_csl_style_file(csl_style, zmeta['client'])
        import tempfile as _tempfile
        _tmp_biblio_dir = Path(_tempfile.mkdtemp())
        biblio_path = _tmp_biblio_dir / 'bibliography.json'
        biblio_path.write_text(json.dumps(csl_items, ensure_ascii=False), encoding='utf-8')
        citeproc_args = [
            '--citeproc',
            '--bibliography', str(biblio_path),
            '--metadata', 'reference-section-title=Bibliography',
        ]
        # csl_path is None when the style is pandoc's built-in default —
        # omit --csl and let pandoc use its own bundled copy.
        if csl_path:
            citeproc_args += ['--csl', csl_path]

    # ── pandoc invocation (format-specific) ────────────────────────────────────
    if fmt == 'docx':
        # DOCX: clean output with no reference-doc; merge script applies template.
        cmd = [os.environ.get('SW_PANDOC', 'pandoc'), str(citations_md),
               '-t', 'docx',
               '-f', 'markdown+wikilinks_title_after_pipe+lists_without_preceding_blankline',
               *filter_args,
               *citeproc_args,
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
               *citeproc_args,
               *ref_doc_args,
               '-o', str(clean_path)]
        print('Running pandoc:', ' '.join(cmd))
        subprocess.run(cmd, check=True)
        if _tmp_ref_dir:
            import shutil as _sh2
            _sh2.rmtree(_tmp_ref_dir, ignore_errors=True)

    if _tmp_biblio_dir:
        import shutil as _sh3
        _sh3.rmtree(_tmp_biblio_dir, ignore_errors=True)

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
    if tof:
        merge_cmd.append('--list-of-figures')
    if not generate_date:
        merge_cmd.append('--no-generated-date')
    if roman_frontmatter:
        merge_cmd.append('--roman-frontmatter')
        if page1_starts_with:
            merge_cmd += ['--page1-starts-with', page1_starts_with]
    if static_citations:
        merge_cmd.append('--static-citations')
    if _csl_is_override and csl_style:
        # Write the chosen style into the output's Zotero document
        # preferences so a later Word/LibreOffice "Refresh" uses it without
        # prompting. Pass the resolved .csl path when we have one (its <id>
        # is authoritative); otherwise the bare short name.
        _csl_ref = None
        try:
            _csl_ref = _installed_csl_path(csl_style, zmeta['client'])
        except Exception:
            _csl_ref = None
        merge_cmd += ['--csl-style', _csl_ref or csl_style]
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
                tof=False, template_dir=None, output_dir=None, default_author=None,
                new_page_headings=True, restart_footnotes=True,
                mappings_data=None, generate_date=True,
                roman_frontmatter=False, page1_starts_with='',
                static_citations=False, csl_style_override=None,
                csl_from_template=False, output_name=None):
    """Export compiled markdown to DOCX. Thin wrapper around export_document."""
    return export_document('docx', compiled_md,
                           vault_root=vault_root, template=template, toc=toc,
                           tof=tof,
                           template_dir=template_dir, output_dir=output_dir,
                           default_author=default_author,
                           new_page_headings=new_page_headings,
                           restart_footnotes=restart_footnotes,
                           mappings_data=mappings_data, generate_date=generate_date,
                           roman_frontmatter=roman_frontmatter,
                           page1_starts_with=page1_starts_with,
                           static_citations=static_citations,
                           csl_style_override=csl_style_override,
                           csl_from_template=csl_from_template,
                           output_name=output_name)


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
               tof=False, template_dir=None, output_dir=None, default_author=None,
               new_page_headings=True, restart_footnotes=True,
               mappings_data=None, generate_date=True,
               roman_frontmatter=False, page1_starts_with='',
               static_citations=False, csl_style_override=None,
               csl_from_template=False, output_name=None):
    """Export compiled markdown to ODT. Thin wrapper around export_document."""
    return export_document('odt', compiled_md,
                           vault_root=vault_root, template=template, toc=toc,
                           tof=tof,
                           template_dir=template_dir, output_dir=output_dir,
                           default_author=default_author,
                           new_page_headings=new_page_headings,
                           restart_footnotes=restart_footnotes,
                           mappings_data=mappings_data, generate_date=generate_date,
                           roman_frontmatter=roman_frontmatter,
                           page1_starts_with=page1_starts_with,
                           static_citations=static_citations,
                           csl_style_override=csl_style_override,
                           csl_from_template=csl_from_template,
                           output_name=output_name)


def _latex_notes_parts(abstract, extra_sections):
    """Abstract (if present) followed by each note:/sw-* extra section, as a
    flat list of markdown/raw-LaTeX string fragments ready to join into the
    title page/block. This is the SAME format-agnostic ordering/labeling
    logic DOCX and ODT use for their own title blocks — resolve_note_sections
    (abstract-first ordering) and append_extra_sections (title_case labels +
    split_paragraphs body chunks) both live in sw_merge_helpers.py and are
    shared unchanged; only the make_heading/make_body callbacks below are
    LaTeX-specific (DOCX/ODT supply their own, building XML elements instead
    of markdown strings).

    Every fragment (heading or paragraph) carries a LEADING \\medskip: pandoc's
    default LaTeX template loads the `parskip` package unconditionally, which
    removes first-line paragraph indentation in favor of vertical space
    between paragraphs — but that vertical space is barely visible at the
    small font size used on the title page, reading as one run-on paragraph
    even when it's several. \\medskip gives a clearly visible gap before
    every heading and every paragraph, regardless of font size/context.
    """
    parts = []
    append_extra_sections(
        parts, resolve_note_sections(abstract, extra_sections),
        make_heading=lambda label: f'```{{=latex}}\n\\medskip\n```\n**{label}**\n\n',
        make_body=lambda chunk: f'```{{=latex}}\n\\medskip\n```\n{chunk}\n\n',
    )
    return parts


def _latex_titlepage_block(title, subtitle, author, date_val, abstract, extra_sections):
    """A standalone title page, as markdown+raw-LaTeX body content: raw
    \\begin{titlepage}/size commands wrapping ordinary markdown paragraphs
    (title/subtitle/author/date/abstract each still go through pandoc's
    normal markdown->LaTeX rendering, so e.g. *italics* in a title works).

    Needed because pandoc's own \\maketitle only lands on its own page
    automatically for the book/report classes — not article — so this same
    construction is used for all three doc types instead of relying on
    per-class native behaviour. Same information as the DOCX/ODT title
    block's Title/Subtitle/Author/Date/Abstract + note:/sw-* slots.

    \\swTitleLogo is a template-defined hook (empty by default — see
    book.tex) a user can redefine to \\includegraphics a logo above the
    title, without touching this function.
    """
    parts = ['```{=latex}\n\\begin{titlepage}\n\\thispagestyle{empty}\n'
             '\\centering\n\\swTitleLogo\n\\vspace*{3cm}\n\\LARGE\n```\n']
    parts.append(f'{title}\n\n' if title else '\n')
    if subtitle:
        parts.append('```{=latex}\n\\large\n```\n')
        parts.append(f'{subtitle}\n\n')
    parts.append('```{=latex}\n\\vspace{2cm}\n\\normalsize\n```\n')
    if author:
        parts.append(f'{author}\n\n')
    if date_val:
        parts.append(f'{date_val}\n\n')
    notes_parts = _latex_notes_parts(abstract, extra_sections)
    if notes_parts:
        parts.append('```{=latex}\n\\vspace{1.5cm}\n'
                     '\\begin{minipage}{0.8\\textwidth}\\small\n```\n')
        parts.extend(notes_parts)
        parts.append('```{=latex}\n\\end{minipage}\n```\n')
    parts.append('```{=latex}\n\\end{titlepage}\n```\n\n')
    return ''.join(parts)


def _latex_title_block(title, subtitle, author, date_val, abstract, extra_sections):
    """A title BLOCK (not a page), as markdown+raw-LaTeX body content:
    title/subtitle/author/date centered at the top of page 1, immediately
    followed by the abstract/note:/sw-* sections (if any) and body content
    on the SAME page — no page break. Matches document.docx/document.odt
    and article.docx/article.odt, whose title/subtitle/author/date are
    ordinary paragraphs, not a dedicated cover page (only book.docx/book.odt
    gets a real cover page — see _latex_titlepage_block above).

    \\swTitleLogo is a template-defined hook (empty by default — see
    document.tex) a user can redefine to \\includegraphics a logo above the
    title, without touching this function.
    """
    parts = ['```{=latex}\n\\thispagestyle{plain}\n\\begin{center}\n'
             '\\swTitleLogo\n{\\huge\\bfseries\n```\n']
    parts.append(f'{title}\n\n' if title else '\n')
    parts.append('```{=latex}\n}\n```\n')
    if subtitle:
        parts.append('```{=latex}\n{\\Large\n```\n')
        parts.append(f'{subtitle}\n\n')
        parts.append('```{=latex}\n}\n```\n')
    if author:
        parts.append(f'{author}\n\n')
    if date_val:
        parts.append(f'{date_val}\n\n')
    parts.append('```{=latex}\n\\end{center}\n\\vspace{1em}\n```\n\n')
    notes_parts = _latex_notes_parts(abstract, extra_sections)
    if notes_parts:
        parts.append('```{=latex}\n\\begin{quotation}\\noindent\n```\n')
        parts.extend(notes_parts)
        parts.append('```{=latex}\n\\end{quotation}\n```\n')
    return ''.join(parts)


def export_latex(compiled_md, vault_root=None, template=None, toc=False,
                 tof=False, template_dir=None, output_dir=None,
                 default_author=None, new_page_headings=True,
                 restart_footnotes=True, mappings_data=None, generate_date=True,
                 roman_frontmatter=False, page1_starts_with='',
                 csl_style_override=None, csl_from_template=False,
                 output_name=None, as_pdf=False):
    """Export compiled markdown to LaTeX (.tex), or — when as_pdf — straight
    to PDF via pandoc's own --pdf-engine=lualatex. No LibreOffice, no
    intermediate file: pandoc goes from markdown to PDF in one call.

    Unlike export_document (DOCX/ODT), there is no merge step: LaTeX's own
    engine computes TOC/figure/footnote numbering and pagination natively
    (a book-class \\chapter always starts a new page and always resets to the
    `plain` page style for its own first page — the "no header on a chapter
    opener" rule falls out of that for free), so none of sw_merge_helpers'
    DOCX/ODT XML machinery applies here. Citations are always static
    (pandoc's own --citeproc) — LaTeX has no live-field concept, matching the
    PDF-via-DOCX/ODT path. Custom style mappings (arbitrary callout -> named
    style) have no LaTeX equivalent and are not applied here.

    tof (Table of Figures) and new_page_headings (page break before each
    top-level heading) both apply here too — via \\listoffigures and a
    template-side \\newpage token respectively — matching DOCX/ODT, though
    book's top-level heading (\\chapter) always starts a new page regardless
    of new_page_headings, same as DOCX/ODT's own book template.
    restart_footnotes drives section-scoped FIGURE numbering for
    document/article via a template-side token (article-class has no native
    equivalent to fall back on). Footnotes are deliberately NOT tied to this
    flag: book.cls already resets them to plain numbers at every \\chapter,
    and article-class footnotes are naturally continuous — both exactly the
    desired behaviour with zero custom code, so reimplementing either would
    just be redundant (DOCX/ODT need their own restart logic because
    Word/LibreOffice have no native equivalent at all; LaTeX does).
    """
    import subprocess
    import shutil as _sh
    import tempfile as _tempfile
    compiled_md  = Path(compiled_md)
    vault_root   = vault_root or vault_rel()
    template_dir = resolve_template_dir(template_dir, vault_root)

    text = compiled_md.read_text(encoding='utf-8')
    meta = _parse_yaml_metadata(text, compiled_md.stem)

    tpl = template if template is not None else meta['tpl']
    tpl = re.sub(r'\.(docx|odt|tex)$', '', tpl, flags=re.IGNORECASE)
    if tpl.startswith('compile-'):
        tpl = tpl[len('compile-'):]
    is_book = tpl.startswith('book')

    doc_title, doc_subtitle, doc_author, doc_date = resolve_cover(
        meta['title'], meta['subtitle'], meta['author'] or default_author,
        meta['date_val'], compiled_md.stem, generate_date=generate_date)
    doc_abstract   = meta['abstract']
    extra_sections = meta['extra_sections']
    short_title    = meta['short_title'] or doc_title

    out_dir = Path(output_dir).expanduser() if output_dir else compiled_md.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out_ext  = '.pdf' if as_pdf else '.tex'
    out_stem = _resolve_output_stem(output_name, compiled_md.stem)
    out_path = out_dir / f"{out_stem}{out_ext}"

    template_path = _resolve_template_path(tpl, '.tex', template_dir)

    # ── Citation conversion + markdown pre-processing (shared with DOCX/ODT) ──
    citations_md = compiled_md.with_suffix('.citations.md')
    conv_script  = plugin_script_path('convert-citations.mjs')
    node_bin     = os.environ.get('SW_NODE', 'node')
    subprocess.run([node_bin, conv_script, str(compiled_md), str(citations_md)],
                   check=True)

    cit_text = citations_md.read_text(encoding='utf-8')
    cit_text = ensure_blank_before_headings(cit_text)
    cit_text = rewrite_poetry_callouts(cit_text)
    cit_text = preprocess_md_syntax(cit_text)
    cit_text = resolve_embed_links(cit_text)
    cit_text, has_figures = latex_process_images(cit_text)
    cit_text = strip_wikilinks(cit_text)
    cit_text = linkify_bare_urls(cit_text)

    # Strip the note's own YAML frontmatter — pandoc reads title/author/date
    # from IT directly (independent of any --metadata CLI flag, and even an
    # empty --metadata override doesn't unset a $if(title)$ check), which
    # would trigger pandoc's own \maketitle. Everything in it we still need
    # (title/subtitle/author/date/abstract) is already in the Python
    # variables above (via _parse_yaml_metadata + resolve_cover).
    # Must happen BEFORE the book frontmatter injection below: extract_yaml
    # only matches a "---" block at the very start of the text, and the
    # \frontmatter raw block would otherwise get prepended first, pushing the
    # YAML out of position-0 and silently defeating the strip (only visible
    # for book, since article/document never prepend anything here).
    _, cit_text = extract_yaml(cit_text)

    # ── CSL style resolution (same priority chain as the static/PDF path) ──────
    zmeta = _parse_zotero_meta(text)
    csl_style, _csl_is_override = _resolve_export_csl_style(
        text, template_path, 'latex',
        override=csl_style_override, from_template=csl_from_template)
    citekeys  = _extract_citekeys(cit_text)
    csl_items = _fetch_zotero_csl_items(citekeys, csl_style, zmeta['client'], zmeta['library'])
    csl_path  = _fetch_csl_style_file(csl_style, zmeta['client'])

    # A note's own pre-existing 'Bibliography' heading (e.g. hand-typed
    # references predating ScholarWeave's citation system) duplicates
    # pandoc's own --citeproc-rendered one once real bibliography entries are
    # actually resolved (DocumentCompiler.py passes --metadata
    # reference-section-title=Bibliography below) — strip it from the source
    # before pandoc ever sees it, mirroring DOCX/ODT's own
    # duplicate-bibliography handling (sw_export_merge.py /
    # sw_export_odt_merge.py, via the shared strip_duplicate_bibliographies).
    # Gated on csl_items (the actually-fetched entries), not just citekeys:
    # citekeys is a raw text scan for "@key" patterns and stays non-empty even
    # when Zotero isn't running / a key doesn't resolve, in which case pandoc
    # renders no bibliography of its own — the note's hand-typed one is the
    # only content there is, and stripping it would silently delete it.
    if csl_items:
        cit_text = strip_bibliography_heading_from_markdown(cit_text)

    # ── Book only: roman-numeral frontmatter -> \mainmatter, inserted at the
    # reset heading DOCX/ODT use too (find_page_reset_index, shared) — a raw
    # LaTeX block in the compiled markdown, since it must sit in the BODY,
    # not the preamble (it fires mid-document, at whichever heading resets
    # to arabic numbering).
    if is_book and roman_frontmatter:
        h1_positions = [m.start() for m in re.finditer(r'^# .+$', cit_text, re.M)]
        h1_texts = [cit_text[p:cit_text.find('\n', p)].lstrip('# ').strip()
                   for p in h1_positions]
        reset_idx = find_page_reset_index(h1_texts, page1_starts_with)
        if reset_idx is not None:
            cut = h1_positions[reset_idx]
            cit_text = (cit_text[:cut]
                       + '```{=latex}\n\\mainmatter\n```\n\n'
                       + cit_text[cut:])

    # ── Front matter, in reading order: title page/block first, then (book
    # only, roman_frontmatter) \frontmatter switching to roman page numbers,
    # then the table of contents. All built as body content (raw LaTeX
    # wrapping ordinary markdown paragraphs, so title/subtitle/etc. still go
    # through pandoc's normal markdown->LaTeX rendering) and placed manually
    # here rather than via pandoc's own --toc, which would render the TOC
    # BEFORE $body$ entirely — i.e. before the title page/block itself,
    # since pandoc's template always emits TOC ahead of body content.
    # Manual placement also means the TOC sits under the same \frontmatter
    # roman-numbering as the title page, with no separate
    # --include-before-body plumbing needed.
    #
    # "document"/"article" get a title block flowing directly into the body
    # (matching their DOCX/ODT analogs, which have no section break there);
    # "book" gets a dedicated title page (matching book.docx/.odt's own
    # section-break-separated cover). Either way we deliberately do NOT pass
    # title/subtitle/author/date as pandoc metadata — that would trigger a
    # redundant \maketitle, which only gets its own page automatically for
    # book/report anyway, not article.
    title_block_fn = _latex_titlepage_block if is_book else _latex_title_block
    front_matter = title_block_fn(
        doc_title, doc_subtitle, doc_author, doc_date, doc_abstract, extra_sections)
    if is_book and roman_frontmatter:
        front_matter += '```{=latex}\n\\frontmatter\n```\n\n'
    if toc:
        front_matter += ('```{=latex}\n{\\setcounter{tocdepth}{3}\n'
                         '\\tableofcontents\n}\n```\n\n')
    # Matches DOCX/ODT: only render a Table of Figures when the "Include
    # table of figures" checkbox is on AND the document actually contains at
    # least one captioned figure — otherwise \listoffigures would print an
    # empty, heading-only section.
    if tof and has_figures:
        front_matter += '```{=latex}\n\\listoffigures\n```\n\n'
    cit_text = front_matter + cit_text

    citations_md.write_text(cit_text, encoding='utf-8')

    # ── Lua filters: Notes-section suppression, poetry -> verse. No
    # sw-doc-title.lua — its "fall back to source-note" title logic would set
    # meta.title from --metadata source-note=..., re-triggering pandoc's own
    # \maketitle (which we deliberately avoid — see the title-page block
    # below); resolve_cover already gives the same basename fallback. No
    # sw-zotero.lua (always static) and no style-mappings filter (no LaTeX
    # equivalent for a named custom style).
    filters = [
        plugin_script_path('sw-export.lua'),
        plugin_script_path('sw-poetry.lua'),
        plugin_script_path('sw-bidi.lua'),
    ]
    filters += find_user_lua_filters(template_dir)
    filter_args = []
    for f in filters:
        filter_args += ['--lua-filter', f]

    tmp_dir = Path(_tempfile.mkdtemp())
    try:
        biblio_path = tmp_dir / 'bibliography.json'
        biblio_path.write_text(json.dumps(csl_items, ensure_ascii=False), encoding='utf-8')

        # ── Preamble include: the resolved .tex template, with its
        # SWTOKAUTHOR / SWTOKSHORTTITLE placeholders substituted for the real
        # (LaTeX-escaped) values — NOT pandoc $variable$ syntax, which raw
        # --include-in-header content would have LaTeX read as inline math.
        preamble_src = (Path(template_path).read_text(encoding='utf-8')
                        if os.path.exists(template_path) else '')

        # Extra \documentclass options, read from a marker COMMENT line in
        # the template (e.g. "% SWEXTRACLASSOPTIONS: twocolumn") — a plain
        # --include-in-header preamble can't change \documentclass's own
        # options (twoside is fixed, needed by the running-header
        # convention), since that line has already executed by the time
        # this file's content runs. This is the one documentclass-level knob
        # exposed to the template anyway, for things like twocolumn/12pt
        # that only take effect as class options, not as later \usepackage
        # settings.
        extra_classoptions = []
        m = re.search(r'^%[ \t]*SWEXTRACLASSOPTIONS:[ \t]*(.*)$', preamble_src, re.M)
        if m:
            extra_classoptions = [o.strip() for o in m.group(1).split(',') if o.strip()]

        # "Restart footnote and figure numbering per chapter" (restart_footnotes)
        # only affects FIGURE numbering here, and only for document/article —
        # footnotes are deliberately left to each class's own native behaviour
        # instead of being reimplemented: book.cls already resets footnotes to
        # plain "1, 2, 3..." at every \chapter and gives figures a "chapter.N"
        # \thefigure, both unconditionally and with zero custom code (confirmed
        # empirically — a bare \documentclass{book} already does both); article-
        # class footnotes are naturally continuous, which IS the desired
        # "non-book top-level sections don't restart" behaviour, so there's
        # nothing to add there either. The one gap LaTeX doesn't fill natively:
        # article-class has no per-section figure numbering, so
        # SWTOKFIGURECOUNTER supplies \counterwithin{figure}{section} for
        # document/article when the checkbox is on (see document.tex's own
        # list of tokens; book.tex doesn't have this token at all).
        #
        # "Top-level headings start on a new page" (new_page_headings) has no
        # native equivalent for document/article either, hence
        # SWTOKNEWPAGEHEADING there (book's \chapter always starts a new page
        # regardless, so book.tex has no such token).
        #
        # Single-line substitution values, deliberately: both tokens are also
        # named (as literal text) in each template's own top-of-file
        # documentation comment, so the blind string-replace below touches
        # that mention too — a value with an embedded newline would leave
        # everything after the break un-commented (missing its own leading
        # "%"), which is exactly what "! LaTeX Error: Missing \begin{document}"
        # turned out to mean the first time this was tried with a multi-line
        # value. A single-line value can never split a comment line in two,
        # whatever line it lands on.
        figure_counter_latex = (
            '\\counterwithin{figure}{section}' if restart_footnotes else ''
        )
        newpage_latex = '\\newpage' if new_page_headings else ''

        preamble_src = (preamble_src
                        .replace('SWTOKAUTHOR', _latex_escape(doc_author or ''))
                        .replace('SWTOKSHORTTITLE', _latex_escape(short_title or ''))
                        .replace('SWTOKTITLE', _latex_escape(doc_title or ''))
                        .replace('SWTOKFIGURECOUNTER', figure_counter_latex)
                        .replace('SWTOKNEWPAGEHEADING', newpage_latex))
        preamble_path = tmp_dir / 'preamble.tex'
        preamble_path.write_text(preamble_src, encoding='utf-8')

        engine = find_latex_engine()
        if as_pdf and not engine:
            raise RuntimeError(
                'PDF export via LaTeX needs lualatex installed (part of any '
                'TeX distribution: MacTeX/BasicTeX on macOS, MiKTeX on '
                'Windows, TeX Live on Linux). Install one, or export to '
                'DOCX/ODT and convert to PDF via LibreOffice instead.')

        cmd = [os.environ.get('SW_PANDOC', 'pandoc'), str(citations_md),
               '-t', 'latex',
               '-f', 'markdown+wikilinks_title_after_pipe'
                     '+lists_without_preceding_blankline+raw_attribute',
               *filter_args,
               '--citeproc', '--bibliography', str(biblio_path),
               '--metadata', 'reference-section-title=Bibliography',
               '--include-in-header', str(preamble_path),
               '--metadata', f'documentclass={"book" if is_book else "article"}',
               '--metadata', 'classoption=twoside',
               # numbersections=true keeps pandoc from setting secnumdepth to
               # -\maxdimen (its default when this is unset) — headings still
               # show NO visible number (titlesec's \titleformat calls below
               # use an empty label) and the TOC still shows no number
               # (titletoc's \titlecontents calls do the same for TOC
               # entries), but critically the underlying \thechapter/
               # \thesection counter now actually increments: at
               # secnumdepth -\maxdimen it never does (confirmed empirically
               # — \thechapter reads 0 after every single \chapter), which
               # silently broke \counterwithin{figure/footnote}{chapter} —
               # every chapter's figures numbered "Figure 0.1", and footnotes
               # never reset at all, regardless of restart_footnotes.
               '--metadata', 'numbersections=true',
               *[a for opt in extra_classoptions
                 for a in ('--metadata', f'classoption={opt}')],
               # Font, page size/margins, and link color are NOT set here —
               # each template sets its own via \setmainfont/geometry/
               # \definecolor{swlinkcolor}(...) directly, so a user can copy
               # a template and edit those values without touching Python.
               # colorlinks/linkcolor/citecolor/urlcolor below just wire
               # pandoc's own hypersetup call (which runs AFTER our
               # --include-in-header content, so it can already see
               # swlinkcolor) to the template-defined color name.
               '--metadata', 'colorlinks=true',
               '--metadata', 'linkcolor=swlinkcolor',
               '--metadata', 'citecolor=swlinkcolor',
               '--metadata', 'urlcolor=swlinkcolor',
               '--metadata', f'source-note={compiled_md.stem}']
        if is_book:
            # Force \chapter for level-1 headings explicitly, rather than
            # relying on pandoc's own book/report-class detection: that
            # detection only fires when documentclass is set as a template
            # VARIABLE (-V) rather than metadata (-M, used above so the value
            # still reaches $documentclass$ in --include-in-header's
            # surrounding template) — and it is bundled together with an
            # auto-inserted \frontmatter/\mainmatter/\backmatter (pandoc's
            # own $if(has-frontmatter)$ blocks) that would duplicate our own
            # precisely-placed \frontmatter/\mainmatter raw blocks above,
            # producing extra blank pages and page-numbering churn. Setting
            # documentclass via -V to only ever get \chapter, without also
            # getting has-frontmatter's side effect, isn't possible — the two
            # are keyed off the same internal check — so --top-level-division
            # is used instead: it selects \chapter independently of that
            # book-class detection, leaving has-frontmatter false. Confirmed
            # via isolated pandoc test: -M documentclass=book alone -> \section;
            # -V documentclass=book -> \chapter + duplicate \frontmatter/etc.;
            # -M documentclass=book --top-level-division=chapter -> \chapter,
            # no duplicate \frontmatter/\mainmatter/\backmatter.
            cmd += ['--top-level-division=chapter']
        # title/subtitle/author/date are deliberately NOT passed as pandoc
        # metadata — that would also trigger pandoc's own \maketitle,
        # duplicating the title page/block we already built into the body
        # above. The PDF's internal pdftitle/pdfauthor metadata is the one
        # thing not replicated for LaTeX output as a result (a cosmetic gap,
        # not a content one) — see document.tex's own note on this.
        if csl_path:
            cmd += ['--csl', csl_path]
        # --toc is deliberately NOT passed: the table of contents is placed
        # manually in the body above (right after the title page/block)
        # instead, so it doesn't render before that content the way pandoc's
        # own --toc always does.
        if as_pdf:
            cmd += ['--pdf-engine', engine, '-o', str(out_path)]
        else:
            cmd += ['-o', str(out_path)]
        print('Running pandoc:', ' '.join(cmd))
        subprocess.run(cmd, check=True)
    finally:
        _sh.rmtree(tmp_dir, ignore_errors=True)
        citations_md.unlink(missing_ok=True)

    print(f'Exported [{tpl}] → {out_path}')
    print(str(out_path))   # last line — parsed by exportCompiler.ts as the output path
    return str(out_path)


def export_pdf(compiled_md, vault_root=None, template=None, toc=False, tof=False,
               generate_date=True, roman_frontmatter=False, page1_starts_with='',
               template_dir=None, output_dir=None,
               new_page_headings=True, restart_footnotes=True,
               intermediate_format=None, keep_intermediate=False,
               mappings_data=None, csl_style_override=None,
               csl_from_template=False, output_name=None):
    """Export to PDF via an intermediate ODT, DOCX, or LaTeX file.

    The intermediate format is auto-determined from the template: ODT is
    preferred when an ODT template file exists, then DOCX, then LaTeX (a
    bare template name with only a .tex file falls back to it; an explicit
    "book.tex" always means LaTeX). Pass intermediate_format='docx' / 'odt' /
    'latex' to override.

    ODT/DOCX use LibreOffice headless for the -> PDF conversion (no
    fallback: needs a large separate PDF engine, has unicode/font issues,
    and produces output unlike the word-processor rendering — see
    export_document's docstring). LaTeX instead uses pandoc's own
    --pdf-engine=lualatex, straight from markdown to PDF — no LibreOffice,
    no intermediate file needed at all (see export_latex).
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
        raw_tpl = template if template is not None else meta['tpl']
        ext_m = re.search(r'\.(docx|odt|tex)$', raw_tpl or '', flags=re.IGNORECASE)
        if ext_m:
            # The export dialog passes a specific template ("book.docx" vs
            # "book.odt" vs "book.tex") — that choice IS the intended
            # intermediate format.
            intermediate_format = ext_m.group(1).lower()
            if intermediate_format == 'tex':
                intermediate_format = 'latex'
        else:
            # Bare name (from frontmatter): prefer ODT, then DOCX, then
            # LaTeX (only when neither of the word-processor formats has a
            # matching template — keeps existing behaviour unchanged for
            # every template that predates LaTeX support).
            tpl = raw_tpl
            template_dir_r = resolve_template_dir(template_dir, vault_root)
            def _has(ext):
                cands = []
                if template_dir_r:
                    cands.append(os.path.join(template_dir_r, f'{tpl}{ext}'))
                cands.append(plugin_template_path(f'{tpl}{ext}'))
                return any(os.path.exists(c) for c in cands)
            intermediate_format = ('odt' if _has('.odt')
                                  else 'docx' if _has('.docx')
                                  else 'latex' if _has('.tex')
                                  else 'docx')
        print(f'PDF intermediate format auto-determined: {intermediate_format}')

    if intermediate_format == 'latex':
        # pandoc goes straight from markdown to PDF — no intermediate file,
        # no LibreOffice. keep_intermediate additionally asks for the .tex
        # pandoc would have produced along the way, via one extra (cheap,
        # no lualatex) pandoc call.
        latex_kwargs = dict(
            vault_root=vault_root, template=template, toc=toc,
            template_dir=template_dir, output_dir=str(out_dir),
            restart_footnotes=restart_footnotes,
            mappings_data=mappings_data, generate_date=generate_date,
            roman_frontmatter=roman_frontmatter, page1_starts_with=page1_starts_with,
            csl_style_override=csl_style_override,
            csl_from_template=csl_from_template, output_name=output_name)
        if keep_intermediate:
            tex_path = export_latex(compiled_md, as_pdf=False, **latex_kwargs)
            print(f'Intermediate LaTeX kept at: {tex_path}')
        out_pdf = export_latex(compiled_md, as_pdf=True, **latex_kwargs)
        print(f'\nExported PDF written to {out_pdf}')
        print(str(out_pdf))
        return str(out_pdf)

    # Export to the intermediate format in a temp directory.
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        common_kwargs = dict(
            vault_root=vault_root, template=template, toc=toc, tof=tof,
            template_dir=template_dir, output_dir=str(tmp_dir),
            new_page_headings=new_page_headings,
            restart_footnotes=restart_footnotes,
            mappings_data=mappings_data, generate_date=generate_date,
            roman_frontmatter=roman_frontmatter, page1_starts_with=page1_starts_with,
            csl_style_override=csl_style_override,
            csl_from_template=csl_from_template,
            # PDF has no live document to refresh fields in later, so let
            # pandoc's own --citeproc render final citations + bibliography
            # instead of live Zotero fields (see export_document's docstring).
            static_citations=True)
        if intermediate_format == 'docx':
            inter_path = Path(export_docx(compiled_md, **common_kwargs))
        else:
            inter_path = Path(export_odt(compiled_md, **common_kwargs))

        out_stem = _resolve_output_stem(output_name, compiled_md.stem)
        out_pdf = out_dir / f"{out_stem}.pdf"

        # LibreOffice headless — renders the document identically to what the
        # user sees on screen, honouring all template styles. Required: there
        # is no fallback (see the docstring).
        soffice = find_soffice()
        if not soffice:
            raise RuntimeError(
                'PDF export needs LibreOffice installed (it converts the '
                'DOCX/ODT to PDF). Install LibreOffice, or export to DOCX/ODT '
                'and convert to PDF yourself.')
        import subprocess
        # Convert INTO the temp dir, never the output dir — LibreOffice names
        # its output <intermediate-stem>.pdf, which would clobber a real
        # export the user keeps under the note's own name. Move it to the
        # final name afterwards (the only file we touch in out_dir).
        cmd = [soffice, '--headless', '--convert-to', 'pdf',
               '--outdir', str(tmp_dir), str(inter_path)]
        print('Converting to PDF via LibreOffice:', ' '.join(cmd))
        subprocess.run(cmd, check=True)
        lo_out = tmp_dir / f"{inter_path.stem}.pdf"
        if not lo_out.exists():
            raise RuntimeError('LibreOffice did not produce a PDF '
                               f'({lo_out} missing).')
        _sh.move(str(lo_out), str(out_pdf))

        if keep_intermediate:
            dest = out_dir / f"{out_stem}{inter_path.suffix}"
            _sh.copy2(str(inter_path), str(dest))
            print(f'Intermediate {intermediate_format.upper()} kept at: {dest}')

        print(f'\nExported PDF written to {out_pdf}')
        print(str(out_pdf))   # last line — parsed by exportCompiler.ts as the output path
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
                       choices=['docx', 'odt', 'latex', 'pdf'],
                       help='Export format when --export is set: docx (default), odt, latex, or pdf')
    parser.add_argument('--keep-intermediate', action='store_true',
                       help='Keep the intermediate docx/odt/tex when exporting to PDF')
    parser.add_argument('--keep-compiled-md', action='store_true',
                       dest='keep_compiled_md',
                       help='Keep the compiled markdown file after a docx/odt/pdf '
                            'export (default: delete it once the export is done; '
                            'has no effect for an already-compiled input file or '
                            'when --export is not given)')
    parser.add_argument('--template', default=None,
                       help='Override the template (default: read "template" YAML from the master file)')
    parser.add_argument('--toc', action='store_true',
                       help='Include a TOC field in the exported docx (default for book* templates)')
    parser.add_argument('--no-toc', action='store_true',
                       help='Omit the TOC field (default for article* templates; override for book* templates)')
    parser.add_argument('--list-of-figures', action='store_true',
                       dest='list_of_figures',
                       help='Include a table of figures (rendered only when the doc has figures; default for book*)')
    parser.add_argument('--no-list-of-figures', action='store_true',
                       dest='no_list_of_figures',
                       help='Omit the table of figures')
    parser.add_argument('--no-generated-date', action='store_true',
                       dest='no_generated_date',
                       help="Don't insert today's date when the doc has no date property")
    parser.add_argument('--roman-frontmatter', action='store_true',
                       dest='roman_frontmatter',
                       help='Roman-numeral frontmatter page numbers, arabic from the reset heading (default for book*)')
    parser.add_argument('--no-roman-frontmatter', action='store_true',
                       dest='no_roman_frontmatter')
    parser.add_argument('--page1-starts-with', default='', dest='page1_starts_with',
                       help='Heading text that begins arabic page 1 (blank = auto)')
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
    parser.add_argument('--output-name', default=None, dest='output_name',
                       help='Filename for the exported document (extension optional — '
                            'the format\'s own is used). Default: the source note\'s '
                            'name. The file is written to this name directly, so a '
                            'different name never touches an existing export.')
    parser.add_argument('--mappings', default=None,
                       help='JSON array of {source, styleName} objects for style mappings '
                            '(overrides mappings.json in the templates directory)')
    parser.add_argument('--csl-style', default=None, dest='csl_style',
                       help='Citation style for the export (a Zotero style name, a '
                            '.csl path, or a style URL). Overrides the template\'s '
                            'own style and the note\'s csl: property, and is written '
                            'into the exported DOCX/ODT\'s Zotero document preferences.')
    parser.add_argument('--csl-style-from-template', action='store_true',
                       dest='csl_from_template',
                       help='Ignore the note\'s csl: property; use the template\'s '
                            'embedded style (or the global default) only.')

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

    # Template-aware defaults (match the export modal's document-type presets):
    #   book*         -> TOC on,  footnote + figure numbering restart per chapter
    #   anything else -> TOC off, global footnote + figure numbering
    # (Heading 1 is a chapter only in a book; elsewhere it is a section.)
    is_book = effective_tpl.startswith('book')
    is_article = effective_tpl.startswith('article')
    use_toc = is_book
    use_tof = is_book
    use_roman = is_book
    use_global = not is_book
    if args.toc:
        use_toc = True
    if args.no_toc:
        use_toc = False
    if args.list_of_figures:
        use_tof = True
    if args.no_list_of_figures:
        use_tof = False
    if args.roman_frontmatter:
        use_roman = True
    if args.no_roman_frontmatter:
        use_roman = False
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
        print("Not an outline (has headings, prose paragraphs, or no note "
              "includes). Treating as a compiled note; skipping compilation.")
        print(f"Template: {effective_tpl} — TOC {'on' if use_toc else 'off'}, "
              f"footnotes {'global' if use_global else 'per-chapter'}")
        compiled = master_file

    if args.export:
        active_mappings = load_mappings(args.templates_dir, args.mappings)
        _common = dict(
            template=args.template or effective_tpl, toc=use_toc, tof=use_tof,
            template_dir=args.templates_dir, output_dir=args.output_dir,
            new_page_headings=not args.no_new_page_headings,
            restart_footnotes=not use_global,
            mappings_data=active_mappings,
            generate_date=not args.no_generated_date,
            roman_frontmatter=use_roman,
            page1_starts_with=args.page1_starts_with,
            csl_style_override=args.csl_style,
            csl_from_template=args.csl_from_template,
            output_name=args.output_name)
        if args.export_format == 'pdf':
            export_pdf(compiled, keep_intermediate=args.keep_intermediate, **_common)
        elif args.export_format == 'odt':
            export_odt(compiled, **_common)
        elif args.export_format == 'latex':
            export_latex(compiled, default_author=args.default_author, **_common)
        else:
            export_docx(compiled, default_author=args.default_author, **_common)

        # The compiled markdown is a throwaway intermediate once it's been
        # exported — delete it unless the user asked to keep it. Never touch
        # an already-compiled file the user handed us directly (is_outline
        # False, `compiled is master_file`): that's their own source note.
        if is_outline and not args.keep_compiled_md:
            try:
                Path(compiled).unlink(missing_ok=True)
            except OSError as e:
                print(f'WARNING: could not remove compiled markdown: {e}',
                      file=sys.stderr)
    elif args.output_name:
        # Compile-only ('md' format) with a chosen filename: rename the
        # compiled markdown to it (never for an already-compiled input the
        # user handed us — that's their source note).
        src = Path(compiled)
        if src != master_file and src.exists():
            dest = src.with_name(_resolve_output_stem(args.output_name, src.stem)
                                 + '.md')
            if dest != src:
                src.replace(dest)
                print(str(dest))

if __name__ == "__main__":
    import sys
    main()
