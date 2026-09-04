"""sw_merge_helpers.py — shared helpers for the ScholarWeave export merge.

Contains both OOXML-level helpers (used by sw_export_merge.py) and
format-agnostic helpers used by both merge scripts.
"""

# ── Zotero bibliography field instruction ────────────────────────────────────

#: Default Zotero bibliography field instruction, shared by both merge scripts
#: so DOCX and ODT use an identical string (Zotero reads both).
ZOTERO_BIBL_INSTR = (
    'ADDIN ZOTERO_BIBL {"uncited":[],"omittedItems":[],"custom":[]} CSL_BIBLIOGRAPHY'
)

import copy
import datetime
import os
import random
import re
from lxml import etree


def bundled_template(name):
    """Absolute path to a bundled Export Template (…/scripts/../templates/<name>).
    Used as the canonical source when the user's template lacks a structure we
    need to synthesize (e.g. a Table of Figures)."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '..', 'templates', name)


def ensure_docx_styles(styles_bytes, needed_ids, source_styles_bytes):
    """Return word/styles.xml bytes with any of `needed_ids` that are missing
    copied verbatim from `source_styles_bytes` (a known-good template). Pulls in
    a one-level basedOn parent if it is also missing. Unchanged when nothing is
    needed."""
    W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    def wt(n): return '{%s}%s' % (W, n)
    root = etree.fromstring(styles_bytes)
    have = {s.get(wt('styleId')) for s in root.findall(wt('style'))}
    if all(i in have for i in needed_ids):
        return styles_bytes
    src_by_id = {s.get(wt('styleId')): s
                 for s in etree.fromstring(source_styles_bytes).findall(wt('style'))}
    added = set()
    def _add(sid):
        if sid in have or sid in added or sid not in src_by_id:
            return
        st = copy.deepcopy(src_by_id[sid])
        based = st.find(wt('basedOn'))
        if based is not None:
            _add(based.get(wt('val')))
        root.append(st)
        added.add(sid)
    for sid in needed_ids:
        _add(sid)
    return etree.tostring(root, xml_declaration=True, encoding='UTF-8',
                          standalone=True)


def ensure_odt_styles(styles_bytes, needed_names, source_styles_bytes):
    """Return styles.xml bytes with any of `needed_names` that are missing copied
    verbatim from `source_styles_bytes` into <office:styles>. Pulls in a
    one-level parent-style-name if it is also missing. Unchanged when nothing is
    needed."""
    S = 'urn:oasis:names:tc:opendocument:xmlns:style:1.0'
    O = 'urn:oasis:names:tc:opendocument:xmlns:office:1.0'
    def st(n): return '{%s}%s' % (S, n)
    root = etree.fromstring(styles_bytes)
    office_styles = root.find('{%s}styles' % O)
    if office_styles is None:
        return styles_bytes
    have = {e.get(st('name')) for e in root.iter(st('style'))}
    if all(n in have for n in needed_names):
        return styles_bytes
    src_by_name = {e.get(st('name')): e
                   for e in etree.fromstring(source_styles_bytes).iter(st('style'))}
    added = set()
    def _add(name):
        if name in have or name in added or name not in src_by_name:
            return
        el = copy.deepcopy(src_by_name[name])
        parent = el.get(st('parent-style-name'))
        if parent:
            _add(parent)
        office_styles.append(el)
        added.add(name)
    for name in needed_names:
        _add(name)
    return etree.tostring(root, xml_declaration=True, encoding='UTF-8',
                          standalone=True)


# ── pandoc → template style remaps (shared) ──────────────────────────────────

#: Pandoc emits generic paragraph styles (a "first paragraph" variant, a
#: plain/default paragraph style, its own block-quote style) that need to be
#: mapped onto the export template's named styles so body text is visually
#: consistent.  Both merge scripts do this; the names differ only because DOCX
#: uses OOXML style IDs and ODT uses ODF-encoded style names, so the two tables
#: live here side by side rather than being reinvented in each script.
STYLE_REMAP = {
    'docx': {
        'Blockquote':     'BlockText',
        'FirstParagraph': 'BodyText',
    },
    'odt': {
        'First_20_paragraph':            'Text_20_body',
        'Default_20_Paragraph_20_Style': 'Text_20_body',
        'Default Paragraph Style':       'Text_20_body',
        'Block_20_Text':                 'Quotations',
    },
}


# ── cover-value resolution + text helpers (shared) ──────────────────────────

# Words that stay lowercase in title-case: articles, coordinating conjunctions,
# short prepositions, and the infinitive marker 'to'.
_TITLE_CASE_LOWER = frozenset({
    'a', 'an', 'the',
    'and', 'but', 'or', 'nor', 'for', 'yet', 'so',
    'as', 'at', 'by', 'in', 'of', 'on', 'to', 'up',
    'via', 'per',
})


def title_case(key):
    """Convert a YAML key (e.g. 'sw-note-to-readers') to display title case.
    Strips a leading 'sw-' prefix, replaces hyphens with spaces, and capitalises
    each word except small prepositions/conjunctions/articles (and 'to'), unless
    that word is first in the phrase.

        'note'               → 'Note'
        'sw-alert'           → 'Alert'
        'sw-note-to-readers' → 'Note to Readers'

    Used by both merge scripts for note/sw-* section headings.
    """
    key = re.sub(r'^sw-', '', key)
    words = key.split('-')
    result = []
    for idx, word in enumerate(words):
        if idx == 0 or word.lower() not in _TITLE_CASE_LOWER:
            result.append(word.capitalize())
        else:
            result.append(word.lower())
    return ' '.join(result)


def strip_markdown(text):
    """Remove markdown delimiters (*italic*, **bold**, `code`) from a string,
    keeping the content. Used for plain-text-only targets (docProps, meta.xml)
    by both merge scripts."""
    if not text:
        return text or ''
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'_([^_]+)_', r'\1', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    return text


#: Heading 1 text (case-insensitive, anchored) that begins main matter.
_MAIN_START_RE = re.compile(
    r'^(introduction|chapter\s+\d+|prologue|part\s+\d+)\b', re.IGNORECASE
)


def is_main_start(text):
    """True when a Heading 1's text marks the start of main matter (Introduction,
    a numbered chapter, Prologue, a numbered part). Everything before the first
    such heading is frontmatter. Shared by both merge scripts so the
    frontmatter/main boundary is defined once (drives roman→arabic page
    numbering in the DOCX merge; will drive the format-agnostic page-numbering
    feature once written)."""
    return bool(_MAIN_START_RE.match(text.strip()))


def is_toc_heading(text):
    """True when a Heading 1's text is the Table of Contents heading."""
    return text.strip().lower() == 'table of contents'


def is_tof_heading(text):
    """True when a Heading 1's text is the Table of Figures heading."""
    return text.strip().lower() == 'table of figures'


def resolve_cover(title, subtitle, author, date_val, basename):
    """Resolve cover values per spec, identically for DOCX and ODT:
      Title    = whole 'title' property when a 'subtitle' property is given
                 (e.g. "Title: A Study of Important Things" stays whole, so the
                 document can have subtitle "A manuscript submitted to
                 University Press"); otherwise before ':' → whole title →
                 basename before '-'/'–' → full basename
      Subtitle = 'subtitle' property → (else) after ':' of title → none
      Author   = 'author' property → "Joseph Hill"
      Date     = current date "Month DD, YYYY"
    """
    if title:
        if subtitle is not None:
            # Subtitle property given: Title stays the WHOLE title property.
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


# ── format-agnostic helpers ───────────────────────────────────────────────────

def split_paragraphs(text):
    """Split a text value (e.g. a YAML abstract) into a list of non-empty
    paragraph strings, splitting on blank lines (\\n\\n).

    Returns a list of at least one string when text is non-empty, or [] when
    text is None or blank.  Both merge scripts (DOCX and ODT) use this so
    multi-paragraph abstract and extra-section values are handled identically.
    """
    if not text:
        return []
    return [p.strip() for p in text.split('\n\n') if p.strip()]

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
W14 = 'http://schemas.microsoft.com/office/word/2010/wordml'
XML = 'http://www.w3.org/XML/1998/namespace'


def tag(n):
    return '{%s}%s' % (W, n)


def w14(n):
    return '{%s}%s' % (W14, n)


def get_style(p):
    """Return the pStyle val of a paragraph, or None."""
    pPr = p.find(tag('pPr'))
    if pPr is None:
        return None
    pStyle = pPr.find(tag('pStyle'))
    if pStyle is None:
        return None
    return pStyle.get(tag('val'))


def set_style(p, style_name):
    """Set (or create) the pStyle val on a paragraph."""
    pPr = p.find(tag('pPr'))
    if pPr is None:
        pPr = etree.Element(tag('pPr'))
        p.insert(0, pPr)
    pStyle = pPr.find(tag('pStyle'))
    if pStyle is None:
        pStyle = etree.Element(tag('pStyle'))
        pPr.insert(0, pStyle)
    pStyle.set(tag('val'), style_name)


def collect_ids(doc):
    """Return a set of all w14:paraId values (upper-cased) in a document tree."""
    ids = set()
    for el in doc.iter():
        pid = el.get(w14('paraId'))
        if pid:
            ids.add(pid.upper())
    return ids


def mint_id(used):
    """Return a unique 8-hex paraId not in `used`, and add it to `used`."""
    while True:
        c = '%08X' % random.randint(0x10000000, 0xFFFFFFFE)
        if c not in used:
            used.add(c)
            return c


def ensure_para_id(p, used):
    """
    If p already has a paraId that is not in `used`, register it and return.
    Otherwise mint a new one and assign it (along with textId="77777777").
    """
    pid = p.get(w14('paraId'))
    if pid:
        up = pid.upper()
        if up not in used:
            used.add(up)
            return
    new_id = mint_id(used)
    p.set(w14('paraId'), new_id)
    p.set(w14('textId'), '77777777')


def strip_bibliography(elements, heading_text_fn, is_bibl_entry_fn):
    """Find and remove the bibliography section from a flat list of pandoc body
    elements.  Uses find_bibliography_range to locate the heading and its
    plain-text entries, then removes them both.

    Returns (cleaned_elements, True) when a bibliography was found and removed,
    or (list(elements), False) when not found.

    Both merge scripts call this so pandoc's plain-text bibliography is stripped
    before a fresh format-specific Zotero bibliography section is appended at
    the document's end — giving identical behaviour across DOCX and ODT.
    """
    h_idx, s_idx, e_idx = find_bibliography_range(elements, heading_text_fn, is_bibl_entry_fn)
    if h_idx is None:
        return list(elements), False
    return list(elements[:h_idx]) + list(elements[e_idx:]), True


def find_bibliography_range(elements, heading_text_fn, is_bibl_entry_fn):
    """Locate a bibliography section (heading + entry paragraphs) in a sequence.

    Scans *elements* for the first element whose heading_text_fn() returns a
    string containing 'bibliography' (case-insensitive), then collects all
    immediately following elements for which is_bibl_entry_fn() returns True.

    heading_text_fn(el) → str or None
        Return the element's plain text if it is a heading, else None.
    is_bibl_entry_fn(el) → bool
        Return True if the element is a bibliography entry paragraph.

    Returns (heading_idx, entry_start, entry_end) where the entries occupy
    elements[entry_start:entry_end], or (None, None, None) when not found.
    Both DOCX and ODT merge scripts use this so bibliography detection logic
    lives in one place.
    """
    for i, el in enumerate(elements):
        text = heading_text_fn(el)
        if text is None:
            continue
        if 'bibliography' not in text.lower():
            continue
        j = i + 1
        while j < len(elements) and is_bibl_entry_fn(elements[j]):
            j += 1
        if j > i + 1:
            return i, i + 1, j
    return None, None, None

# ── figure captions (shared for DOCX and ODT) ────────────────────────────────

#: A caption line begins with the word "Figure" (the vault convention is a
#: paragraph "Figure. <desc>" or "Figure N. <desc>" right after an image embed).
_FIGURE_CAPTION_RE = re.compile(r'^\s*figure\b', re.IGNORECASE)

#: Leading "Figure" / "Figure." / "Figure 3." / "Figure 2.4:" token to strip so
#: the merge can supply its own (computed) number. Requires the word "Figure"
#: followed by an optional number and/or a separator — plain "Figures of speech"
#: is left alone (\b stops "figure" matching inside "figures").
_FIGURE_PREFIX_RE = re.compile(
    r'^\s*figure\b[ \t]*\d*(?:[.:]\d+)*[ \t]*[.:]?[ \t]*', re.IGNORECASE)

#: An "Alt-text: <text>" line following a caption.
_ALT_TEXT_RE = re.compile(r'^\s*alt[- ]?text\s*[:.\-]?\s*(.*)$',
                          re.IGNORECASE | re.DOTALL)


def looks_like_caption(text):
    """True when a paragraph's text begins with the word 'Figure'."""
    return bool(_FIGURE_CAPTION_RE.match(text or ''))


def strip_figure_prefix(text):
    """Strip a leading 'Figure' / 'Figure.' / 'Figure 3.' / 'Figure 2.4:' token
    from a caption line, returning just the description. Any number in the
    original is discarded — the merge computes the real figure number."""
    return _FIGURE_PREFIX_RE.sub('', text or '', count=1).strip()


#: A Heading 1 that begins with a literal chapter number: "Chapter 3: Title",
#: "3. Title", "3) Title". Group 1 is the title without the prefix.
_CHAPTER_PREFIX_RE = re.compile(
    r'^\s*(?:chapter\s+)?(\d+)\s*[.):]?\s+(.*)$', re.IGNORECASE | re.DOTALL)


def parse_chapter_number(text):
    """Return the leading chapter number of a Heading 1's text ('Chapter 3: X'
    or '3. X' → 3), or 0 when the heading carries no number (Preface,
    Introduction, Conclusion, …). Used to compute chapter-scoped figure
    numbers identically for DOCX and ODT."""
    m = re.match(r'^\s*(?:chapter\s+)?(\d+)\b', text or '', re.IGNORECASE)
    return int(m.group(1)) if m else 0


def strip_chapter_prefix(text):
    """Strip a leading 'Chapter N:' / 'N.' / 'N)' chapter-number prefix from a
    Heading 1's text, returning the bare title. Unchanged when there is no such
    prefix. Shared so DOCX (which then supplies the number via Word numPr) and
    ODT strip the literal prefix identically."""
    m = _CHAPTER_PREFIX_RE.match(text or '')
    if m and m.group(2).strip():
        return m.group(2).strip()
    return text or ''


def process_figures(elements, *, get_style, get_text, set_body_style,
                    is_heading1, image_styles, caption_styles, body_styles,
                    make_caption, make_alttext, chapter_scoped, start_state=None):
    """Walk a flat list of body paragraphs and turn pandoc's figure blocks into
    the export template's caption layout. Shared by DOCX and ODT — the walk
    (finding the vault 'Figure. …' caption line after an image, discarding
    pandoc's auto filename caption, stripping any existing number, computing the
    real number, consuming a trailing 'Alt-text: …' line, chapter tracking,
    has_figures detection) lives here; the two make_* callbacks build the
    format-specific caption / alt-text elements.

    start_state : opaque tuple from a previous call's return, so a caller that
                  processes the body in several chunks (the DOCX merge works on
                  a list of sections) keeps continuous figure numbering across
                  them. Returns (new_elements, has_figures, end_state).

    Parameters
    ----------
    elements        : list — body paragraphs (mutated copies are fine)
    get_style(el)   : -> str  paragraph style name ('' if none)
    get_text(el)    : -> str  concatenated text, stripped
    set_body_style(el)        : restyle an image paragraph to the template body style
    is_heading1(el) : -> bool
    image_styles    : set — pandoc's image-paragraph styles
    caption_styles  : set — pandoc's auto caption styles (filename; discarded)
    body_styles     : set — body-text styles (for the caption + alt-text lines)
    make_caption(desc, number_str, ordinal) : -> element  (ordinal is 1-based)
    make_alttext(alt_text_or_None)          : -> element or None; pass None to
                        skip alt-text paragraphs entirely (template lacks the style)
    chapter_scoped  : bool — True → number 'C.N' (book); False → 'N' (sequential)

    Returns (new_elements, has_figures, end_state).
    """
    out = []
    i = 0
    n = len(elements)
    chapter, fig_in_chapter, fig_global = start_state or (0, 0, 0)
    has_figures = False

    while i < n:
        el = elements[i]
        if is_heading1(el):
            chapter = parse_chapter_number(get_text(el))
            fig_in_chapter = 0
            out.append(el)
            i += 1
            continue

        if get_style(el) in image_styles:
            set_body_style(el)
            out.append(el)
            has_figures = True
            i += 1
            # Discard pandoc's auto caption paragraph (holds the image filename).
            fallback_desc = None
            if i < n and get_style(elements[i]) in caption_styles:
                fallback_desc = strip_figure_prefix(get_text(elements[i]))
                i += 1
            # The real caption is the vault's "Figure. …" line, if present.
            desc = None
            if i < n and get_style(elements[i]) in body_styles \
                    and looks_like_caption(get_text(elements[i])):
                desc = strip_figure_prefix(get_text(elements[i]))
                i += 1
            elif fallback_desc:
                desc = fallback_desc
            if desc is None:
                continue  # image with no caption — leave it uncaptioned

            fig_in_chapter += 1
            fig_global += 1
            number = ('%d.%d' % (chapter, fig_in_chapter) if chapter_scoped
                      else str(fig_global))
            out.append(make_caption(desc, number, fig_global))

            alt = None
            if i < n and get_style(elements[i]) in body_styles:
                m = _ALT_TEXT_RE.match(get_text(elements[i]))
                if m:
                    alt = m.group(1).strip() or None
                    i += 1
            if make_alttext is not None:
                node = make_alttext(alt)
                if node is not None:
                    out.append(node)
            continue

        out.append(el)
        i += 1

    return out, has_figures, (chapter, fig_in_chapter, fig_global)

# ── image sizing (shared for DOCX and ODT) ────────────────────────────────────

def resize_images(doc_root, format_name, template_zip_data=None):
    """Cap images to fit within the template's text area, preserving aspect ratio.
    Images that exceed the text width or height are scaled down proportionally.
    Images smaller than the text area are left at their native size.

    doc_root          — lxml Element: tmpl_doc (DOCX) or content.xml root (ODT)
    format_name       — 'docx' or 'odt'
    template_zip_data — dict {filename: bytes}; required for ODT (styles.xml);
                        unused for DOCX (geometry is read from doc_root's sectPr)

    Returns the count of image elements that were scaled (0 = nothing changed).
    Both DOCX and ODT are handled in one body so that any change to sizing
    logic (what to cap, how to preserve aspect ratio) is automatically applied
    to both formats.
    """
    if format_name == 'docx':
        # ── DOCX: all dimensions in EMU (914400 per inch) ─────────────────────
        WNS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        WP  = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
        A   = 'http://schemas.openxmlformats.org/drawingml/2006/main'
        TWIPS_TO_EMU = 635  # 1440 twips/inch ÷ 914400 EMU/inch ≈ 635 EMU/twip

        def _w(n): return '{%s}%s' % (WNS, n)

        # Read page geometry from sectPr in the merged document body.
        # tmpl_doc always carries the template's final sectPr after build_body.
        # Footnote XML has no sectPr — falls back to A4 text area.
        MM_TO_EMU = 914400 / 25.4
        text_w = int(160 * MM_TO_EMU)   # A4 fallback: 160 mm text width
        text_h = int(247 * MM_TO_EMU)   # A4 fallback: 247 mm text height
        sect = doc_root.find('.//' + _w('sectPr'))
        if sect is not None:
            pgsz  = sect.find(_w('pgSz'))
            pgmar = sect.find(_w('pgMar'))
            if pgsz is not None and pgmar is not None:
                try:
                    pg_w  = int(pgsz.get(_w('w'),      '0') or '0')
                    pg_h  = int(pgsz.get(_w('h'),      '0') or '0')
                    mar_l = int(pgmar.get(_w('left'),   '0') or '0')
                    mar_r = int(pgmar.get(_w('right'),  '0') or '0')
                    mar_t = int(pgmar.get(_w('top'),    '0') or '0')
                    mar_b = int(pgmar.get(_w('bottom'), '0') or '0')
                    if pg_w > 0:
                        text_w = (pg_w - mar_l - mar_r) * TWIPS_TO_EMU
                    if pg_h > 0:
                        text_h = (pg_h - mar_t - mar_b) * TWIPS_TO_EMU
                except (ValueError, TypeError):
                    pass

        # Scale all three extent element types — wp:extent and a:extent/a:ext
        # must agree (Word uses wp:extent for rendered size; a:extent is what
        # some inspectors and LibreOffice read).
        changed = 0
        for el in doc_root.iter():
            if el.tag not in (
                '{%s}extent' % WP, '{%s}extent' % A, '{%s}ext' % A,
            ):
                continue
            try:
                cx = int(el.get('cx', 0) or 0)
                cy = int(el.get('cy', 0) or 0)
            except (ValueError, TypeError):
                continue
            if cx <= 0 or cy <= 0:
                continue
            new_cx, new_cy = cx, cy
            if new_cx > text_w:
                new_cy = int(round(new_cy * text_w / new_cx))
                new_cx = text_w
            if new_cy > text_h:
                new_cx = int(round(new_cx * text_h / new_cy))
                new_cy = text_h
            if new_cx != cx or new_cy != cy:
                el.set('cx', str(new_cx))
                el.set('cy', str(new_cy))
                changed += 1
        return changed

    else:  # odt
        # ── ODT: all dimensions in mm ──────────────────────────────────────────
        DR  = 'urn:oasis:names:tc:opendocument:xmlns:drawing:1.0'
        SV  = 'urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0'
        FO  = 'urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0'
        STY = 'urn:oasis:names:tc:opendocument:xmlns:style:1.0'

        def _odf_to_mm(val):
            """Parse an ODF length string ('16cm', '160mm', '6.5in', '864pt') → mm."""
            if not val:
                return None
            m = re.match(r'^\s*([0-9]*\.?[0-9]+)\s*(cm|mm|in|pt|px)?\s*$', val)
            if not m:
                return None
            num, unit = float(m.group(1)), (m.group(2) or 'mm')
            return {'mm': num, 'cm': num * 10, 'in': num * 25.4,
                    'pt': num * 25.4 / 72, 'px': num * 25.4 / 96}[unit]

        def _mm_to_odf(mm, unit):
            """Format mm back to the given ODF unit string."""
            v = {'mm': mm, 'cm': mm / 10, 'in': mm / 25.4,
                 'pt': mm * 72 / 25.4, 'px': mm * 96 / 25.4}[unit]
            return f'{v:.4f}{unit}'

        # Read page geometry from styles.xml (page-layout-properties).
        # Also accepts margin-start/margin-end (alternate ODF attribute names).
        text_w, text_h = 160.0, 247.0   # A4 fallback
        styles_bytes = (template_zip_data or {}).get('styles.xml', b'')
        if styles_bytes:
            try:
                sroot = etree.fromstring(styles_bytes)
                for pm in sroot.iter('{%s}page-layout-properties' % STY):
                    pw = _odf_to_mm(pm.get('{%s}page-width'    % FO))
                    ph = _odf_to_mm(pm.get('{%s}page-height'   % FO))
                    ml = _odf_to_mm(pm.get('{%s}margin-left'   % FO)
                                    or pm.get('{%s}margin-start' % FO) or '0mm')
                    mr = _odf_to_mm(pm.get('{%s}margin-right'  % FO)
                                    or pm.get('{%s}margin-end'   % FO) or '0mm')
                    mt = _odf_to_mm(pm.get('{%s}margin-top'    % FO) or '0mm')
                    mb = _odf_to_mm(pm.get('{%s}margin-bottom' % FO) or '0mm')
                    if pw and ph:
                        text_w = pw - (ml or 0) - (mr or 0)
                        text_h = ph - (mt or 0) - (mb or 0)
                        break
            except Exception:
                pass

        # Scale draw:frame svg:width / svg:height.
        changed = 0
        for frame in doc_root.iter('{%s}frame' % DR):
            w_str = frame.get('{%s}width'  % SV, '')
            h_str = frame.get('{%s}height' % SV, '')
            if not w_str or not h_str:
                continue
            w_unit = re.sub(r'[0-9. ]', '', w_str) or 'mm'
            h_unit = re.sub(r'[0-9. ]', '', h_str) or 'mm'
            w_mm = _odf_to_mm(w_str)
            h_mm = _odf_to_mm(h_str)
            if not w_mm or not h_mm or w_mm <= 0 or h_mm <= 0:
                continue
            new_w, new_h = w_mm, h_mm
            if new_w > text_w:
                new_h = new_h * text_w / new_w
                new_w = text_w
            if new_h > text_h:
                new_w = new_w * text_h / new_h
                new_h = text_h
            if abs(new_w - w_mm) > 0.001 or abs(new_h - h_mm) > 0.001:
                frame.set('{%s}width'  % SV, _mm_to_odf(new_w, w_unit))
                frame.set('{%s}height' % SV, _mm_to_odf(new_h, h_unit))
                changed += 1
        return changed
