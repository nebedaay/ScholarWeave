#!/usr/bin/env python3
"""
sw_export_odt_merge.py — ScholarWeave ODT export merge.

Parallel to sw_export_merge.py (which handles DOCX). Takes the clean ODT
produced by pandoc (via the sw-*.lua filters) and the target Export Template
(book.odt/article.odt/document.odt), and copies the pandoc body into the
template's content.xml, RETAINING the template's frontmatter structure:

  - title block (Title/Author/Date/Abstract sections) filled from YAML metadata
  - note/sw-* extra sections injected after the abstract in the title block
  - TOC section (heading + <text:table-of-content>) preserved from template
    when toc=True, with a page break before it
  - pandoc body content with style remaps (First_20_paragraph → Text_20_body etc.)
  - page breaks before every Heading 1 element when new_page_headings=True
  - per-chapter footnote restart when restart_footnotes=True

All ODT Export Templates are fully structured (title block + TOC before body),
so the merge ALWAYS does full structural replacement, unlike the DOCX path which
has a "simple reference-doc" fallback for templates without section breaks.

Usage (from DocumentCompiler.py, in-process):
    from sw_export_odt_merge import merge_odt
    merge_odt(template_path, input_path, output_path,
              title=..., author=..., subtitle=..., date_val=...,
              toc=True, abstract=..., extra_sections=[...], ...)

Standalone CLI:
    python3 sw_export_odt_merge.py \\
        --template book.odt --input clean.odt --output final.odt \\
        --title "My Book" --author "Joseph Hill" --toc
"""

import argparse
import copy
import datetime
import json
import re
import sys
import os
import zipfile
from lxml import etree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sw_merge_helpers import split_paragraphs, find_bibliography_range, strip_bibliography, ZOTERO_BIBL_INSTR

# ── ODF namespace constants ───────────────────────────────────────────────────

_NS = {
    'text':   'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
    'style':  'urn:oasis:names:tc:opendocument:xmlns:style:1.0',
    'office': 'urn:oasis:names:tc:opendocument:xmlns:office:1.0',
    'fo':     'urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0',
    'dc':     'http://purl.org/dc/elements/1.1/',
    'meta':   'urn:oasis:names:tc:opendocument:xmlns:meta:1.0',
    'draw':   'urn:oasis:names:tc:opendocument:xmlns:drawing:1.0',
    'svg':    'urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0',
    'xlink':  'http://www.w3.org/1999/xlink',
}

def T(n):  return '{%s}%s' % (_NS['text'],   n)
def S(n):  return '{%s}%s' % (_NS['style'],  n)
def O(n):  return '{%s}%s' % (_NS['office'], n)
def F(n):  return '{%s}%s' % (_NS['fo'],     n)

# ── Canonical ODT style names ─────────────────────────────────────────────────

# Named styles used in document.odt / article.odt title blocks.
_TITLE_STYLE      = 'Title'
_SUBTITLE_STYLE   = 'Subtitle'
_AUTHOR_STYLE     = 'Author'
_DATE_STYLE       = 'Date'
# "Abstract & keywords heading" — spaces→_20_, &→_26_
_AKH_STYLE        = 'Abstract_20__26__20_keywords_20_heading'
_BODY_STYLE       = 'Text_20_body'
_TOCHEADING_STYLE = 'TOCHeading'

# Styles emitted by pandoc for YAML frontmatter (all dropped from pandoc body
# since they duplicate what the template's title block already provides).
_PANDOC_FRONTMATTER_STYLES = frozenset({
    'Title', 'Subtitle', 'Author', 'Date',
    'Abstract', 'Abstract_Body',
})

# Pandoc-specific → canonical template style remaps (mirrors sw_export_merge.py).
_STYLE_REMAPS = {
    'First_20_paragraph':            _BODY_STYLE,
    'Default_20_Paragraph_20_Style': _BODY_STYLE,
    'Default Paragraph Style':       _BODY_STYLE,
    'Block_20_Text':                 'Quotations',
}

# Words that stay lowercase in title-case (mirrors sw_export_merge.py).
_TITLE_CASE_LOWER = frozenset({
    'a', 'an', 'the', 'and', 'but', 'or', 'nor', 'for', 'yet', 'so',
    'as', 'at', 'by', 'in', 'of', 'on', 'to', 'up', 'via', 'per',
})

# ── helpers ───────────────────────────────────────────────────────────────────

def _title_case(key):
    """Convert a YAML key ('sw-note-to-readers') to display title case.
    Identical logic to sw_export_merge._title_case."""
    key = re.sub(r'^sw-', '', key)
    words = key.split('-')
    result = []
    for idx, word in enumerate(words):
        if idx == 0 or word.lower() not in _TITLE_CASE_LOWER:
            result.append(word.capitalize())
        else:
            result.append(word.lower())
    return ' '.join(result)

def _strip_markdown(text):
    """Remove markdown delimiters (*italic*, **bold**, `code`) from plain text."""
    if not text:
        return text or ''
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'_([^_]+)_', r'\1', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    return text

def _get_sn(el):
    """Get the text:style-name attribute of an ODF element."""
    return el.get(T('style-name'), '')

def _elem_text(el):
    """Concatenate all text content of an element, stripped."""
    return ''.join(el.itertext()).strip()

def _clear_runs(el):
    """Remove all child elements from el (keeping the tag/attrs, clearing text)."""
    for child in list(el):
        el.remove(child)
    el.text = None

def _set_plain_text(el, text):
    """Replace element content with a single plain-text string."""
    _clear_runs(el)
    el.text = text or ''

_MD_RE = re.compile(r'\*\*([^*]+)\*\*|__([^_]+)__|(?<!\*)\*([^*]+)\*(?!\*)|_([^_]+)_|`([^`]+)`')

def _set_markdown_text(el, markdown):
    """Replace element content with runs rendered from markdown (bold/italic/code).
    Uses <text:span> for inline formatting. Falls back to plain text when no
    markdown markers are present."""
    if not markdown or not re.search(r'[*_`]', markdown):
        _set_plain_text(el, markdown or '')
        return
    _clear_runs(el)
    pos = 0
    last = el  # element whose .tail gets the inter-span text
    for m in _MD_RE.finditer(markdown):
        prefix = markdown[pos:m.start()]
        if prefix:
            if last is el:
                el.text = (el.text or '') + prefix
            else:
                last.tail = (last.tail or '') + prefix
        # Determine the content and formatting.
        if m.group(5) is not None:
            content, is_bold, is_italic = m.group(5), False, False  # code → plain
        elif m.group(1) or m.group(2):
            content, is_bold, is_italic = (m.group(1) or m.group(2)), True, False
        else:
            content, is_bold, is_italic = (m.group(3) or m.group(4)), False, True
        span = etree.SubElement(el, T('span'))
        if is_bold:
            span.set(T('style-name'), 'Strong_20_Emphasis')
        elif is_italic:
            span.set(T('style-name'), 'Emphasis')
        span.text = content
        last = span
        pos = m.end()
    suffix = markdown[pos:]
    if suffix:
        if last is el:
            el.text = (el.text or '') + suffix
        else:
            last.tail = (last.tail or '') + suffix

# ── cover-value resolution ────────────────────────────────────────────────────

def resolve_cover(title, subtitle, author, date_val, basename):
    """Resolve cover values per spec (mirrors sw_export_merge.resolve_cover)."""
    if title:
        if subtitle is not None:
            title = title.strip()
        elif ':' in title:
            main, _, sub = title.partition(':')
            title = main.strip()
            subtitle = sub.strip() or None
        else:
            title = title.strip()
    if not title:
        base = basename or ''
        m = re.split(r'\s*[-–]\s*', base, maxsplit=1)
        title = (m[0].strip() if m and m[0].strip() else base)
    author = author or 'Joseph Hill'
    if not date_val:
        today = datetime.date.today()
        date_val = f"{today.strftime('%B')} {today.day}, {today.year}"
    return title, subtitle, author, date_val

# ── template layout extraction ────────────────────────────────────────────────

def _is_toc_element(el):
    return el.tag == T('table-of-content')

def _looks_like_toc_heading(el):
    """True if the element looks like a TOC heading paragraph."""
    sn = _get_sn(el)
    text = _elem_text(el)
    if sn == _TOCHEADING_STYLE:
        return True
    if text.strip().lower() == 'table of contents':
        return True
    return False

def extract_template_layout(template_path):
    """
    Parse the template ODT, returning a dict with:
      title_block   — deep copies of elements before the TOC heading
      toc_heading   — deep copy of the TOC heading element (or None)
      toc_element   — deep copy of <text:table-of-content> (or None)
      content_bytes — raw content.xml bytes (preserves template nsmap/encoding)
      styles_bytes  — raw styles.xml bytes
    """
    with zipfile.ZipFile(template_path) as z:
        content_bytes = z.read('content.xml')
        styles_bytes  = z.read('styles.xml') if 'styles.xml' in z.namelist() else b''

    tmpl_root = etree.fromstring(content_bytes)
    office_text = tmpl_root.find('.//' + O('text'))
    if office_text is None:
        raise ValueError(f'No <office:text> in template: {template_path}')

    children = list(office_text)

    # Find the first <text:table-of-content> element (direct child first, then
    # recursive fallback for templates that wrap the TOC in a section or div).
    toc_idx = next((i for i, el in enumerate(children) if _is_toc_element(el)), None)
    if toc_idx is None:
        # Recursive fallback: look for a nested TOC inside any direct child.
        for i, el in enumerate(children):
            if el.find('.//' + T('table-of-content')) is not None:
                toc_idx = i
                break

    toc_heading = None
    toc_heading_idx = None
    title_block_end = toc_idx if toc_idx is not None else len(children)

    if toc_idx is not None:
        # The TOC heading is the last <text:h> or <text:p> just before the TOC.
        for i in range(toc_idx - 1, -1, -1):
            el = children[i]
            if el.tag in (T('h'), T('p')):
                if _looks_like_toc_heading(el):
                    toc_heading = copy.deepcopy(el)
                    toc_heading_idx = i
                    title_block_end = i  # title block is [0..i)
                    break
                # book.odt uses a custom heading style (P4) for the TOC heading;
                # detect by outline-level=1 on <text:h> or by being the last
                # block element right before the TOC that isn't body content.
                if el.tag == T('h'):
                    toc_heading = copy.deepcopy(el)
                    toc_heading_idx = i
                    title_block_end = i
                    break

    title_block = [copy.deepcopy(el) for el in children[:title_block_end]]
    if toc_idx is not None:
        raw_toc_child = children[toc_idx]
        # If the child IS a TOC, copy it directly; otherwise extract the nested one.
        if _is_toc_element(raw_toc_child):
            toc_element = copy.deepcopy(raw_toc_child)
        else:
            nested = raw_toc_child.find('.//' + T('table-of-content'))
            toc_element = copy.deepcopy(nested) if nested is not None else None
    else:
        toc_element = None

    return {
        'title_block': title_block,
        'toc_heading': toc_heading,
        'toc_element': toc_element,
        'content_bytes': content_bytes,
        'styles_bytes': styles_bytes,
    }

# ── title block fill ──────────────────────────────────────────────────────────

def _fill_title_block(title_block, title, subtitle, author, date_val):
    """
    Walk the template title block elements, filling or removing each by role.
    Returns a new list ready to include in the output.

    AKH headings (abstract / keywords / notes) are always dropped here; the
    caller (merge_odt) injects abstract and extra sections via _append_extra_sections
    so that injection works uniformly regardless of where AKH appears in the template.

    Handles all three ODT template styles:
      document.odt — canonical styles: Title, Subtitle, Author, Date + AKH
      book.odt     — custom styles: P1 (title), Subtitle, P2×2 (author/date) + AKH
      article.odt  — Title + first Text_20_body (author) + AKH
    """
    out = []
    first_para_done = False   # True once the title paragraph has been handled
    author_done     = False
    date_done       = False
    before_akh      = True    # True until we see the first AKH paragraph

    i = 0
    while i < len(title_block):
        el = title_block[i]
        sn = _get_sn(el)
        text = _elem_text(el)

        # ── canonical title/author/date styles ──────────────────────────────
        if sn == _TITLE_STYLE:
            _set_markdown_text(el, title or '')
            out.append(el)
            first_para_done = True

        elif sn == _SUBTITLE_STYLE:
            if subtitle:
                _set_markdown_text(el, subtitle)
                out.append(el)
            # else: drop the Subtitle paragraph when there is no subtitle

        elif sn == _AUTHOR_STYLE:
            _set_plain_text(el, author or '')
            out.append(el)
            author_done = True

        elif sn == _DATE_STYLE:
            _set_plain_text(el, date_val or '')
            out.append(el)
            date_done = True

        # ── AKH (Abstract & keywords heading) ───────────────────────────────
        # Always drop ALL AKH headings and their following body paragraphs from
        # the template's title block. Abstract and extra sections are injected
        # uniformly by merge_odt via _append_extra_sections, so we never need to
        # fill a template AKH in-place. This also handles templates (e.g. book.odt)
        # where AKH sections appear after the TOC and therefore aren't in title_block.
        elif sn == _AKH_STYLE:
            before_akh = False
            # Drop this heading and skip its body paragraph if present.
            if i + 1 < len(title_block) and _get_sn(title_block[i + 1]) == _BODY_STYLE:
                i += 1  # skip body paragraph too

        # ── article.odt: first Text_20_body before any AKH = author ─────────
        elif sn == _BODY_STYLE and before_akh and not first_para_done:
            _set_plain_text(el, author or '')
            out.append(el)
            first_para_done = True
            author_done = True

        elif sn == _BODY_STYLE and before_akh:
            # Additional body-style paragraphs in the title area (e.g. keywords
            # line in article.odt) — keep as-is.
            out.append(el)

        # ── book.odt custom paragraph styles (P1, P2, P3, …) ────────────────
        elif sn.startswith('P') and sn[1:].isdigit():
            if not first_para_done:
                # First custom paragraph = title (P1 in book.odt).
                _set_markdown_text(el, title or '')
                out.append(el)
                first_para_done = True
            elif sn == 'P2' and not author_done:
                # First P2 = author slot.
                _set_plain_text(el, author or '')
                out.append(el)
                author_done = True
            elif sn == 'P2' and author_done and not date_done:
                # Second P2 = date slot.
                _set_plain_text(el, date_val or '')
                out.append(el)
                date_done = True
            else:
                # P3 spacer, P4-type extra — keep.
                out.append(el)

        else:
            # Any other paragraph style — keep as-is.
            out.append(el)

        i += 1

    return out

# ── extra sections injection ──────────────────────────────────────────────────

def _append_extra_sections(out_list, extra_sections):
    """Append AKH heading + Text_20_body paragraph(s) for each
    (key, value) in extra_sections (note/sw-* YAML properties).
    Multi-paragraph values (e.g. a multi-paragraph abstract split by \\n\\n)
    produce one body paragraph per chunk, using split_paragraphs() from
    sw_merge_helpers — the same function used by the DOCX merge so both
    formats handle paragraphs identically."""
    for key, value in extra_sections:
        label = _title_case(key)
        h = etree.Element(T('p'))
        h.set(T('style-name'), _AKH_STYLE)
        h.text = label
        out_list.append(h)
        for chunk in split_paragraphs(value):
            b = etree.Element(T('p'))
            b.set(T('style-name'), _BODY_STYLE)
            _set_markdown_text(b, chunk)
            out_list.append(b)

# ── pandoc body classification ────────────────────────────────────────────────

def classify_pandoc_body(pdc_text):
    """
    Walk pandoc's <office:text>, skipping:
      - <text:table-of-content> elements (we manage TOC from the template)
      - leading YAML frontmatter paragraphs (Title/Author/Date/Abstract) that
        duplicate what the template title block already provides

    Applies style remaps (First_20_paragraph → Text_20_body, etc.) in-place.
    Returns a list of body element references (still children of pdc_text;
    moved to tmpl_text by the caller when assembling output).
    """
    elements = list(pdc_text)
    i = 0

    # Skip any initial TOC elements.
    while i < len(elements) and _is_toc_element(elements[i]):
        i += 1

    # Skip the initial YAML frontmatter block (Title, Author, Date, Abstract).
    # Only enter the skip loop if the first element IS a frontmatter-style paragraph;
    # otherwise the note starts with body content (no title) and we keep everything.
    # When we do enter, skip ALL non-heading content until the first <text:h> element
    # so that abstract body text (which may have any style, not just Abstract_Body)
    # and any other pre-body content is not leaked into the document body.
    if i < len(elements) and _get_sn(elements[i]) in _PANDOC_FRONTMATTER_STYLES:
        while i < len(elements):
            el = elements[i]
            if el.tag == T('h'):
                break  # first actual heading = start of body content
            i += 1     # skip TOC elements, frontmatter, abstract body, etc.

    # Collect and remap remaining body elements.
    body = []
    for el in elements[i:]:
        if _is_toc_element(el):
            continue  # drop pandoc's own TOC
        if el.tag in (T('p'), T('h')):
            sn = _get_sn(el)
            new_sn = _STYLE_REMAPS.get(sn)
            if new_sn:
                el.set(T('style-name'), new_sn)
        body.append(el)

    return body

# ── automatic-styles merge ────────────────────────────────────────────────────

def _collect_defined_names(tmpl_root, styles_root):
    """Collect all style names defined in the template's content.xml auto-styles
    and styles.xml named styles."""
    names = set()
    auto = tmpl_root.find('.//' + O('automatic-styles'))
    if auto is not None:
        for child in auto:
            n = child.get(S('name'))
            if n:
                names.add(n)
    if styles_root is not None:
        for el in styles_root.iter(S('style')):
            n = el.get(S('name'))
            if n:
                names.add(n)
        for el in styles_root.iter(T('list-style')):
            n = el.get(T('name'))
            if n:
                names.add(n)
    return names

def _merge_auto_styles(tmpl_root, pdc_root, styles_root):
    """Merge pandoc's <office:automatic-styles> into the template's, renaming
    any conflicting style names (generated names like P1, T1 may collide with
    book.odt's custom P1/P2/P4 named styles).

    Returns a name_map {old_pandoc_name: new_name} for use by _rewrite_style_refs.
    The renamed copies are appended to tmpl_root's auto-styles section.
    """
    tmpl_auto = tmpl_root.find('.//' + O('automatic-styles'))
    pdc_auto  = pdc_root.find('.//' + O('automatic-styles'))
    if tmpl_auto is None or pdc_auto is None:
        return {}

    existing = _collect_defined_names(tmpl_root, styles_root)
    name_map = {}

    for child in pdc_auto:
        orig = child.get(S('name'), '') or child.get(T('name'), '')
        if not orig:
            continue
        if orig in existing:
            counter = 1
            while f'PDC_{orig}_{counter}' in existing:
                counter += 1
            new_name = f'PDC_{orig}_{counter}'
        else:
            new_name = orig
        name_map[orig] = new_name
        nc = copy.deepcopy(child)
        # Update the name attribute (could be style:name or text:name).
        if nc.get(S('name')):
            nc.set(S('name'), new_name)
        elif nc.get(T('name')):
            nc.set(T('name'), new_name)
        tmpl_auto.append(nc)
        existing.add(new_name)

    return name_map

_STYLE_REF_ATTRS = [T('style-name'), T('list-style-name'), T('master-page-name')]

def _rewrite_style_refs(elements, name_map):
    """Rewrite style-name references in elements (and all descendants) per name_map."""
    if not name_map:
        return
    for el in elements:
        for node in el.iter():
            for attr in _STYLE_REF_ATTRS:
                val = node.get(attr)
                if val and val in name_map:
                    node.set(attr, name_map[val])

# ── page break injection ──────────────────────────────────────────────────────

_H1_PB_STYLE = 'SW_Heading1_Pagebreak'
_H1_PARENT   = 'Heading_20_1'

def _ensure_h1_pagebreak_style(auto_styles):
    """Inject the SW_Heading1_Pagebreak automatic style if absent."""
    for child in auto_styles:
        if child.get(S('name')) == _H1_PB_STYLE:
            return
    style_el = etree.SubElement(auto_styles, S('style'))
    style_el.set(S('name'),                _H1_PB_STYLE)
    style_el.set(S('family'),              'paragraph')
    style_el.set(S('parent-style-name'),   _H1_PARENT)
    pp = etree.SubElement(style_el, S('paragraph-properties'))
    pp.set(F('break-before'), 'page')

def apply_page_breaks(body_elements, tmpl_root):
    """Set the page-break style on all H1 elements in body_elements.
    Also injects the style definition into tmpl_root's automatic-styles."""
    auto = tmpl_root.find('.//' + O('automatic-styles'))
    if auto is not None:
        _ensure_h1_pagebreak_style(auto)
    for el in body_elements:
        if el.tag == T('h') and el.get(T('outline-level'), '') == '1':
            el.set(T('style-name'), _H1_PB_STYLE)

# ── TOC page break ────────────────────────────────────────────────────────────

_TOC_PB_STYLE   = 'SW_TOC_Pagebreak'
_TOC_H_PB_STYLE = 'SW_TOCHeading_Pagebreak'

def _ensure_toc_pagebreak_style(auto_styles):
    for child in auto_styles:
        if child.get(S('name')) == _TOC_PB_STYLE:
            return
    style_el = etree.SubElement(auto_styles, S('style'))
    style_el.set(S('name'),   _TOC_PB_STYLE)
    style_el.set(S('family'), 'paragraph')
    pp = etree.SubElement(style_el, S('paragraph-properties'))
    pp.set(F('break-before'), 'page')

def _ensure_toc_heading_pb_style(auto_styles):
    """Inject SW_TOCHeading_Pagebreak: inherits TOCHeading + fo:break-before=page.
    Applying this to the TOC heading avoids the need for a separate blank
    spacer paragraph before it."""
    for child in auto_styles:
        if child.get(S('name')) == _TOC_H_PB_STYLE:
            return
    style_el = etree.SubElement(auto_styles, S('style'))
    style_el.set(S('name'),               _TOC_H_PB_STYLE)
    style_el.set(S('family'),             'paragraph')
    style_el.set(S('parent-style-name'),  _TOCHEADING_STYLE)
    pp = etree.SubElement(style_el, S('paragraph-properties'))
    pp.set(F('break-before'), 'page')

def make_toc_pagebreak_para(tmpl_root):
    """Return a blank page-break paragraph using the SW_TOC_Pagebreak auto-style.
    Used only when there is no TOC heading paragraph to attach the break to."""
    auto = tmpl_root.find('.//' + O('automatic-styles'))
    if auto is not None:
        _ensure_toc_pagebreak_style(auto)
    p = etree.Element(T('p'))
    p.set(T('style-name'), _TOC_PB_STYLE)
    return p

# ── footnote restart ──────────────────────────────────────────────────────────

def apply_footnote_restart(styles_root):
    """Set text:start-numbering-at="chapter" on footnote notes-configuration.
    Mirrors the per-section footnote restart in sw_export_merge (eachSect).
    Returns True if anything changed."""
    changed = False
    for el in styles_root.iter():
        local = el.tag.split('}')[-1] if '}' in el.tag else el.tag
        if local == 'notes-configuration':
            note_class = el.get(T('note-class'), '')
            if note_class == 'footnote':
                old = el.get(T('start-numbering-at'), '')
                if old != 'chapter':
                    el.set(T('start-numbering-at'), 'chapter')
                    changed = True
    return changed

# ── media merge ───────────────────────────────────────────────────────────────

def _merge_media(z_data, pdc_data):
    """Copy Pictures/* and media/* from pandoc output into template zip data."""
    for n, data in pdc_data.items():
        if (n.startswith('Pictures/') or n.startswith('media/')) and n not in z_data:
            z_data[n] = data

def _merge_manifest(z_data, pdc_data):
    """Merge pandoc's META-INF/manifest.xml picture entries into the template's."""
    MANIFEST = 'META-INF/manifest.xml'
    if MANIFEST not in z_data or MANIFEST not in pdc_data:
        return
    MF_NS = 'urn:oasis:names:tc:opendocument:xmlns:manifest:1.0'
    def mf(n): return '{%s}%s' % (MF_NS, n)
    try:
        tmpl_mf = etree.fromstring(z_data[MANIFEST])
        pdc_mf  = etree.fromstring(pdc_data[MANIFEST])
        existing_paths = set()
        for entry in tmpl_mf:
            p = entry.get(mf('full-path'), '')
            if p:
                existing_paths.add(p)
        for entry in pdc_mf:
            p = entry.get(mf('full-path'), '')
            if p and p not in existing_paths and (
                    p.startswith('Pictures/') or p.startswith('media/')):
                tmpl_mf.append(copy.deepcopy(entry))
                existing_paths.add(p)
        z_data[MANIFEST] = etree.tostring(
            tmpl_mf, xml_declaration=True, encoding='UTF-8', standalone=True)
    except Exception as e:
        print(f'WARNING: could not merge manifest.xml: {e}')

# ── metadata update ───────────────────────────────────────────────────────────

def _update_meta(z_data, title, author):
    """Update meta.xml dc:title and meta:initial-creator / dc:creator if present."""
    if 'meta.xml' not in z_data:
        return
    DC  = _NS['dc']
    META_NS = _NS['meta']
    try:
        root = etree.fromstring(z_data['meta.xml'])
        if title:
            for el in root.iter('{%s}title' % DC):
                el.text = _strip_markdown(title)
                break
        if author:
            for el in root.iter('{%s}creator' % DC):
                el.text = author
                break
            for el in root.iter('{%s}initial-creator' % META_NS):
                el.text = author
                break
        z_data['meta.xml'] = etree.tostring(
            root, xml_declaration=True, encoding='UTF-8', standalone=True)
    except Exception as e:
        print(f'WARNING: could not update meta.xml: {e}')

# ── Zotero bibliography section injection ────────────────────────────────────

def _inject_zotero_bibliography_odt(body_elements):
    """Detect the bibliography section in pandoc ODT body elements and wrap the
    entries in a Zotero <text:section> so LibreOffice + Zotero can refresh it.

    Shares detection logic with the DOCX merge via find_bibliography_range() from
    sw_merge_helpers.  The section name is ZOTERO_BIBL_INSTR (also shared), which
    Zotero for LibreOffice recognises and can update on bibliography refresh.

    Returns a new list with the bibliography entries replaced by the section element.
    The heading element itself is kept outside the section (matching LibreOffice's
    own behaviour when it generates a Zotero bibliography section).

    If no bibliography section is found, returns body_elements unchanged.
    """
    def heading_text(el):
        if el.tag == T('h'):
            return _elem_text(el)
        return None

    def is_bibl_entry(el):
        sn = _get_sn(el).replace('_20_', ' ')
        return 'ibliograph' in sn

    h_idx, s_idx, e_idx = find_bibliography_range(body_elements, heading_text, is_bibl_entry)
    if s_idx is None or s_idx == e_idx:
        return body_elements

    entries = body_elements[s_idx:e_idx]

    # Build <text:section text:style-name="Sect1" text:name="ADDIN ZOTERO_BIBL ...">
    # wrapping the bibliography entry paragraphs.  Zotero for LibreOffice reads the
    # section name as its field instruction and can update the bibliography on refresh.
    section = etree.Element(T('section'))
    section.set(T('style-name'), 'Sect1')
    section.set(T('name'), ZOTERO_BIBL_INSTR)
    for entry in entries:
        section.append(copy.deepcopy(entry))

    # Keep heading + post-bibliography content; replace entry span with the section.
    return list(body_elements[:s_idx]) + [section] + list(body_elements[e_idx:])


# ── main merge ────────────────────────────────────────────────────────────────

def merge_odt(template_path, input_path, output_path,
              title=None, author=None, subtitle=None, date_val=None,
              toc=False, short_title=None, basename=None,
              abstract=None, extra_sections=None,
              new_page_headings=True, restart_footnotes=True):
    """
    Merge pandoc ODT output into the ODT template.

    Parallel to merge() in sw_export_merge.py:
      1. Load template (content.xml + styles.xml) and pandoc output
      2. Resolve cover values (title/subtitle/author/date)
      3. Extract template layout (title block, TOC heading, TOC element)
      4. Fill template title block from YAML metadata
      5. Append extra sections (note/sw-* properties)
      6. Classify pandoc body (skip frontmatter, apply style remaps)
      7. Merge pandoc auto-styles into template (with conflict renaming)
      8. Apply page breaks before H1 elements
      9. Assemble output: title block + TOC + pandoc body
     10. Apply footnote restart (in styles.xml)
     11. Merge pandoc media into template zip
     12. Write output ODT
    """
    # ── Load template ──────────────────────────────────────────────────────
    with zipfile.ZipFile(template_path) as z:
        z_names = z.namelist()
        z_data  = {n: z.read(n) for n in z_names}

    content_bytes = z_data['content.xml']
    styles_bytes  = z_data.get('styles.xml', b'')

    tmpl_root   = etree.fromstring(content_bytes)
    styles_root = etree.fromstring(styles_bytes) if styles_bytes else None

    # ── Load pandoc output ─────────────────────────────────────────────────
    with zipfile.ZipFile(input_path) as z:
        pdc_data    = {n: z.read(n) for n in z.namelist()}
    pdc_root    = etree.fromstring(pdc_data['content.xml'])
    pdc_text    = pdc_root.find('.//' + O('text'))
    if pdc_text is None:
        raise ValueError(f'No <office:text> in pandoc output: {input_path}')

    # ── Resolve cover values ───────────────────────────────────────────────
    title, subtitle, author, date_val = resolve_cover(
        title, subtitle, author, date_val, basename)

    # ── Extract template layout ────────────────────────────────────────────
    layout = extract_template_layout(template_path)

    # ── Fill title block ───────────────────────────────────────────────────
    filled_title = _fill_title_block(
        layout['title_block'], title, subtitle, author, date_val)

    # ── Append abstract + extra sections ───────────────────────────────────
    # Abstract is prepended so it appears first, before note/sw-* sections.
    # Both are injected uniformly via _append_extra_sections so the merge is
    # independent of whether the template's AKH sections were in the title block.
    all_extra = []
    if abstract:
        all_extra.append(('abstract', abstract))
    if extra_sections:
        all_extra.extend(extra_sections)
    if all_extra:
        _append_extra_sections(filled_title, all_extra)

    # ── Classify pandoc body ───────────────────────────────────────────────
    body_elements = classify_pandoc_body(pdc_text)

    # ── Merge pandoc auto-styles into template (before moving elements) ────
    name_map = _merge_auto_styles(tmpl_root, pdc_root, styles_root)
    _rewrite_style_refs(body_elements, name_map)

    # ── Apply page breaks ──────────────────────────────────────────────────
    if new_page_headings:
        apply_page_breaks(body_elements, tmpl_root)

    # ── Locate and clear template <office:text> ────────────────────────────
    tmpl_text = tmpl_root.find('.//' + O('text'))
    if tmpl_text is None:
        raise ValueError(f'No <office:text> in template: {template_path}')
    for child in list(tmpl_text):
        tmpl_text.remove(child)

    # ── Assemble output content ────────────────────────────────────────────

    # 1. Title block (filled from template + YAML metadata).
    for el in filled_title:
        tmpl_text.append(el)

    # 2. TOC section: heading (with page break embedded) + TOC field (when toc=True).
    # The page break is applied directly to the TOC heading paragraph via a new
    # auto-style (SW_TOCHeading_Pagebreak, inheriting TOCHeading), so no separate
    # blank spacer paragraph is needed before the heading.
    if toc and layout['toc_element'] is not None:
        auto = tmpl_root.find('.//' + O('automatic-styles'))
        if auto is not None:
            _ensure_toc_heading_pb_style(auto)
        if layout['toc_heading'] is not None:
            toc_h = copy.deepcopy(layout['toc_heading'])
            toc_h.set(T('style-name'), _TOC_H_PB_STYLE)
            tmpl_text.append(toc_h)
        else:
            # No standalone heading in template — fall back to a blank spacer.
            tmpl_text.append(make_toc_pagebreak_para(tmpl_root))
        tmpl_text.append(copy.deepcopy(layout['toc_element']))
    elif toc and layout['toc_element'] is None:
        # Template has no TOC element (unusual). Insert a minimal TOC heading.
        print('WARNING: template has no TOC element; inserting a bare heading.')
        auto = tmpl_root.find('.//' + O('automatic-styles'))
        if auto is not None:
            _ensure_toc_heading_pb_style(auto)
        h = etree.Element(T('h'))
        h.set(T('style-name'), _TOC_H_PB_STYLE)
        h.set(T('outline-level'), '1')
        h.text = 'Table of Contents'
        tmpl_text.append(h)

    # 3. Strip the bibliography section (pandoc plain-text entries) so a fresh
    #    Zotero section can be appended at the end.  Mirrors the DOCX approach:
    #    both formats detect and strip via the shared strip_bibliography helper,
    #    then each appends its own format-specific Zotero bibliography section.
    def _bibl_heading_text(el):
        if el.tag == T('h'):
            return _elem_text(el)
        return None

    def _is_bibl_entry(el):
        # Accept any non-heading element after the bibliography heading.
        # Bibliography is always the last section, so everything after the
        # heading belongs to it regardless of individual paragraph styles.
        # This mirrors the DOCX approach, which also doesn't inspect entry styles.
        return el.tag != T('h')

    body_elements, _has_bibliography = strip_bibliography(
        body_elements, _bibl_heading_text, _is_bibl_entry)

    # 4. Pandoc body elements (moved from pdc_text to tmpl_text).
    for el in body_elements:
        tmpl_text.append(el)

    # 5. Fresh Zotero bibliography section, appended at the end when the pandoc
    #    output contained a bibliography.  Uses SW_Heading1_Pagebreak so the
    #    heading starts on a new page (same as all other Heading 1 elements).
    if _has_bibliography:
        auto = tmpl_root.find('.//' + O('automatic-styles'))
        if auto is not None:
            _ensure_h1_pagebreak_style(auto)
        _bh = etree.Element(T('h'))
        _bh.set(T('style-name'), _H1_PB_STYLE)
        _bh.set(T('outline-level'), '1')
        _bh.text = 'Bibliography'
        tmpl_text.append(_bh)
        _bsect = etree.Element(T('section'))
        _bsect.set(T('style-name'), 'Sect1')
        _bsect.set(T('name'), ZOTERO_BIBL_INSTR)
        _bph = etree.SubElement(_bsect, T('p'))
        _bph.set(T('style-name'), _BODY_STYLE)
        _bph.text = 'Refresh Zotero to view this content.'
        tmpl_text.append(_bsect)

    # ── Footnote restart ───────────────────────────────────────────────────
    if restart_footnotes and styles_root is not None:
        changed = apply_footnote_restart(styles_root)
        if changed:
            print('ODT: set footnote numbering to restart per chapter')
            z_data['styles.xml'] = etree.tostring(
                styles_root, xml_declaration=True, encoding='UTF-8', standalone=True)

    # ── Serialize updated content.xml ──────────────────────────────────────
    z_data['content.xml'] = etree.tostring(
        tmpl_root, xml_declaration=True, encoding='UTF-8', standalone=True)

    # ── Merge pandoc media files + manifest entries ────────────────────────
    _merge_media(z_data, pdc_data)
    _merge_manifest(z_data, pdc_data)

    # ── Update document metadata ───────────────────────────────────────────
    _update_meta(z_data, title, author)

    # ── Write output ODT ───────────────────────────────────────────────────
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zout:
        # mimetype MUST be the first entry and stored uncompressed (ODF spec).
        if 'mimetype' in z_data:
            zout.writestr('mimetype', z_data['mimetype'],
                          compress_type=zipfile.ZIP_STORED)
        for n, data in z_data.items():
            if n != 'mimetype':
                zout.writestr(n, data)

    print(f'Merged ODT: {output_path}')

# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--template', required=True)
    ap.add_argument('--input',    required=True)
    ap.add_argument('--output',   required=True)
    ap.add_argument('--title',    default=None)
    ap.add_argument('--author',   default=None)
    ap.add_argument('--subtitle', default=None)
    ap.add_argument('--date',     default=None, dest='date_val')
    ap.add_argument('--toc',      action='store_true')
    ap.add_argument('--shorttitle', default=None)
    ap.add_argument('--basename', default=None)
    ap.add_argument('--abstract', default=None)
    ap.add_argument('--extra-sections', default=None, dest='extra_sections',
                    help='JSON array of [key, value] pairs for note/sw-* sections')
    ap.add_argument('--note',     default=None,
                    help='Backward-compat: single note value')
    ap.add_argument('--no-new-page-headings', action='store_true',
                    dest='no_new_page_headings')
    ap.add_argument('--no-global-footnotes', action='store_true',
                    dest='no_global_footnotes',
                    help='Restart footnote numbering per chapter')
    ap.add_argument('--global-footnotes', action='store_true',
                    dest='global_footnotes')
    args = ap.parse_args()

    extra_sections = None
    if args.extra_sections:
        try:
            extra_sections = json.loads(args.extra_sections)
        except (json.JSONDecodeError, ValueError):
            extra_sections = None
    elif args.note:
        extra_sections = [['note', args.note]]

    new_page_headings  = not args.no_new_page_headings
    restart_footnotes  = args.no_global_footnotes

    merge_odt(
        args.template, args.input, args.output,
        title=args.title, author=args.author, subtitle=args.subtitle,
        date_val=args.date_val, toc=args.toc, short_title=args.shorttitle,
        basename=args.basename, abstract=args.abstract,
        extra_sections=extra_sections,
        new_page_headings=new_page_headings,
        restart_footnotes=restart_footnotes,
    )
    print(f'Merged: {args.output}')

if __name__ == '__main__':
    main()
