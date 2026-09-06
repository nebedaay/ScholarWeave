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
import json
import re
import sys
import os
import zipfile
from lxml import etree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sw_merge_helpers import (split_paragraphs, find_bibliography_range,
    strip_bibliography, ZOTERO_BIBL_INSTR, resize_images, STYLE_REMAP,
    STYLE_ALIASES, resolve_style_alias,
    resolve_cover, title_case as _title_case, strip_markdown as _strip_markdown,
    is_toc_heading, process_figures, bundled_template, ensure_odt_styles,
    strip_chapter_prefix, parse_chapter_number, append_extra_sections,
    find_page_reset_index)

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
def X(n):  return '{%s}%s' % (_NS['xlink'],  n)

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
_FIGCAPTION_STYLE = 'FigureCaption'                       # pandoc's + our caption
_FIGIMAGE_STYLE   = 'FigureWithCaption'                   # pandoc's image paragraph
_ALTTEXT_STYLE    = 'caption_20_-_20_Alt-text'            # "caption - Alt-text" (book.odt only)
_TOF_HEADING_STYLE = 'Figure_20_Index_20_Heading'         # "Figure Index Heading"

# Styles emitted by pandoc for YAML frontmatter (all dropped from pandoc body
# since they duplicate what the template's title block already provides).
_PANDOC_FRONTMATTER_STYLES = frozenset({
    'Title', 'Subtitle', 'Author', 'Date',
    'Abstract', 'Abstract_Body',
})

# Pandoc-specific → canonical template style remaps. Table lives in
# sw_merge_helpers.STYLE_REMAP so DOCX and ODT stay in sync.
_STYLE_REMAPS = STYLE_REMAP['odt']

# ── helpers ───────────────────────────────────────────────────────────────────
# _title_case, _strip_markdown, resolve_cover come from sw_merge_helpers
# (shared verbatim with the DOCX merge).

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

# ── cached index (TOC / ToF) content ────────────────────────────────────────

def _rebuild_index_body(index_el, entries, entry_style_fn):
    """Replace an index element's cached <text:index-body> with fresh entries.

    Neither pandoc nor this merge ever computes a TOC/ToF field's displayed
    content — only a word processor does, on "update fields". A headless
    LibreOffice PDF conversion never updates it, so the exported PDF shows
    whatever was last cached in the *template's* field (real but stale
    content, or the literal "Right-click to update field" placeholder). This
    writes correct entries for THIS document instead — as hyperlinks to each
    target's bookmark, without page numbers (pagination isn't known until a
    word processor lays the document out). The field structure is untouched,
    so updating it in a word processor still regenerates everything, page
    numbers included.

    entries: list of (text, bookmark_name_or_None, level) tuples.
    entry_style_fn(level) -> paragraph style name for that entry.
    """
    body = index_el.find(T('index-body'))
    if body is None:
        body = etree.SubElement(index_el, T('index-body'))
    title = body.find(T('index-title'))
    for child in list(body):
        body.remove(child)
    body.text = None
    if title is not None:
        body.append(title)
    for text, bookmark, level in entries:
        if not text:
            continue
        p = etree.SubElement(body, T('p'))
        p.set(T('style-name'), entry_style_fn(level))
        if bookmark:
            a = etree.SubElement(p, T('a'))
            a.set(X('type'), 'simple')
            a.set(X('href'), '#%s' % bookmark)
            a.set(T('style-name'), 'Index_20_Link')
            a.set(T('visited-style-name'), 'Index_20_Link')
            a.text = text
        else:
            p.text = text


def _heading_bookmark(h_el):
    """The name of the first <text:bookmark-start> inside a heading (pandoc
    emits one per heading, slugified from the heading text), or None."""
    bs = h_el.find(T('bookmark-start'))
    return bs.get(T('name')) if bs is not None else None


def _toc_depth(toc_el, default=10):
    """The deepest heading level the template's TOC is configured to show —
    <text:table-of-content-source text:outline-level="N">."""
    src = toc_el.find(T('table-of-content-source'))
    if src is not None:
        try:
            return max(1, int(src.get(T('outline-level'))))
        except (TypeError, ValueError):
            pass
    return default


def _roman(n):
    vals = ((1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'), (100, 'C'),
            (90, 'XC'), (50, 'L'), (40, 'XL'), (10, 'X'), (9, 'IX'),
            (5, 'V'), (4, 'IV'), (1, 'I'))
    out = ''
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out


def _fmt_chapter_number(fmt, n):
    """Render chapter number `n` the way the template's chapter list style
    would — its num-format ('1'/'I'/'i') and either its loext num-list-format
    ('Chapter %1%.') or prefix+num+suffix ('Chapter ' + n + '.')."""
    nf = fmt.get('num_format', '1')
    s = _roman(n) if nf == 'I' else _roman(n).lower() if nf == 'i' else str(n)
    nlf = fmt.get('num_list_format')
    if nlf and '%1%' in nlf:
        return nlf.replace('%1%', s)
    return (fmt.get('prefix') or '') + s + (fmt.get('suffix') or '')


def _toc_entries(body_elements, max_level=10, chapter_fmt=None):
    """(text, bookmark, level) for every <text:h> in the assembled body, down
    to max_level (the template's configured TOC depth). When the template
    numbers its chapters, the number ('Chapter 1.') is computed here and
    prepended — ODF outline numbering only renders on a word-processor field
    update, so the static cached text would otherwise omit it."""
    out = []
    chap_n = 0
    for el in body_elements:
        if el.tag != T('h'):
            continue
        try:
            level = int(el.get(T('outline-level')) or '1')
        except ValueError:
            level = 1
        if level > max_level:
            continue
        level = max(1, level)
        text = _elem_text(el)
        if not text:
            continue
        if (level == 1 and chapter_fmt
                and el.get(T('is-list-header')) != 'true'):
            chap_n += 1
            text = _fmt_chapter_number(chapter_fmt, chap_n) + ' ' + text
        out.append((text, _heading_bookmark(el), level))
    return out


def _tof_entries(body_elements, caption_styles):
    """(text, bookmark, 1) for every figure-caption paragraph in the body.
    Captions carry a <text:sequence>, not a bookmark, so a bookmark is added
    here (in place — the elements are appended to the body right after) so the
    ToF entry can link to it."""
    out = []
    n = 0
    for el in body_elements:
        if el.tag == T('p') and el.get(T('style-name')) in caption_styles:
            text = _elem_text(el)
            if not text:
                continue
            n += 1
            name = '_sw_fig_%d' % n
            bs = etree.Element(T('bookmark-start'));  bs.set(T('name'), name)
            be = etree.Element(T('bookmark-end'));    be.set(T('name'), name)
            el.insert(0, bs)
            el.append(be)
            out.append((text, name, 1))
    return out


# ── figure caption builders (used by the shared process_figures walk) ────────

def _make_figure_caption(desc, number, ordinal, chapter_scoped, style=_FIGCAPTION_STYLE):
    """Build a figure caption paragraph: 'Figure <seq>. <desc>'. The <text:sequence>
    named "Figure" is what <text:illustration-index> collects for the ToF; its
    cached text is the number computed by process_figures (LibreOffice
    recalculates on Tools ▸ Update ▸ Fields). `style` is the template's caption
    style (FigureCaption when defined, else Caption)."""
    p = etree.Element(T('p'))
    p.set(T('style-name'), style)
    p.text = 'Figure '
    seq = etree.SubElement(p, T('sequence'))
    seq.set(T('ref-name'), 'refFigure%d' % (ordinal - 1))
    seq.set(T('name'), 'Figure')
    seq.set(T('formula'), 'ooow:Figure+1')
    seq.set(S('num-format'), '1')
    if chapter_scoped:
        seq.set(T('display-outline-level'), '1')
        seq.set(T('separation-character'), '.')
    seq.text = number
    seq.tail = '. ' + desc
    return p


def _flatten_caption_fields(body_elements, caption_styles):
    """Replace the <text:sequence> in each figure caption with its computed
    number as literal text. Static path only.

    process_figures computes the correct chapter-scoped number ('1.1') and
    caches it in the sequence, but LibreOffice recomputes the sequence on
    load / PDF export and gets continuous numbering ('1', '2', '3') — its
    display-outline-level reads document *outline* numbering, and book.odt
    numbers chapters via a list style bound to Heading 1, not outline
    numbering. Freezing the caption text sidesteps that, the same way the
    cached TOC/ToF and static citations sidestep every other field the PDF
    path can't rely on a word processor to update."""
    for el in body_elements:
        if el.tag != T('p') or el.get(T('style-name')) not in caption_styles:
            continue
        for seq in list(el.findall(T('sequence'))):
            frozen = (seq.text or '') + (seq.tail or '')
            children = list(el)
            i = children.index(seq)
            el.remove(seq)
            if i == 0:
                el.text = (el.text or '') + frozen
            else:
                children[i - 1].tail = (children[i - 1].tail or '') + frozen


def _make_alttext_para(alt_text):
    """Build a 'caption - Alt-text' paragraph. Emitted only when the template
    defines the style (book.odt), matching the DOCX merge."""
    p = etree.Element(T('p'))
    p.set(T('style-name'), _ALTTEXT_STYLE)
    p.text = 'Alt-text.' + ((' ' + alt_text) if alt_text else '')
    return p


# ── template layout extraction ────────────────────────────────────────────────

def _is_toc_element(el):
    return el.tag == T('table-of-content')

def _is_illustration_index(el):
    return el.tag == T('illustration-index')

def _looks_like_tof_heading(el):
    """True if the element looks like a Table of Figures heading paragraph."""
    if _get_sn(el) == _TOF_HEADING_STYLE:
        return True
    return _elem_text(el).strip().lower() in ('table of figures', 'figure index')

def _looks_like_toc_heading(el):
    """True if the element looks like a TOC heading paragraph."""
    sn = _get_sn(el)
    if sn == _TOCHEADING_STYLE:
        return True
    return is_toc_heading(_elem_text(el))

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

    # Table of Figures: <text:illustration-index> + its heading paragraph.
    tof_element = tof_heading = None
    tof_idx = next((i for i, el in enumerate(children)
                    if _is_illustration_index(el)), None)
    if tof_idx is None:
        for i, el in enumerate(children):
            if el.find('.//' + T('illustration-index')) is not None:
                tof_idx = i
                break
    if tof_idx is not None:
        raw = children[tof_idx]
        tof_element = copy.deepcopy(raw if _is_illustration_index(raw)
                                   else raw.find('.//' + T('illustration-index')))
        # Drop the template's stale cached entry list; LibreOffice rebuilds it
        # (Tools ▸ Update ▸ Fields), and an empty body is cleaner than a wrong one.
        _body = tof_element.find(T('index-body'))
        if _body is not None:
            for _c in list(_body):
                _body.remove(_c)
        for i in range(tof_idx - 1, -1, -1):
            el = children[i]
            if el.tag in (T('h'), T('p')):
                if _looks_like_tof_heading(el):
                    tof_heading = copy.deepcopy(el)
                break

    has_alttext_style = ('style:name="%s"' % _ALTTEXT_STYLE).encode() in styles_bytes
    # FigureCaption when the template defines it (document.odt), else Caption
    # (book.odt) — both exist in the bundled templates.
    caption_style = (_FIGCAPTION_STYLE
                     if ('style:name="%s"' % _FIGCAPTION_STYLE).encode() in styles_bytes
                     else 'Caption')

    # Chapter numbering: the {prefix, num_format, suffix} the template uses for
    # its numbered chapters (book.odt: "Chapter " / "1" / "." from the list style
    # bound to Heading 1, or a <text:list> wrapping a level-1 heading). None for
    # document/article — they don't auto-number chapters (≈ DOCX chapter_numid).
    chapter_number_format = _detect_chapter_number_format(
        etree.fromstring(styles_bytes) if styles_bytes else None, tmpl_root)

    # Master page the template's OWN numbered-chapter sample headings switch
    # to (book.odt: "Converted5" on its first sample chapter). Needed so each
    # EXPORTED chapter can re-trigger that master's "different first page"
    # header/footer-first treatment — see apply_chapter_numbering_odt.
    chapter_master = _detect_chapter_master(tmpl_root)

    return {
        'title_block': title_block,
        'toc_heading': toc_heading,
        'toc_element': toc_element,
        'tof_heading': tof_heading,
        'tof_element': tof_element,
        'has_alttext_style': has_alttext_style,
        'caption_style': caption_style,
        'chapter_number_format': chapter_number_format,
        'chapter_master': chapter_master,
        'content_bytes': content_bytes,
        'styles_bytes': styles_bytes,
    }


def _detect_chapter_number_format(styles_root, tmpl_root):
    """Find the {prefix, num_format, suffix, direct} the template uses to
    number chapter headings. Looks at the list style bound to Heading 1
    (style:list-style-name) and any list style wrapping a level-1 <text:h> in
    the template body. Returns None when the template does not number its
    chapter headings.

    `direct` is True when Heading_20_1/Heading ITSELF carries the
    list-style-name (numbering built directly into the heading style — what
    book.odt does as of 2026-09-04), False when it was only found via a
    <text:list> wrapping a sample numbered heading (the template numbers by
    wrapping, not by the style). apply_chapter_numbering_odt uses this to
    decide whether the numbered-chapter auto-style should PRESERVE the
    heading's own list-style-name (direct case — clearing it would silence
    the only numbering mechanism the template has) or blank it (wrap case —
    our merge doesn't replicate the wrap, so numbering instead comes from the
    <text:outline-style> fallback that applies to headings with no list-style
    of their own)."""
    def _fmt(list_style_el, direct):
        lvl = list_style_el.find(T('list-level-style-number'))
        if lvl is None:
            return None
        return {
            'prefix': lvl.get(S('num-prefix')) or '',
            'num_format': lvl.get(S('num-format')) or '1',
            'suffix': lvl.get(S('num-suffix')) or '',
            'num_list_format': lvl.get(
                '{urn:org:documentfoundation:names:experimental:'
                'office:xmlns:loext:1.0}num-list-format'),
            'direct': direct,
        }
    # candidate list-style names, tagged by how we found them
    direct_names = set()
    wrap_names = set()
    for root in (styles_root, tmpl_root):
        if root is None:
            continue
        for st in root.iter(S('style')):
            if st.get(S('name')) in ('Heading_20_1', 'Heading') \
                    and st.get(S('list-style-name')):
                direct_names.add(st.get(S('list-style-name')))
    for lst in tmpl_root.iter(T('list')):
        h = lst.find(T('list-item') + '/' + T('h'))
        if h is not None and h.get(T('outline-level'), '') == '1' \
                and lst.get(T('style-name')):
            wrap_names.add(lst.get(T('style-name')))
    for names, direct in ((direct_names, True), (wrap_names, False)):
        for root in (styles_root, tmpl_root):
            if root is None:
                continue
            for ls in root.iter(T('list-style')):
                if ls.get(S('name')) in names:
                    f = _fmt(ls, direct)
                    if f:
                        return f
    return None


def _detect_chapter_master(tmpl_root):
    """Return the master-page-name the template's OWN first main-body chapter
    heading (the one right after Introduction/Chapter 1/frontmatter — same
    reset point find_page_reset_index would pick) switches to, via its
    auto-style's style:master-page-name. None if the template assigns no
    such master.

    Doesn't use parse_chapter_number on the heading TEXT — book.odt's own
    sample chapters ("What's It All About?", "How It All Began") number
    purely via the style now (see _detect_chapter_number_format's `direct`),
    so their text carries no "Chapter N" marker to match at all. Locating
    "the heading after the frontmatter reset point" with the SAME
    find_page_reset_index used for roman/arabic numbering finds the right
    heading regardless of whether its numbering is text-based or style-based.

    Rationale for why this matters: ODF's "different first page"
    (header-first/footer-first vs. header/footer) only applies on the page
    where a style:master-page-name is EXPLICITLY assigned — every subsequent
    page just continues that same master's regular header/footer, even
    across an fo:break-before page break, until another explicit
    master-page-name switch. book.odt assigns a fresh master (e.g.
    "Converted5") to each of its OWN sample chapter headings for exactly this
    reason; our merge must do the same for every EXPORTED chapter (there's no
    way to give each one its own unique template-authored master — a note
    can have any number of chapters — so the one found here is reused for
    all of them, re-triggering "first page" treatment each time it's
    (re-)assigned)."""
    h1_els = [h for h in tmpl_root.iter(T('h')) if h.get(T('outline-level'), '') == '1']
    h1_texts = [_elem_text(h) for h in h1_els]
    idx = find_page_reset_index(h1_texts)
    if idx is None or idx + 1 >= len(h1_els):
        return None
    next_h = h1_els[idx + 1]
    for st in tmpl_root.iter(S('style')):
        if st.get(S('family')) == 'paragraph' and st.get(S('name')) == next_h.get(T('style-name')):
            return st.get(S('master-page-name'))
    return None

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
            if date_val:
                _set_plain_text(el, date_val)
                out.append(el)
                date_done = True
            # else: drop the Date paragraph when there is no date

        # ── AKH (Abstract & keywords heading) ───────────────────────────────
        # Always drop ALL AKH headings and their following body paragraphs from
        # the template's title block. Abstract and extra sections are injected
        # uniformly by merge_odt via _append_extra_sections, so we never need to
        # fill a template AKH in-place. This also handles templates (e.g. book.odt)
        # where AKH sections appear after the TOC and therefore aren't in title_block.
        elif sn == _AKH_STYLE:
            before_akh = False
            # Drop this heading AND every following paragraph up to the next
            # heading (or the end of the title block) — that's the whole
            # AKH section, whatever style its body paragraph(s) carry.
            # A template converted from DOCX (e.g. book.odt) gives each such
            # paragraph its own auto-style rather than literally
            # 'Text_20_body' (its Note body is styled "P3"), so matching on
            # style name alone misses it and leaks the placeholder text —
            # matching "runs of non-heading elements" instead is robust to
            # that regardless of the template's paragraph-style naming.
            j = i + 1
            while j < len(title_block) and title_block[j].tag != T('h'):
                j += 1
            i = j - 1

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
                if date_val:
                    _set_plain_text(el, date_val)
                    out.append(el)
                    date_done = True
                # else: drop the date P2 when there is no date
            else:
                # P3 spacer, P4-type extra — keep.
                out.append(el)

        else:
            # Any other paragraph style — keep as-is.
            out.append(el)

        i += 1

    return out

# ── extra sections injection ──────────────────────────────────────────────────

def _make_akh_heading(label):
    h = etree.Element(T('p'))
    h.set(T('style-name'), _AKH_STYLE)
    h.text = label
    return h

def _make_akh_body(chunk):
    b = etree.Element(T('p'))
    b.set(T('style-name'), _BODY_STYLE)
    _set_markdown_text(b, chunk)
    return b

def _append_extra_sections(out_list, extra_sections):
    """Append an AKH heading + Text_20_body paragraph(s) for each (key, value)
    in extra_sections (abstract + note/sw-* YAML properties). Shared loop lives
    in sw_merge_helpers.append_extra_sections."""
    append_extra_sections(out_list, extra_sections,
                          _make_akh_heading, _make_akh_body)

# ── pandoc body classification ────────────────────────────────────────────────

def classify_pandoc_body(pdc_text, style_remap=None):
    """
    Walk pandoc's <office:text>, skipping:
      - <text:table-of-content> elements (we manage TOC from the template)
      - leading YAML frontmatter paragraphs (Title/Author/Date/Abstract) that
        duplicate what the template title block already provides

    Applies style remaps (First_20_paragraph → Text_20_body, etc.) in-place.
    `style_remap` defaults to STYLE_REMAP['odt']; merge_odt passes an
    effective copy with 'Quotations' resolved per-template by
    resolve_style_alias — see STYLE_ALIASES.
    Returns a list of body element references (still children of pdc_text;
    moved to tmpl_text by the caller when assembling output).
    """
    style_remap = style_remap if style_remap is not None else _STYLE_REMAPS
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
            new_sn = style_remap.get(sn)
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


def _resolve_named_style_and_display(tmpl_root, styles_root, style_name, max_depth=6):
    """Walk a style's parent-style-name chain to find (a) the nearest REAL
    NAMED style (defined in styles.xml's <office:styles>, as opposed to an
    automatic style in content.xml) and (b) a human-readable
    style:display-name found along the way.

    (a) matters because ODF's style:parent-style-name is meant to reference a
    common/named style, not another automatic one — book.odt's TOC heading is
    styled with the AUTOMATIC style "P5", whose own parent-style-name is the
    NAMED style "Heading_20_1_20_-_20_exclude_20_from_20_TOC"; our synthetic
    page-break variant should be parented to the latter, not to "P5" directly.

    (b) matters for LibreOffice's <loext:style-ref> header fields, which look
    up a heading by its human-readable name — "Heading_20_1_20_-_20_exclude_
    20_from_20_TOC"'s display-name is "Heading 1 - exclude from TOC", the
    exact text such a field's text:ref-name carries. Giving our synthetic
    style the WRONG display name (or none) is why those fields show "Error:
    Reference source not found", and referencing an undefined canonical name
    (book.odt has no style literally named "TOCHeading") is why a heading
    renders unstyled as "Default Paragraph Style".

    Returns (resolved_named_style, display_name); display_name falls back to
    a best-effort underscore-to-space rendering of whatever name was last
    resolved when no explicit style:display-name is found anywhere.
    """
    auto = tmpl_root.find('.//' + O('automatic-styles')) if tmpl_root is not None else None
    seen = set()
    name = style_name
    display_name = None
    resolved_named = style_name
    for _ in range(max_depth):
        if not name or name in seen:
            break
        seen.add(name)
        named_el = None
        if styles_root is not None:
            for child in styles_root.iter(S('style')):
                if child.get(S('name')) == name:
                    named_el = child
                    break
        if named_el is not None:
            resolved_named = name
            if display_name is None:
                display_name = named_el.get(S('display-name'))
            break   # a real named style — stop walking further up
        auto_el = None
        if auto is not None:
            for child in auto:
                if child.get(S('name')) == name:
                    auto_el = child
                    break
        if auto_el is None:
            break
        if display_name is None:
            display_name = auto_el.get(S('display-name'))
        parent = auto_el.get(S('parent-style-name'))
        if not parent:
            break
        name = parent
    if not display_name:
        display_name = (resolved_named or style_name or '') \
            .replace('_20_', ' ').replace('_2d_', '-')
    return resolved_named, display_name


def _ensure_h1_pagebreak_style(auto_styles, display_name=None):
    """Inject the SW_Heading1_Pagebreak automatic style if absent."""
    for child in auto_styles:
        if child.get(S('name')) == _H1_PB_STYLE:
            return
    style_el = etree.SubElement(auto_styles, S('style'))
    style_el.set(S('name'),                _H1_PB_STYLE)
    style_el.set(S('family'),              'paragraph')
    style_el.set(S('parent-style-name'),   _H1_PARENT)
    if display_name:
        style_el.set(S('display-name'), display_name)
    pp = etree.SubElement(style_el, S('paragraph-properties'))
    pp.set(F('break-before'), 'page')

def apply_page_breaks(body_elements, tmpl_root, display_name=None):
    """Set the page-break style on all H1 elements in body_elements.
    Also injects the style definition into tmpl_root's automatic-styles.
    Chapter headings already wrapped in <text:list> by apply_chapter_numbering_odt
    are nested (not direct members of body_elements) and are skipped here — they
    carry their own page-break-bearing style."""
    auto = tmpl_root.find('.//' + O('automatic-styles'))
    if auto is not None:
        _ensure_h1_pagebreak_style(auto, display_name)
    for el in body_elements:
        if el.tag == T('h') and el.get(T('outline-level'), '') == '1':
            el.set(T('style-name'), _H1_PB_STYLE)


_LOEXT = 'urn:org:documentfoundation:names:experimental:office:xmlns:loext:1.0'

def _enable_chapter_outline_numbering(styles_root, fmt):
    """Turn on ODF chapter numbering (<text:outline-style> level 1) with the
    template's chapter format ({prefix, num_format, suffix}). This is what
    <text:sequence text:display-outline-level="1"> on figure captions reads, so
    enabling it is what makes ODT figures 'Figure C.N' like DOCX. Returns True
    if it changed anything."""
    if styles_root is None or not fmt:
        return False
    os_el = None
    for e in styles_root.iter(T('outline-style')):
        os_el = e
        break
    if os_el is None:
        return False
    lvl1 = None
    for lvl in os_el.iter(T('outline-level-style')):
        if lvl.get(T('level')) == '1':
            lvl1 = lvl
            break
    if lvl1 is None:
        lvl1 = etree.SubElement(os_el, T('outline-level-style'))
        lvl1.set(T('level'), '1')
    lvl1.set(S('num-format'), fmt['num_format'] or '1')
    if fmt['prefix']:
        lvl1.set(S('num-prefix'), fmt['prefix'])
    if fmt['suffix']:
        lvl1.set(S('num-suffix'), fmt['suffix'])
    if fmt.get('num_list_format'):
        lvl1.set('{%s}num-list-format' % _LOEXT, fmt['num_list_format'])
    return True


_CHAPTER_H_STYLE = 'SW_Chapter_Heading'

def _ensure_chapter_heading_style(auto_styles, new_page, display_name=None,
                                  clear_list_style=True):
    """SW_Chapter_Heading: inherits Heading 1. Page break when per-heading
    page breaks are on.

    clear_list_style: True (the historical default) sets list-style-name=""
    so the template's paragraph-level chapter list does NOT also number it —
    only <text:outline-style>'s chapter numbering does. This is correct ONLY
    when the template's chapter numbering was detected via a <text:list>
    WRAPPING a sample heading rather than attached to Heading_20_1 itself
    (_detect_chapter_number_format's `direct` is False): the wrap isn't
    reproduced by this merge, so outline-style's numbering — which applies to
    any heading with no list-style of its own — is the only thing left to
    number it. When numbering is instead built directly into Heading_20_1's
    OWN list-style-name (book.odt as of 2026-09-04: `direct` True), clearing
    it here would silence the template's only numbering mechanism and leave
    numbered chapters unnumbered — pass False to inherit it unchanged."""
    for child in auto_styles:
        if child.get(S('name')) == _CHAPTER_H_STYLE:
            return
    se = etree.SubElement(auto_styles, S('style'))
    se.set(S('name'),              _CHAPTER_H_STYLE)
    se.set(S('family'),            'paragraph')
    se.set(S('parent-style-name'), _H1_PARENT)
    if clear_list_style:
        se.set(S('list-style-name'), '')
    if display_name:
        se.set(S('display-name'), display_name)
    if new_page:
        pp = etree.SubElement(se, S('paragraph-properties'))
        pp.set(F('break-before'), 'page')


def apply_chapter_numbering_odt(body_elements, tmpl_root, new_page_headings,
                                display_name=None, direct_list_style=False,
                                chapter_master=None):
    """Strip the literal 'Chapter N:' prefix from numbered chapter Heading 1s so
    ODF chapter numbering supplies 'Chapter N.' — either via Heading_20_1's own
    list-style-name (direct_list_style=True) or via the <text:outline-style>
    fallback _enable_chapter_outline_numbering enables (direct_list_style=
    False). Numbered chapters get SW_Chapter_Heading; see
    _ensure_chapter_heading_style for what direct_list_style controls there.
    Non-numbered Heading 1s (Preface, Introduction, Conclusion, TOC, ToF,
    Bibliography) get text:is-list-header="true" so they are excluded from
    the chapter count — a figure in the Preface then numbers 'Figure 0.n'
    like DOCX's STYLEREF. Run AFTER apply_page_breaks. Returns True when a
    numbered chapter was seen.

    chapter_master (from _detect_chapter_master): when the template assigns
    its OWN sample chapters a master-page-name, re-assign that SAME master to
    EVERY exported chapter so its "different first page" header/footer-first
    treatment re-triggers at each chapter start rather than only the first
    time that master appears (see _detect_chapter_master; needs LibreOffice
    verification that re-assigning an already-active master-page-name really
    does retrigger first-page treatment).
    """
    auto = tmpl_root.find('.//' + O('automatic-styles'))
    numbered = 0
    for el in body_elements:
        if el.tag != T('h') or el.get(T('outline-level'), '') != '1':
            continue
        txt = _elem_text(el)
        stripped = strip_chapter_prefix(txt)
        if parse_chapter_number(txt) > 0 and stripped != txt:
            if auto is not None:
                _ensure_chapter_heading_style(
                    auto, new_page_headings, display_name,
                    clear_list_style=not direct_list_style)
            _set_plain_text(el, stripped)
            style_name = _CHAPTER_H_STYLE
            if chapter_master and auto is not None:
                style_name = _page_switch_variant(
                    auto, _CHAPTER_H_STYLE, chapter_master, None,
                    suffix='_ChapterStart')
            el.set(T('style-name'), style_name)
            numbered += 1
        else:
            el.set(T('is-list-header'), 'true')
    return numbered > 0


# ── Named-style page mode (2026-09-05) ──────────────────────────────────────
# An alternative to the SW_*-clone machinery above, for a template that drives
# chapter page breaks + page-style switching from the heading styles
# THEMSELVES — LibreOffice's native "insert page break before, with page
# style" Text Flow setting, i.e. style:master-page-name set directly on the
# NAMED style "Heading 1" (and a "Heading 1 Frontmatter" variant for
# roman-numbered sections) — rather than needing this merge to synthesize a
# fresh per-paragraph auto-style for every heading just to carry that
# property. book.odt was rebuilt this way 2026-09-05. Plain chapter and
# frontmatter headings are assigned the matching NAMED style verbatim, unlike
# every other heading-styling function above; LibreOffice re-triggers
# "different first page" every time that style's master-page-name is
# (re-)applied to a new paragraph, with no per-heading bookkeeping needed on
# our side. Only the roman→arabic reset heading still needs a one-off
# per-instance auto-style: LibreOffice has no way to attach "restart page
# numbering here" to a reusable named style — the "change page number"
# option only exists on a manual/direct page break — which is exactly why
# the user had to insert one by hand for Introduction and remove the style's
# own automatic break there. _ensure_reset_heading_style mirrors that by-hand
# result (a 'P12'-style auto-style: parent Heading_20_1, its own
# master-page-name, page-number=1, list-style-name cleared since it isn't a
# numbered chapter).

def uses_named_style_page_mode(styles_root):
    """True when the template's own 'Heading 1' carries a
    style:master-page-name — signal to use apply_heading_roles_named_style_mode
    instead of apply_page_breaks/apply_chapter_numbering_odt/
    apply_roman_frontmatter_odt. False for templates (document.odt,
    article.odt, or a book.odt not yet rebuilt this way) that need this merge
    to synthesize the page-break/master-switch styling itself."""
    return bool(_named_style_master(styles_root, _H1_PARENT))


def _named_style_master(styles_root, style_name):
    """The style:master-page-name a NAMED paragraph style carries directly
    (not inherited — ODF/LibreOffice never applies master-page-name through
    style:parent-style-name, only when set on the paragraph's own exact
    style), or None."""
    if styles_root is None or not style_name:
        return None
    for st in styles_root.iter(S('style')):
        if st.get(S('name')) == style_name:
            return st.get(S('master-page-name'))
    return None


def _detect_frontmatter_heading_style(styles_root):
    """Find the template's dedicated frontmatter variant of Heading 1 (book.odt:
    'Heading 1 Frontmatter', parented to Heading_20_1 with its own
    style:master-page-name so the roman-numbered pages get their own page
    style the same native way). Returns the style's own name, or None if the
    template doesn't define one (then every heading just uses Heading_20_1 —
    matches the outcome of leaving roman_frontmatter off)."""
    if styles_root is None:
        return None
    defined = [(st.get(S('name')), st.get(S('display-name')))
               for st in styles_root.iter(S('style'))
               if st.get(S('family')) == 'paragraph']
    return resolve_style_alias(
        defined,
        ['Heading 1 Frontmatter', 'Frontmatter Heading 1',
         'Heading 1 - Frontmatter'],
        None)


_RESET_HEADING_SUFFIX = '_PgReset'

def _ensure_reset_heading_style(auto_styles, base_style, master, clear_numbering):
    """One-off per-instance style for the roman→arabic reset heading — see
    the "Named-style page mode" note above for why this ONE heading still
    needs an auto-style even in named-style mode."""
    name = base_style + _RESET_HEADING_SUFFIX
    for child in auto_styles:
        if child.get(S('name')) == name:
            return name
    se = etree.SubElement(auto_styles, S('style'))
    se.set(S('name'), name)
    se.set(S('family'), 'paragraph')
    se.set(S('parent-style-name'), base_style)
    se.set(S('master-page-name'), master)
    if clear_numbering:
        se.set(S('list-style-name'), '')
    pp = etree.SubElement(se, S('paragraph-properties'))
    pp.set(S('page-number'), '1')
    return name


def apply_heading_roles_named_style_mode(body_elements, tmpl_root, styles_root,
                                         frontmatter_style, reset_text):
    """The named-style-mode replacement for apply_page_breaks +
    apply_chapter_numbering_odt's chapter-clone step + apply_roman_
    frontmatter_odt, combined into one pass (all three decisions — chapter
    vs frontmatter vs the reset heading — depend on the same per-heading
    role classification, so there is no need to touch each heading three
    times under three different auto-style names the way the old,
    independent passes did).

    reset_text — plain text of the heading where arabic page 1 begins (from
    find_page_reset_index), or None/'' when roman_frontmatter is off — in
    which case every heading just gets Heading_20_1, no frontmatter split.
    Returns True when a numbered chapter was seen (mirrors
    apply_chapter_numbering_odt)."""
    auto = tmpl_root.find('.//' + O('automatic-styles'))
    h1s = [el for el in body_elements
           if el.tag == T('h') and el.get(T('outline-level'), '') == '1']

    reset_idx = None
    if reset_text:
        marker = strip_chapter_prefix(reset_text).strip().lower()
        full = reset_text.strip().lower()
        for i, h in enumerate(h1s):
            cand = {_elem_text(h).strip().lower(),
                    strip_chapter_prefix(_elem_text(h)).strip().lower()}
            if full in cand or (marker and marker in cand):
                reset_idx = i
                break

    arabic_master = _named_style_master(styles_root, _H1_PARENT)
    numbered = 0
    for i, el in enumerate(h1s):
        txt = _elem_text(el)
        stripped = strip_chapter_prefix(txt)
        is_numbered = parse_chapter_number(txt) > 0 and stripped != txt

        if i == reset_idx:
            _set_plain_text(el, stripped if is_numbered else txt)
            if auto is not None and arabic_master:
                el.set(T('style-name'), _ensure_reset_heading_style(
                    auto, _H1_PARENT, arabic_master,
                    clear_numbering=not is_numbered))
            else:
                el.set(T('style-name'), _H1_PARENT)
            if is_numbered:
                numbered += 1
            else:
                el.set(T('is-list-header'), 'true')
            continue

        if is_numbered:
            _set_plain_text(el, stripped)
            el.set(T('style-name'), _H1_PARENT)
            numbered += 1
            continue

        # Non-chapter, non-reset: the frontmatter style before the reset
        # point (Preface, ToF-in-body if it's ever a real pandoc heading),
        # plain Heading_20_1 after it (Conclusion, a trailing Bibliography).
        el.set(T('is-list-header'), 'true')
        if frontmatter_style and reset_idx is not None and i < reset_idx:
            el.set(T('style-name'), frontmatter_style)
        else:
            el.set(T('style-name'), _H1_PARENT)

    return numbered > 0


# ── Roman-numeral frontmatter page numbering ──────────────────────────────────
# Parallel to the DOCX section-break switch (sw_export_merge.merge, the
# roman_frontmatter block). The shared decision — which heading is where arabic
# "page 1" begins — is find_page_reset_index() in sw_merge_helpers, used by both
# formats. Here we implement the switch the ODF way: assign a roman master page
# to every level-1 heading before the reset heading, and an arabic master page
# with style:page-number="1" to the reset heading itself.

def _detect_page_masters(styles_root, tmpl_root):
    """Return (roman_master, arabic_master) master-page names from the template,
    or (None, None) when it has no roman/arabic master pages.

    roman_master  — a master-page whose page-layout has style:num-format "i"/"I".
    arabic_master — the master-page referenced by a paragraph style that carries
                    style:page-number="1" (the template's own frontmatter→body
                    switch point); falls back to any non-roman master, else
                    'Standard'.
    """
    if styles_root is None:
        return None, None
    layfmt = {}
    for pl in styles_root.iter(S('page-layout')):
        pp = pl.find(S('page-layout-properties'))
        layfmt[pl.get(S('name'))] = pp.get(S('num-format')) if pp is not None else None
    roman = None
    masters = {}
    for mp in styles_root.iter(S('master-page')):
        nm = mp.get(S('name'))
        fmt = layfmt.get(mp.get(S('page-layout-name')))
        masters[nm] = fmt
        if roman is None and (fmt or '').lower() == 'i':
            roman = nm
    arabic = None
    for root in (tmpl_root, styles_root):
        if root is None:
            continue
        for st in root.iter(S('style')):
            if st.get(S('family')) != 'paragraph':
                continue
            pp = st.find(S('paragraph-properties'))
            if pp is not None and pp.get(S('page-number')) == '1' \
                    and st.get(S('master-page-name')):
                arabic = st.get(S('master-page-name'))
                break
        if arabic:
            break
    if arabic is None:
        for nm, fmt in masters.items():
            if (fmt or '1').lower() not in ('i',):
                arabic = nm
                break
    return roman, arabic


def _page_switch_variant(auto_styles, base_style_name, master, page_number,
                         suffix=None):
    """Return the name of a paragraph auto-style identical to base_style_name but
    pinned to `master` (and, when page_number is given, restarting page numbers
    at it). base_style_name is usually one of the SW_* heading auto-styles set
    earlier in the merge (page break / TOC heading / chapter heading); we clone
    it so the heading keeps its own appearance and only the page master changes.
    A base that is a named style (e.g. 'Heading_20_1') yields a fresh child.
    `suffix` defaults to '_PgReset' (page_number given) or '_Roman' (roman-
    frontmatter master switch, no page_number) — pass an explicit suffix for
    any other kind of master switch (e.g. '_ChapterStart')."""
    if suffix is None:
        suffix = '_PgReset' if page_number else '_Roman'
    new_name = (base_style_name or _H1_PARENT) + suffix
    for child in auto_styles:
        if child.get(S('name')) == new_name:
            return new_name
    src = None
    for child in auto_styles:
        if child.get(S('name')) == base_style_name:
            src = child
            break
    if src is not None:
        se = copy.deepcopy(src)
    else:
        se = etree.Element(S('style'))
        se.set(S('family'),            'paragraph')
        se.set(S('parent-style-name'), base_style_name or _H1_PARENT)
    se.set(S('name'), new_name)
    se.set(S('master-page-name'), master)
    if page_number:
        pp = se.find(S('paragraph-properties'))
        if pp is None:
            pp = etree.SubElement(se, S('paragraph-properties'))
        pp.set(S('page-number'), page_number)
    auto_styles.append(se)
    return new_name


def apply_roman_frontmatter_odt(tmpl_text, tmpl_root, styles_root, reset_text,
                                new_page_headings):
    """Assign a roman master page to every level-1 heading before the reset
    heading and an arabic-restart master (style:page-number="1") to the reset
    heading — the ODF equivalent of the DOCX roman→arabic sectPr switch.

    reset_text — plain text of the heading where arabic page 1 begins
                 (from find_page_reset_index over the pandoc H1s).
    Runs AFTER the is-list-header pass so it can restore chapter counting on a
    numbered reset heading. Returns True when applied.
    """
    if not reset_text:
        return False
    roman_master, arabic_master = _detect_page_masters(styles_root, tmpl_root)
    if not roman_master or not arabic_master:
        print('WARNING: roman frontmatter requested but the template defines no '
              'roman/arabic master pages — leaving page numbering unchanged')
        return False
    auto = tmpl_root.find('.//' + O('automatic-styles'))
    if auto is None:
        return False

    h1s = [h for h in tmpl_text.iter(T('h'))
           if h.get(T('outline-level'), '') == '1']
    marker = strip_chapter_prefix(reset_text).strip().lower()
    full   = reset_text.strip().lower()
    reset_pos = None
    for i, h in enumerate(h1s):
        cand = {_elem_text(h).strip().lower(),
                strip_chapter_prefix(_elem_text(h)).strip().lower()}
        if full in cand or (marker and marker in cand):
            reset_pos = i
            break
    if reset_pos is None:
        return False

    for h in h1s[:reset_pos]:
        h.set(T('style-name'), _page_switch_variant(
            auto, _get_sn(h), roman_master, None))
        h.set(T('is-list-header'), 'true')
    rh = h1s[reset_pos]
    rh.set(T('style-name'), _page_switch_variant(
        auto, _get_sn(rh), arabic_master, '1'))
    if parse_chapter_number(reset_text) > 0:
        rh.attrib.pop(T('is-list-header'), None)   # numbered chapter: keep count
    else:
        rh.set(T('is-list-header'), 'true')
    return True


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

def _ensure_toc_heading_pb_style(auto_styles, parent=None, display_name=None):
    """Inject SW_TOCHeading_Pagebreak: inherits the TOC heading's OWN style +
    fo:break-before=page. Applying this to the TOC heading avoids the need
    for a separate blank spacer paragraph before it.

    `parent` defaults to the canonical _TOCHEADING_STYLE ('TOCHeading', as
    used by document.odt/article.odt) — but book.odt has no such style; its
    own TOC heading is styled after "Heading 1 - exclude from TOC" instead.
    merge_odt passes the ACTUAL template's own TOC-heading style here so a
    template that names it differently doesn't end up parented to an
    undefined style (silently rendered "Default Paragraph Style") and keeps
    the display name book.odt's own header <loext:style-ref> fields expect.
    """
    parent = parent or _TOCHEADING_STYLE
    for child in auto_styles:
        if child.get(S('name')) == _TOC_H_PB_STYLE:
            return
    style_el = etree.SubElement(auto_styles, S('style'))
    style_el.set(S('name'),               _TOC_H_PB_STYLE)
    style_el.set(S('family'),             'paragraph')
    style_el.set(S('parent-style-name'),  parent)
    if display_name:
        style_el.set(S('display-name'), display_name)
    pp = etree.SubElement(style_el, S('paragraph-properties'))
    pp.set(F('break-before'), 'page')

_TOF_H_PB_STYLE = 'SW_FigureIndexHeading_Pagebreak'

def _ensure_tof_heading_pb_style(auto_styles, parent=None, display_name=None):
    """Inject SW_FigureIndexHeading_Pagebreak: inherits the ToF heading's OWN
    style + fo:break-before=page, so the Table of Figures heading starts a
    new page without a separate spacer paragraph. See
    _ensure_toc_heading_pb_style for why `parent`/`display_name` are resolved
    per-template rather than hard-coded (book.odt styles its ToF heading
    after plain "Heading 1", not the canonical _TOF_HEADING_STYLE)."""
    parent = parent or _TOF_HEADING_STYLE
    for child in auto_styles:
        if child.get(S('name')) == _TOF_H_PB_STYLE:
            return
    style_el = etree.SubElement(auto_styles, S('style'))
    style_el.set(S('name'),              _TOF_H_PB_STYLE)
    style_el.set(S('family'),            'paragraph')
    style_el.set(S('parent-style-name'), parent)
    if display_name:
        style_el.set(S('display-name'), display_name)
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

def _update_meta(z_data, title, author, short_title=None, subtitle=None):
    """Update meta.xml dc:title, meta:initial-creator / dc:creator, and the
    'Short Title' / 'Subtitle' <meta:user-defined> custom properties (the ODF
    equivalent of DOCX's docProps/custom.xml 'Short Title'/'Subtitle',
    mirrored in sw_export_merge.normalize_headers_footers)."""
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
        for name, val in (('Author', author), ('Short Title', short_title),
                         ('Subtitle', subtitle)):
            if not val:
                continue
            for el in root.iter('{%s}user-defined' % META_NS):
                if el.get('{%s}name' % META_NS) == name:
                    el.text = val
                    break
        z_data['meta.xml'] = etree.tostring(
            root, xml_declaration=True, encoding='UTF-8', standalone=True)
    except Exception as e:
        print(f'WARNING: could not update meta.xml: {e}')


def _update_odt_field_placeholders(z_data, author, short_title):
    """Rewrite the cached text of every <text:user-defined text:name="Author"
    | "Short Title"> field in styles.xml's headers/footers, mirroring DOCX's
    normalize_headers_footers (which refreshes its DOCPROPERTY fldSimple
    caches the same way). book.odt's even-page header shows these via
    text:user-defined fields — one is text:fixed="true" ("Author"), which
    means it does NOT recompute from the underlying meta:user-defined
    property at all; both need their cached text rewritten directly, and the
    meta.xml property updated too (belt-and-suspenders, same as DOCX) so a
    manual field refresh in LibreOffice also shows the right value."""
    if 'styles.xml' not in z_data:
        return
    TEXT_NS = _NS['text']
    try:
        root = etree.fromstring(z_data['styles.xml'])
        changed = False
        for el in root.iter('{%s}user-defined' % TEXT_NS):
            name = el.get('{%s}name' % TEXT_NS)
            val = author if name == 'Author' else short_title if name == 'Short Title' else None
            if val and el.text != val:
                el.text = val
                changed = True
        if changed:
            z_data['styles.xml'] = etree.tostring(
                root, xml_declaration=True, encoding='UTF-8', standalone=True)
    except Exception as e:
        print(f'WARNING: could not update header/footer user-defined fields: {e}')

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
              toc=False, tof=False, short_title=None, basename=None,
              abstract=None, extra_sections=None,
              new_page_headings=True, restart_footnotes=True,
              generate_date=True, roman_frontmatter=False, page1_starts_with='',
              static_citations=False):
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
        title, subtitle, author, date_val, basename, generate_date=generate_date)

    # ── Extract template layout ────────────────────────────────────────────
    layout = extract_template_layout(template_path)

    # See "Named-style page mode" above uses_named_style_page_mode: True when
    # the template drives chapter page breaks + page-style switching from
    # Heading 1's OWN named style (book.odt as of 2026-09-05) rather than
    # needing this merge to synthesize per-paragraph auto-styles for it.
    _new_mode = uses_named_style_page_mode(styles_root)
    _frontmatter_style = _detect_frontmatter_heading_style(styles_root) if _new_mode else None

    # When the user asked for a table of figures but the template has no
    # <text:illustration-index>, borrow the index + heading from the bundled
    # document.odt and pull in the styles they reference.
    _tof_from_fallback = False
    if tof and layout['tof_element'] is None:
        try:
            _fb = extract_template_layout(bundled_template('document.odt'))
            if _fb['tof_element'] is not None:
                layout['tof_element'] = _fb['tof_element']
                layout['tof_heading'] = _fb['tof_heading']
                _tof_from_fallback = True
        except Exception as e:
            print(f'WARNING: could not load fallback ToF from document.odt: {e}')

    # Resolve the real style-name/display-name each page-break-variant style
    # should present as, PER THIS TEMPLATE, instead of assuming the canonical
    # document.odt/article.odt names — book.odt names its TOC heading style
    # "Heading 1 - exclude from TOC" and its ToF heading after plain
    # "Heading 1", not the generic 'TOCHeading'/'Figure_20_Index_20_Heading'.
    # Getting the display-name right also matters for LibreOffice's
    # <loext:style-ref> header fields (see _resolve_named_style_and_display).
    _, _h1_display_name = _resolve_named_style_and_display(
        tmpl_root, styles_root, _H1_PARENT)

    if layout['toc_heading'] is not None:
        _toc_parent, _toc_display_name = _resolve_named_style_and_display(
            tmpl_root, styles_root, _get_sn(layout['toc_heading']))
    else:
        _toc_parent, _toc_display_name = _TOCHEADING_STYLE, None

    if layout['tof_heading'] is not None:
        _tof_style = _get_sn(layout['tof_heading'])
        if _tof_from_fallback:
            # Borrowed from the bundled document.odt — resolve against ITS
            # OWN roots, not this (book.odt) template's.
            try:
                with zipfile.ZipFile(bundled_template('document.odt')) as _z:
                    _fb_content = etree.fromstring(_z.read('content.xml'))
                    _fb_styles = etree.fromstring(_z.read('styles.xml'))
                _tof_parent, _tof_display_name = _resolve_named_style_and_display(
                    _fb_content, _fb_styles, _tof_style)
            except Exception:
                _tof_parent, _tof_display_name = _TOF_HEADING_STYLE, None
        else:
            _tof_parent, _tof_display_name = _resolve_named_style_and_display(
                tmpl_root, styles_root, _tof_style)
    else:
        _tof_parent, _tof_display_name = _TOF_HEADING_STYLE, None

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

    # Resolve semantic-role style aliases (currently: the blockquote style)
    # against THIS template's own defined styles — parallel to the DOCX merge.
    # pandoc's ODT writer always emits 'Quotations' for a blockquote; a
    # template that instead defines its own differently-named equivalent
    # (book.odt has "Block_20_quote", converted from the DOCX original) gets
    # its own style. See sw_merge_helpers.STYLE_ALIASES.
    def _odt_style_display_name(style_el):
        return style_el.get(S('display-name')) or style_el.get(S('name'))

    _style_remap = dict(STYLE_REMAP['odt'])
    _extra_style_names_for_aliases = []
    if styles_root is not None:
        _defined_styles = [
            (st.get(S('name')), _odt_style_display_name(st))
            for st in styles_root.iter(S('style'))
            if st.get(S('family')) == 'paragraph'
        ]
        for _pandoc_name, (_candidates, _fallback) in STYLE_ALIASES['odt'].items():
            _target = resolve_style_alias(_defined_styles, _candidates, _fallback)
            _style_remap[_pandoc_name] = _target
            if _target == _fallback:
                # Nothing in the template matched — make sure the fallback
                # style actually exists (borrowed from document.odt).
                _extra_style_names_for_aliases.append(_fallback)

    # ── Classify pandoc body ───────────────────────────────────────────────
    body_elements = classify_pandoc_body(pdc_text, style_remap=_style_remap)

    # Roman-numeral frontmatter: locate the heading where arabic "page 1"
    # begins, using the SAME shared decision as the DOCX merge. The switch
    # itself is applied the ODF way (master pages) after assembly.
    _reset_text = None
    if roman_frontmatter:
        _h1_texts = [_elem_text(el) for el in body_elements
                     if el.tag == T('h') and el.get(T('outline-level'), '') == '1']
        _ridx = find_page_reset_index(_h1_texts, page1_starts_with)
        if _ridx is not None:
            _reset_text = _h1_texts[_ridx]
        else:
            print('Roman frontmatter requested but no matching start heading '
                  'found — exporting with arabic page numbering throughout')

    # ── Figure captions (shared walk with the DOCX merge) ──────────────────
    # Chapter-scoped numbering (Figure C.N) follows the footnote-restart
    # setting; otherwise captions are 'Figure N'.
    def _odt_caption(desc, number, ordinal):
        return _make_figure_caption(desc, number, ordinal, restart_footnotes,
                                    style=layout['caption_style'])
    _odt_alttext = (_make_alttext_para if layout['has_alttext_style'] else None)
    body_elements, has_figures, _ = process_figures(
        body_elements,
        get_style=_get_sn, get_text=_elem_text,
        set_body_style=lambda el: el.set(T('style-name'), _BODY_STYLE),
        is_heading1=lambda el: el.tag == T('h')
                    and el.get(T('outline-level'), '') == '1',
        image_styles={_FIGIMAGE_STYLE},
        caption_styles={_FIGCAPTION_STYLE},
        body_styles={_BODY_STYLE, 'First_20_paragraph'},
        make_caption=_odt_caption, make_alttext=_odt_alttext,
        chapter_scoped=restart_footnotes)

    if static_citations:
        _flatten_caption_fields(body_elements,
                                {layout['caption_style'], _FIGCAPTION_STYLE})

    # ── Merge pandoc auto-styles into template (before moving elements) ────
    name_map = _merge_auto_styles(tmpl_root, pdc_root, styles_root)
    _rewrite_style_refs(body_elements, name_map)

    # ── Apply page breaks + chapter numbering ───────────────────────────────
    # Named-style mode (see uses_named_style_page_mode): one combined pass —
    # chapter/frontmatter/reset-heading role all decide the same style
    # assignment, so there's no separate page-break step. Otherwise, the
    # original two independent passes (clone-based page breaks, then
    # ODF-outline-numbering-based chapter numbering).
    _chaptered = False
    if _new_mode:
        _chaptered = apply_heading_roles_named_style_mode(
            body_elements, tmpl_root, styles_root, _frontmatter_style,
            _reset_text)
    else:
        if new_page_headings:
            apply_page_breaks(body_elements, tmpl_root, display_name=_h1_display_name)
        # Strip the literal "Chapter N:" prefix; ODF chapter numbering supplies
        # "Chapter N." (parallel to the DOCX numPr) and makes figure captions
        # chapter-scoped. Runs AFTER apply_page_breaks so it can override the
        # page-break style on numbered chapters. document/article templates
        # carry no chapter_number_format → left untouched (≈ DOCX chapter_numid).
        if layout['chapter_number_format']:
            _chaptered = apply_chapter_numbering_odt(
                body_elements, tmpl_root, new_page_headings,
                display_name=_h1_display_name,
                direct_list_style=bool(layout['chapter_number_format'].get('direct')),
                chapter_master=layout.get('chapter_master'))
    if _chaptered and layout['chapter_number_format'] and styles_root is not None:
        _enable_chapter_outline_numbering(
            styles_root, layout['chapter_number_format'])
        z_data['styles.xml'] = etree.tostring(
            styles_root, xml_declaration=True, encoding='UTF-8',
            standalone=True)

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
        if not _new_mode and new_page_headings:
            auto = tmpl_root.find('.//' + O('automatic-styles'))
            if auto is not None:
                _ensure_toc_heading_pb_style(auto, parent=_toc_parent,
                                             display_name=_toc_display_name)
        if layout['toc_heading'] is not None:
            toc_h = copy.deepcopy(layout['toc_heading'])
            if _new_mode:
                # Named-style mode: keep the TOC heading's OWN style (its
                # page break/master switch is baked into that style already —
                # book.odt's "Heading 1 - exclude from TOC"). Only exclude it
                # from the outline chapter count when numbering is active.
                if layout['chapter_number_format'] and toc_h.tag == T('h'):
                    toc_h.set(T('is-list-header'), 'true')
            elif new_page_headings:
                toc_h.set(T('style-name'), _TOC_H_PB_STYLE)
            tmpl_text.append(toc_h)
        elif new_page_headings and not _new_mode:
            # No standalone heading in template — fall back to a blank spacer.
            tmpl_text.append(make_toc_pagebreak_para(tmpl_root))
        _toc_el = copy.deepcopy(layout['toc_element'])
        _rebuild_index_body(
            _toc_el,
            _toc_entries(body_elements, _toc_depth(_toc_el),
                         layout['chapter_number_format'] if _chaptered else None),
            lambda lvl: 'Contents_20_%d' % lvl)
        tmpl_text.append(_toc_el)
    elif toc and layout['toc_element'] is None:
        # Template has no TOC element (unusual). Insert a minimal TOC heading.
        print('WARNING: template has no TOC element; inserting a bare heading.')
        if not _new_mode and new_page_headings:
            auto = tmpl_root.find('.//' + O('automatic-styles'))
            if auto is not None:
                _ensure_toc_heading_pb_style(auto, parent=_toc_parent,
                                             display_name=_toc_display_name)
        h = etree.Element(T('h'))
        if _new_mode:
            h.set(T('style-name'), _frontmatter_style or _H1_PARENT)
            if layout['chapter_number_format']:
                h.set(T('is-list-header'), 'true')
        else:
            h.set(T('style-name'), _TOC_H_PB_STYLE if new_page_headings else _H1_PARENT)
        h.set(T('outline-level'), '1')
        h.text = 'Table of Contents'
        tmpl_text.append(h)

    # 2b. Table of Figures — heading + <text:illustration-index>, when the user
    #     asked for it and the document has figures. The index element (and its
    #     heading + styles) come from the template, or from the bundled
    #     document.odt when the template has none.
    if tof and has_figures and layout['tof_element'] is not None:
        if not _new_mode and new_page_headings:
            auto = tmpl_root.find('.//' + O('automatic-styles'))
            if auto is not None:
                _ensure_tof_heading_pb_style(auto, parent=_tof_parent,
                                             display_name=_tof_display_name)
        if layout['tof_heading'] is not None:
            tof_h = copy.deepcopy(layout['tof_heading'])
            if _new_mode:
                # Named-style mode: keep its OWN style (book.odt's ToF
                # heading is "Heading 1 Frontmatter" — already the right
                # page style), just exclude it from the chapter count.
                if layout['chapter_number_format'] and tof_h.tag == T('h'):
                    tof_h.set(T('is-list-header'), 'true')
            elif new_page_headings:
                tof_h.set(T('style-name'), _TOF_H_PB_STYLE)
            tmpl_text.append(tof_h)
        else:
            th = etree.Element(T('p'))
            if _new_mode:
                th.set(T('style-name'), _frontmatter_style or _H1_PARENT)
            else:
                th.set(T('style-name'),
                       _TOF_H_PB_STYLE if new_page_headings else _TOF_HEADING_STYLE)
            th.text = 'Table of Figures'
            tmpl_text.append(th)
        _tof_el = copy.deepcopy(layout['tof_element'])
        _rebuild_index_body(
            _tof_el,
            _tof_entries(body_elements, {layout['caption_style'], _FIGCAPTION_STYLE}),
            lambda lvl: 'Figure_20_Index_20_1')
        tmpl_text.append(_tof_el)

    # 3. Strip the bibliography section (pandoc plain-text entries) so a fresh
    #    Zotero section can be appended at the end.  Mirrors the DOCX approach:
    #    both formats detect and strip via the shared strip_bibliography helper,
    #    then each appends its own format-specific Zotero bibliography section.
    #
    #    In static_citations mode (PDF path — see DocumentCompiler.py), pandoc's
    #    own --citeproc already produced a real, populated bibliography (heading
    #    + rendered entries, always positioned last, same as here) — there's no
    #    live field to refresh, so it's left as ordinary body content instead of
    #    being stripped and replaced with the "Refresh Zotero" placeholder. It
    #    gets the same Heading_20_1 style pandoc gives any other heading, so it
    #    flows through the general heading-remapping/chapter-exclusion logic
    #    below exactly like a normal section.
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

    if static_citations:
        _has_bibliography = False
        # Pandoc's citeproc wraps bibliography entries in <text:p
        # style-name="First_20_paragraph"> inside nested <text:section> — and
        # a template may style "First paragraph" distinctively (book.odt gives
        # it a yellow background). Normalise those entries to plain body text.
        for _el in body_elements:
            if _el.tag == T('section'):
                for _p in _el.iter(T('p')):
                    if _p.get(T('style-name')) in (
                            'First_20_paragraph', 'Text_20_body', None):
                        _p.set(T('style-name'), _BODY_STYLE)
    else:
        body_elements, _has_bibliography = strip_bibliography(
            body_elements, _bibl_heading_text, _is_bibl_entry)

    # 4. Pandoc body elements (moved from pdc_text to tmpl_text).
    for el in body_elements:
        tmpl_text.append(el)

    # 5. Fresh Zotero bibliography section, appended at the end when the pandoc
    #    output contained a bibliography.  Uses SW_Heading1_Pagebreak so the
    #    heading starts on a new page (same as all other Heading 1 elements).
    if _has_bibliography:
        if _new_mode:
            _bibl_style = _H1_PARENT
        else:
            _bibl_style = _H1_PB_STYLE if new_page_headings else _H1_PARENT
            if new_page_headings:
                auto = tmpl_root.find('.//' + O('automatic-styles'))
                if auto is not None:
                    _ensure_h1_pagebreak_style(auto, _h1_display_name)
        _bh = etree.Element(T('h'))
        _bh.set(T('style-name'), _bibl_style)
        _bh.set(T('outline-level'), '1')
        if _new_mode and layout['chapter_number_format']:
            _bh.set(T('is-list-header'), 'true')
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

    # When the ToF was borrowed from document.odt, make sure the styles its
    # entry template references exist in this template.
    if _tof_from_fallback and 'styles.xml' in z_data:
        try:
            with zipfile.ZipFile(bundled_template('document.odt')) as _z:
                _src = _z.read('styles.xml')
            z_data['styles.xml'] = ensure_odt_styles(
                z_data['styles.xml'],
                ['Figure_20_Index_20_Heading', 'Figure_20_Index_20_1',
                 'Index_20_Link'],
                _src)
        except Exception as e:
            print(f'WARNING: could not inject ToF styles: {e}')

    # Same for a style alias fallback (e.g. no blockquote-equivalent style
    # found in the template) — borrow the bundled document.odt's version.
    if _extra_style_names_for_aliases and 'styles.xml' in z_data:
        try:
            with zipfile.ZipFile(bundled_template('document.odt')) as _z:
                _src = _z.read('styles.xml')
            z_data['styles.xml'] = ensure_odt_styles(
                z_data['styles.xml'], _extra_style_names_for_aliases, _src)
        except Exception as e:
            print(f'WARNING: could not inject style-alias fallback styles: {e}')

    # With ODF chapter numbering on, exclude every non-chapter level-1 heading
    # (TOC, ToF, Bibliography, and the frontmatter headings marked earlier) from
    # the chapter count so the first numbered chapter is "Chapter 1", not
    # "Chapter 4". Named-style mode already marked every non-chapter heading
    # is-list-header at assignment time (its chapters share Heading_20_1 with
    # everything else, so this style-name-based sweep can't tell them apart
    # the way the SW_Chapter_Heading clone let it in the old mode).
    if _chaptered and not _new_mode:
        for h in tmpl_text.iter(T('h')):
            if h.get(T('outline-level'), '') == '1' \
                    and _get_sn(h) != _CHAPTER_H_STYLE:
                h.set(T('is-list-header'), 'true')

    # Roman-numeral frontmatter page-numbering switch (ODF master pages).
    # Runs last so it can restore chapter counting on a numbered reset heading
    # after the is-list-header pass above. Named-style mode already assigned
    # the reset heading's master + page-number restart as part of
    # apply_heading_roles_named_style_mode above.
    if _reset_text and not _new_mode:
        if apply_roman_frontmatter_odt(tmpl_text, tmpl_root, styles_root,
                                       _reset_text, new_page_headings):
            print(f'ODT: roman frontmatter → arabic restart at "{_reset_text}"')
    elif _reset_text and _new_mode:
        print(f'ODT: roman frontmatter → arabic restart at "{_reset_text}" (named-style mode)')

    # Cap images to template text area dimensions, preserving aspect ratio.
    n_scaled = resize_images(tmpl_root, 'odt', template_zip_data=z_data)
    if n_scaled:
        print(f'ODT: capped {n_scaled} image(s) to text area')

    # ── Serialize updated content.xml ──────────────────────────────────────
    z_data['content.xml'] = etree.tostring(
        tmpl_root, xml_declaration=True, encoding='UTF-8', standalone=True)

    # ── Merge pandoc media files + manifest entries ────────────────────────
    _merge_media(z_data, pdc_data)
    _merge_manifest(z_data, pdc_data)

    # ── Update document metadata ───────────────────────────────────────────
    _update_meta(z_data, title, author, short_title=short_title, subtitle=subtitle)
    _update_odt_field_placeholders(z_data, author, short_title)

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
    ap.add_argument('--list-of-figures', action='store_true', dest='tof',
                    help='Include a table of figures (only when the doc has figures)')
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
    ap.add_argument('--no-generated-date', action='store_true',
                    dest='no_generated_date',
                    help="Don't insert today's date when the doc has no date property")
    ap.add_argument('--roman-frontmatter', action='store_true',
                    dest='roman_frontmatter',
                    help='Roman-numeral page numbers for frontmatter, switching '
                         'to arabic (page 1) at the first main-body heading')
    ap.add_argument('--page1-starts-with', default='', dest='page1_starts_with',
                    help='Heading text where arabic "page 1" begins (default: '
                         'first Introduction/Chapter 1/numbered heading)')
    ap.add_argument('--static-citations', action='store_true',
                    dest='static_citations',
                    help='Pandoc already rendered final citations + a real '
                         'bibliography via --citeproc (PDF path) — preserve '
                         'it instead of stripping and replacing with a live '
                         'Zotero field placeholder')
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
        date_val=args.date_val, toc=args.toc, tof=args.tof,
        short_title=args.shorttitle,
        basename=args.basename, abstract=args.abstract,
        extra_sections=extra_sections,
        new_page_headings=new_page_headings,
        restart_footnotes=restart_footnotes,
        generate_date=not args.no_generated_date,
        roman_frontmatter=args.roman_frontmatter,
        page1_starts_with=args.page1_starts_with,
        static_citations=args.static_citations,
    )
    print(f'Merged: {args.output}')

if __name__ == '__main__':
    main()
