#!/usr/bin/env python3
"""
sw_export_merge.py — ScholarWeave export merge.

Takes the clean docx produced by pandoc (via the sw-*.lua filters) and the
target export template (book/article/document under templates/), and copies
the pandoc body into the template's XML, RETAINING the template's frontmatter
structure:
  - title block (Title/Subtitle/Author/Date/Abstract/Note) filled from YAML
    with cover-value resolution (resolve_cover in sw_merge_helpers; Title =
    whole title when a subtitle prop is given, else before ':' → whole title
    → basename; Subtitle = 'subtitle' prop → after ':' → none; Author =
    'author' → account name; Date = 'date' prop → today's date only when
    generate_date is on, else the Date slot is dropped)
  - the template's TOC section is preserved only when toc is requested
  - the template's ToF section is preserved only when tof is requested AND the
    input has figures
  - roman/arabic page numbering (when roman_frontmatter is on): frontmatter
    sections get lowerRoman page numbers and the reset heading picked by
    sw_merge_helpers.find_page_reset_index gets arabic start=1; later chapters
    continue arabic. When off, every section is arabic. Per-section footnote
    restart either way.
  - numbered chapters ("Chapter N:" or "N." Heading 1) get Word numbering
    (numPr) instead of literal text, so Word assigns chapter numbers that
    figure-caption fields can reference
  - figure captions become "Figure {chapter}.{n}. Description" via
    STYLEREF/SEQ fields, with an Alt-text paragraph in "Caption - Alt-text"
    (the figure walk itself is sw_merge_helpers.process_figures, shared with
    the ODT merge)
  - headers/footers: first section defines them, rest are "same as previous"

Every substantive content/structure decision is shared with the ODT merge via
sw_merge_helpers.py (see scripts/ARCHITECTURE.md). This script holds only the
OOXML mechanics. Do not re-fork logic that sw_export_odt_merge.py also needs.

Usage:
    python3 scripts/sw_export_merge.py \
        --template "templates/book.docx" \
        --input "path/to/clean.docx" \
        --output "path/to/final.docx" \
        [--title "Title"] [--subtitle "Subtitle"] [--author "Name"]
        [--date "Month D, YYYY"] [--basename "file stem"]
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
from sw_merge_helpers import (
    tag, get_style, set_style, collect_ids, mint_id, ensure_para_id,
    split_paragraphs, find_bibliography_range, strip_bibliography, ZOTERO_BIBL_INSTR,
    resize_images, STYLE_REMAP, STYLE_ALIASES, resolve_style_alias,
    resolve_cover, process_figures,
    title_case as _title_case, strip_markdown as _strip_markdown,
    is_main_start, is_toc_heading, is_tof_heading, strip_chapter_prefix,
    find_page_reset_index, bundled_template, ensure_docx_styles,
    append_extra_sections,
    csl_style_id, zotero_pref_blob, zotero_pref_chunks,
)

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

_ABSTRACTKEYWORDS_STYLE_ID = 'Abstractkeywordsheading'

# _title_case, _strip_markdown, resolve_cover are imported from sw_merge_helpers
# (shared verbatim with the ODT merge).

# is_main_start / is_toc_heading / is_tof_heading are imported from
# sw_merge_helpers (shared heading predicates).

# ── template layout extraction ───────────────────────────────────────────────

def _para_text(p):
    return ''.join(t.text or '' for t in p.iter(tag('t'))).strip()

def _para_instrs(p):
    return ''.join(it.text or '' for it in p.iter(tag('instrText')))

def _para_first_instr(p):
    """First instrText in a paragraph. The template's TOC/ToF field paras
    carry BOTH the field code AND cached PAGEREF results in the same para —
    concatenating them (as _para_instrs does) would emit a malformed
    instruction like 'TOC ... PAGEREF _Toc... \h' that Word can't parse."""
    for it in p.iter(tag('instrText')):
        if it.text and it.text.strip():
            return it.text
    return ''

def _is_sect_para(c):
    if c.tag != tag('p'):
        return False
    ppr = c.find(tag('pPr'))
    return ppr is not None and ppr.find(tag('sectPr')) is not None

def _strip_field_cache(p):
    """Remove cached field results (content between 'separate' and 'end'
    fldChars), keeping the field code (begin/instr/separate/end)."""
    in_cache = False
    for r in list(p):
        fld = r.find(tag('fldChar'))
        ftype = fld.get(tag('fldCharType')) if fld is not None else None
        if ftype == 'separate':
            in_cache = True
            continue
        if in_cache:
            if ftype == 'end':
                break
            p.remove(r)

def _plain_heading_copy(p):
    """Deep-copy a heading paragraph, dropping any field runs (fldChar /
    instrText / cached results) so only the pPr + plain-text runs remain.
    Used when a template puts the TOC/ToF FIELD code on the heading paragraph
    itself (document.docx) — we keep the styled heading, build a fresh field
    paragraph separately."""
    q = copy.deepcopy(p)
    in_field = False
    for r in list(q):
        if r.tag != tag('r'):
            continue
        fld = r.find(tag('fldChar'))
        ftype = fld.get(tag('fldCharType')) if fld is not None else None
        if ftype == 'begin':
            in_field = True
            q.remove(r)
            continue
        if ftype == 'end':
            in_field = False
            q.remove(r)
            continue
        if in_field or r.find(tag('instrText')) is not None:
            q.remove(r)
    return q


def extract_template_layout(template_path):
    """Extract the template's frontmatter skeleton and section kinds.
    Returns a dict with:
      title_block  — deep copies of body children before the first sectPr
      toc_heading, toc_field  — deep copies of the TOC heading/field paras
      tof_heading, tof_field  — deep copies of the ToF heading/field paras
      kinds        — {'title','toc','tof','roman','first_main','continue'}
      final_sect   — body-level final sectPr
      chapter_numid — numId used by the template's numbered chapter headings
    Section kinds are taken from the template's OWN sectPr-carrying paras, by
    position: title = first closer, toc = closer after the TOC field, tof =
    closer after the ToF field, roman = closer after ToF (frontmatter
    content), first_main = first with a start, continue = first no-pgNumType
    after first_main.
    """
    with zipfile.ZipFile(template_path) as z:
        doc = etree.fromstring(z.read('word/document.xml'))
        try:
            _styles_xml = z.read('word/styles.xml').decode('utf-8', 'replace')
        except KeyError:
            _styles_xml = ''
    body = doc.find(tag('body'))
    children = list(body)

    # Does the template define the caption-Alt-text style? Only book.docx does;
    # document/article don't, so we skip the "Alt-text." paragraph for those.
    has_alttext_style = 'w:styleId="caption-Alt-text"' in _styles_xml

    # 1. Title block.
    #    Structured (book-style) templates: everything before the first
    #    sectPr-carrying paragraph.
    #    Simple templates (document/article — no inline section breaks): just the
    #    leading run of cover paragraphs (Title/Subtitle/Author/Date/abstract
    #    placeholders), NOT the sample Heading 1/2/… that follow. This lets
    #    document.docx fill its cover the same way document.odt does, instead of
    #    falling back to pandoc's raw (unsplit, undated) frontmatter.
    first_sect_idx = None
    for i, c in enumerate(children):
        if _is_sect_para(c):
            first_sect_idx = i
            break
    has_section_breaks = first_sect_idx is not None
    if has_section_breaks:
        title_block = [copy.deepcopy(c) for c in children[:first_sect_idx]]
    else:
        _COVER_STYLES = {'Title', 'Subtitle', 'Author', 'Date',
                         _ABSTRACTKEYWORDS_STYLE_ID, 'Abstract'}
        cover_end = 0
        for c in children:
            if c.tag != tag('p') or get_style(c) not in _COVER_STYLES:
                break
            cover_end += 1
        title_block = [copy.deepcopy(c) for c in children[:cover_end]]

    # 2. Locate TOC/ToF headings and field paragraphs anywhere in the body.
    #    We keep the heading paras verbatim but build FRESH field paragraphs
    #    from the template's instruction text: the template's TOC/ToF fields
    #    SPAN multiple paragraphs (begin in one para, cached entries as TOC1/
    #    TOC2 paras, end far later), so copying one para leaves a dangling
    #    field that Word renders as literal text.
    toc_heading = tof_heading = None
    toc_instr = tof_instr = None
    toc_field_pos = tof_field_pos = None
    bibl_heading = bibl_instr = None
    for i, c in enumerate(children):
        if c.tag != tag('p'):
            continue
        style = get_style(c)
        text = _para_text(c)
        instrs = _para_instrs(c)
        if toc_heading is None and style in ('Heading1-excludefromTOC', 'TOCHeading'):
            toc_heading = copy.deepcopy(c)
        elif toc_heading is None and style == 'Heading1' and is_toc_heading(text):
            toc_heading = copy.deepcopy(c)
        elif tof_heading is None and (style == 'TOFHeading'
                or (style == 'Heading1' and is_tof_heading(text))):
            tof_heading = _plain_heading_copy(c)
            # document.docx carries the ToF field code ON the heading paragraph
            # (no separate field para); grab the instruction here.
            if tof_instr is None and 'TOC' in instrs and 'Figure' in instrs:
                tof_instr = _para_first_instr(c).strip()
                tof_field_pos = i
        elif bibl_heading is None and style == 'Heading1' and \
                text.strip().lower() == 'bibliography':
            bibl_heading = copy.deepcopy(c)
        elif bibl_instr is None and 'ZOTERO_BIBL' in instrs:
            bibl_instr = _para_first_instr(c).strip()
        elif toc_instr is None and 'TOC' in instrs and 'Figure' not in instrs:
            toc_instr = _para_first_instr(c).strip()
            toc_field_pos = i
        elif tof_instr is None and 'TOC' in instrs and 'Figure' in instrs:
            tof_instr = _para_first_instr(c).strip()
            tof_field_pos = i

    # 3. Collect every sectPr-carrying para position + the body final sectPr.
    sect_positions = []   # (index, sectPr element)
    for i, c in enumerate(children):
        if c.tag == tag('p'):
            ppr = c.find(tag('pPr'))
            sect = ppr.find(tag('sectPr')) if ppr is not None else None
            if sect is not None:
                sect_positions.append((i, copy.deepcopy(sect)))
    final_sect = None
    for c in children:
        if c.tag == tag('sectPr'):
            final_sect = copy.deepcopy(c)
    if final_sect is None:
        raise ValueError('template has no body-level sectPr')

    def first_sect_after(pos):
        for i, s in sect_positions:
            if i > pos:
                return s
        return None

    # 4. Label kinds by position.
    kinds = {}
    kinds['title'] = sect_positions[0][1] if sect_positions else final_sect
    kinds['toc'] = first_sect_after(toc_field_pos) if toc_field_pos is not None else kinds['title']
    kinds['tof'] = first_sect_after(tof_field_pos) if tof_field_pos is not None else kinds['toc']
    # roman = the closer after the ToF section (frontmatter content closer)
    tof_closer_idx = None
    for i, s in sect_positions:
        if s is kinds['tof']:
            tof_closer_idx = i
            break
    kinds['roman'] = first_sect_after(tof_closer_idx) if tof_closer_idx is not None else kinds['tof']
    # first_main = first sectPr with a start
    first_main = None
    for _, s in sect_positions:
        pg = s.find(tag('pgNumType'))
        if pg is not None and pg.get(tag('start')) is not None:
            first_main = s
            break
    kinds['first_main'] = first_main
    # continue = first no-pgNumType sectPr after first_main, else final
    continue_kind = None
    seen_fm = first_main is None
    for _, s in sect_positions:
        pg = s.find(tag('pgNumType'))
        if pg is None or (pg.get(tag('fmt')) is None and pg.get(tag('start')) is None):
            if seen_fm:
                continue_kind = s
                break
        if s is first_main:
            seen_fm = True
    kinds['continue'] = continue_kind if continue_kind is not None else final_sect

    # Fallbacks: any missing kind → continue-kind.
    for k in ('title', 'toc', 'tof', 'roman', 'first_main', 'continue'):
        if kinds.get(k) is None:
            kinds[k] = kinds.get('continue') if kinds.get('continue') is not None else final_sect

    # 5. Chapter numbering numId: from the template's numbered Heading 1s.
    chapter_numid = None
    for c in children:
        if c.tag == tag('p') and get_style(c) == 'Heading1':
            ppr = c.find(tag('pPr'))
            numpr = ppr.find(tag('numPr')) if ppr is not None else None
            if numpr is not None:
                ni = numpr.find(tag('numId'))
                if ni is not None:
                    chapter_numid = ni.get(tag('val'))
                    break

    return {
        'title_block': title_block,
        'toc_heading': toc_heading,
        'toc_instr': toc_instr,
        'tof_heading': tof_heading,
        'tof_instr': tof_instr,
        'bibl_heading': bibl_heading,
        'bibl_instr': bibl_instr,
        'kinds': kinds,
        'final_sect': final_sect,
        'chapter_numid': chapter_numid,
        'chapter_number_format': _chapter_number_format(template_path, chapter_numid),
        'has_alttext_style': has_alttext_style,
        'has_section_breaks': has_section_breaks,
    }


def _chapter_number_format(template_path, numid):
    """{lvlText, numFmt} the template's chapter numId uses at level 0, e.g.
    {'lvlText': 'Chapter %1.', 'numFmt': 'decimal'}. None when not numbered.
    Word only renders this on a field/numbering update, so the cached TOC has
    to reproduce it — same reason the ODT merge computes chapter numbers."""
    if not numid:
        return None
    try:
        with zipfile.ZipFile(template_path) as z:
            num_xml = etree.fromstring(z.read('word/numbering.xml'))
    except (KeyError, OSError):
        return None
    abs_id = None
    for num in num_xml.findall(tag('num')):
        if num.get(tag('numId')) == str(numid):
            ai = num.find(tag('abstractNumId'))
            abs_id = ai.get(tag('val')) if ai is not None else None
            break
    if abs_id is None:
        return None
    for an in num_xml.findall(tag('abstractNum')):
        if an.get(tag('abstractNumId')) != abs_id:
            continue
        for lvl in an.findall(tag('lvl')):
            if lvl.get(tag('ilvl')) == '0':
                lt = lvl.find(tag('lvlText'))
                nf = lvl.find(tag('numFmt'))
                return {
                    'lvlText': lt.get(tag('val')) if lt is not None else '%1.',
                    'numFmt': nf.get(tag('val')) if nf is not None else 'decimal',
                }
    return None


def _fmt_docx_chapter_number(fmt, n):
    """Render chapter number n per the template's lvlText/numFmt."""
    nf = fmt.get('numFmt', 'decimal')
    if nf in ('upperRoman', 'lowerRoman'):
        s = _roman(n)
        if nf == 'lowerRoman':
            s = s.lower()
    else:
        s = str(n)
    return re.sub(r'%\d', s, fmt.get('lvlText', '%1.'))


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

def make_field_paragraph(pstyle_val, instr_text, placeholder='Right-click to '
                        'update field.'):
    """Build a well-formed Word field paragraph (begin/instr/separate/
    placeholder/end) with the given style and field code."""
    p = etree.Element(tag('p'))
    ppr = etree.SubElement(p, tag('pPr'))
    if pstyle_val:
        ps = etree.SubElement(ppr, tag('pStyle'))
        ps.set(tag('val'), pstyle_val)
    r = etree.SubElement(p, tag('r'))
    fc = etree.SubElement(r, tag('fldChar'))
    fc.set(tag('fldCharType'), 'begin')
    r = etree.SubElement(p, tag('r'))
    it = etree.SubElement(r, tag('instrText'))
    it.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    it.text = ' ' + instr_text + ' '
    r = etree.SubElement(p, tag('r'))
    fc = etree.SubElement(r, tag('fldChar'))
    fc.set(tag('fldCharType'), 'separate')
    if placeholder:
        r = etree.SubElement(p, tag('r'))
        t = etree.SubElement(r, tag('t'))
        t.text = placeholder
    r = etree.SubElement(p, tag('r'))
    fc = etree.SubElement(r, tag('fldChar'))
    fc.set(tag('fldCharType'), 'end')
    return p


def _toc_depth_from_instr(instr, default=9):
    """Deepest heading level a Word TOC field is configured to show, from its
    `\\o "n-m"` range and any `\\t "style,level"` pairs."""
    depths = []
    m = re.search(r'\\o\s+"(\d+)-(\d+)"', instr or '')
    if m:
        depths.append(int(m.group(2)))
    for m in re.finditer(r'\\t\s+"([^"]*)"', instr or ''):
        parts = m.group(1).split(',')
        for i in range(1, len(parts), 2):
            try:
                depths.append(int(parts[i]))
            except ValueError:
                pass
    return max(depths) if depths else default


def _add_toc_bookmarks(sections, extra_styles=(), start_id=900000):
    """Wrap each Heading1..N paragraph (and any paragraph whose style is in
    extra_styles, e.g. figure captions) in a <w:bookmarkStart/End> so TOC/ToF
    entries can link to it — pandoc's DOCX headings/captions carry none.
    Returns {id(paragraph): bookmark_name}."""
    names = {}
    seen = set()
    bid = start_id
    for _kind, blocks in sections:
        for p in blocks:
            if p.tag != tag('p'):
                continue
            st = get_style(p) or ''
            if not (re.match(r'Heading\d+$', st) or st in extra_styles):
                continue
            slug = re.sub(r'[^a-z0-9]+', '-', _para_text(p).strip().lower()).strip('-')
            name = ('_sw_' + slug) if slug else ('_sw_p%d' % bid)
            n, base = name, name
            while n in seen:
                n = '%s_%d' % (base, bid)
            seen.add(n)
            names[id(p)] = n
            bs = etree.Element(tag('bookmarkStart'))
            bs.set(tag('id'), str(bid))
            bs.set(tag('name'), n)
            be = etree.Element(tag('bookmarkEnd'))
            be.set(tag('id'), str(bid))
            ppr = p.find(tag('pPr'))
            p.insert(1 if ppr is not None else 0, bs)
            p.append(be)
            bid += 1
    return names


def make_field_paragraphs(instr_text, entries, style_fn):
    """A TOC/ToF field whose cached result is real entries for THIS document,
    not the template's stale cache or the "Right-click to update field"
    placeholder. Neither pandoc nor this merge computes a field's display
    content — only a word processor does, on update — and a headless
    LibreOffice PDF conversion never updates it. Entries carry a hyperlink to
    the target's bookmark but no page number (pagination isn't known until
    layout); the field code is intact, so updating in a word processor
    regenerates everything.

    entries: list of (text, level, bookmark_or_None). style_fn(level) -> style.
    Returns a list of <w:p>; the field spans them (begin in the first,
    end in the last).
    """
    if not entries:
        return [make_field_paragraph(style_fn(1), instr_text)]
    paras = []
    for idx, (text, level, bookmark) in enumerate(entries):
        p = etree.Element(tag('p'))
        ppr = etree.SubElement(p, tag('pPr'))
        ps = etree.SubElement(ppr, tag('pStyle'))
        ps.set(tag('val'), style_fn(level))
        if idx == 0:
            r = etree.SubElement(p, tag('r'))
            etree.SubElement(r, tag('fldChar')).set(tag('fldCharType'), 'begin')
            r = etree.SubElement(p, tag('r'))
            it = etree.SubElement(r, tag('instrText'))
            it.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            it.text = ' ' + instr_text + ' '
            r = etree.SubElement(p, tag('r'))
            etree.SubElement(r, tag('fldChar')).set(tag('fldCharType'), 'separate')
        container = p
        if bookmark:
            container = etree.SubElement(p, tag('hyperlink'))
            container.set(tag('anchor'), bookmark)
        r = etree.SubElement(container, tag('r'))
        if bookmark:
            rpr = etree.SubElement(r, tag('rPr'))
            etree.SubElement(rpr, tag('rStyle')).set(tag('val'), 'Hyperlink')
        t = etree.SubElement(r, tag('t'))
        t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        t.text = text
        if idx == len(entries) - 1:
            r = etree.SubElement(p, tag('r'))
            etree.SubElement(r, tag('fldChar')).set(tag('fldCharType'), 'end')
        paras.append(p)
    return paras


def _docx_toc_entries(sections, max_level=9, bookmarks=None, chapter_fmt=None):
    """(text, level, bookmark) for every Heading1..HeadingN paragraph across
    sections, down to max_level. When the template numbers chapters, the
    number ('Chapter 1.') is computed here and prepended — Word only renders
    it on a numbering/field update, so the cached text would otherwise omit
    it. A numbered chapter is a Heading1 carrying <w:numPr> (added by
    apply_chapter_numbering, which runs before this)."""
    out = []
    chap_n = 0
    for _kind, blocks in sections:
        for p in blocks:
            if p.tag != tag('p'):
                continue
            m = re.match(r'Heading(\d+)$', get_style(p) or '')
            if not m:
                continue
            level = int(m.group(1))
            if level > max_level:
                continue
            text = _para_text(p).strip()
            if not text:
                continue
            ppr = p.find(tag('pPr'))
            numbered = (level == 1 and chapter_fmt is not None
                        and ppr is not None and ppr.find(tag('numPr')) is not None)
            if numbered:
                chap_n += 1
                text = _fmt_docx_chapter_number(chapter_fmt, chap_n) + ' ' + text
            out.append((text, max(1, level), (bookmarks or {}).get(id(p))))
    return out


#: Styles a figure caption can carry in merged DOCX output — 'Caption' is what
#: _make_figure_caption emits; 'ImageCaption' is pandoc's own, kept when
#: transform_figures didn't restyle it.
_DOCX_CAPTION_STYLES = frozenset({'Caption', 'ImageCaption'})


def _docx_tof_entries(sections, bookmarks=None):
    """(text, 1, bookmark) for every figure-caption paragraph across sections."""
    out = []
    for _kind, blocks in sections:
        for p in blocks:
            if p.tag == tag('p') and get_style(p) in _DOCX_CAPTION_STYLES:
                text = _para_text(p).strip()
                if text:
                    out.append((text, 1, (bookmarks or {}).get(id(p))))
    return out

#: <w:sectPr> child order — <w:pgNumType> sits after these, before <w:cols> etc.
_SECTPR_BEFORE_PGNUM = ('headerReference', 'footerReference', 'footnotePr',
                        'endnotePr', 'type', 'pgSz', 'pgMar', 'paperSrc',
                        'pgBorders', 'lnNumType')

def _ensure_pgnumtype(sectpr):
    """Return the <w:pgNumType> of a sectPr, creating it in a schema-valid
    position (after pgMar / lnNumType) when absent."""
    pg = sectpr.find(tag('pgNumType'))
    if pg is not None:
        return pg
    pg = etree.Element(tag('pgNumType'))
    anchor = None
    for c in sectpr:
        if c.tag.split('}')[-1] in _SECTPR_BEFORE_PGNUM:
            anchor = c
    if anchor is not None:
        anchor.addnext(pg)
    else:
        sectpr.insert(0, pg)
    return pg

#: Set by merge() for the static/PDF path: LibreOffice's DOCX import can't do
#: per-section footnote restart (numRestart="eachSect") — it renders every
#: restarted footnote as "0" — and the PDF path always goes through LibreOffice,
#: so those sections must NOT carry the restart. Direct DOCX export (opened in
#: Word) keeps it.
_SUPPRESS_FN_RESTART = False


def _normalize_footnote_restart(doc_root, data, restart_footnotes):
    """Make document-wide footnote numbering agree with the export's
    continuity choice.

    When footnotes are continuous (or _SUPPRESS_FN_RESTART forces continuity
    because the PDF path goes through LibreOffice, which renders
    numRestart="eachSect" from DOCX as a per-section restart even with a
    single section), strip every <w:numRestart>/<w:numStart> from BOTH the
    body sectPr elements AND settings.xml's document-level <w:footnotePr> —
    document.docx ships with eachSect there, and neither make_section_break
    nor the sectPr re-append touches settings.xml. Per-chapter restart
    (book output opened in Word) is left exactly as the template + section
    breaks set it."""
    if restart_footnotes and not _SUPPRESS_FN_RESTART:
        return
    for sect in doc_root.iter(tag('sectPr')):
        fnpr = sect.find(tag('footnotePr'))
        if fnpr is None:
            continue
        for c in fnpr.findall(tag('numRestart')) + fnpr.findall(tag('numStart')):
            fnpr.remove(c)
    if 'word/settings.xml' in data:
        try:
            s_root = etree.fromstring(data['word/settings.xml'])
            fnpr = s_root.find(tag('footnotePr'))
            if fnpr is not None:
                removed = False
                for c in (fnpr.findall(tag('numRestart'))
                          + fnpr.findall(tag('numStart'))):
                    fnpr.remove(c)
                    removed = True
                if removed:
                    data['word/settings.xml'] = etree.tostring(
                        s_root, xml_declaration=True, encoding='UTF-8',
                        standalone=True)
        except etree.XMLSyntaxError as e:
            print(f'WARNING: could not normalize settings.xml footnotes: {e}')


def make_section_break(sectpr_kind, rsid='00DE2936'):
    """Build a paragraph whose pPr carries the given sectPr — a section break.
    Ensures footnote numbering restarts per section (eachSect) so chapters
    don't number footnotes document-wide (unless _SUPPRESS_FN_RESTART)."""
    p = etree.Element(tag('p'))
    ppr = etree.SubElement(p, tag('pPr'))
    sect = copy.deepcopy(sectpr_kind)
    sect.set(tag('rsidR'), rsid)
    sect.set(tag('rsidSect'), rsid)
    fnpr = sect.find(tag('footnotePr'))
    if _SUPPRESS_FN_RESTART:
        if fnpr is not None:
            for _c in fnpr.findall(tag('numRestart')) + fnpr.findall(tag('numStart')):
                fnpr.remove(_c)
    else:
        # Per-section footnote restart. Schema order: pos, numFmt, numStart,
        # numRestart — explicit numStart="1" for word processors that need it.
        if fnpr is None:
            fnpr = etree.Element(tag('footnotePr'))
            pos = etree.SubElement(fnpr, tag('pos'))
            pos.set(tag('val'), 'beneathText')
            sect.insert(0, fnpr)
        if fnpr.find(tag('numStart')) is None:
            ns = etree.Element(tag('numStart'))
            ns.set(tag('val'), '1')
            nr_existing = fnpr.find(tag('numRestart'))
            fnpr.insert(list(fnpr).index(nr_existing) if nr_existing is not None
                        else len(fnpr), ns)
        if fnpr.find(tag('numRestart')) is None:
            nr = etree.SubElement(fnpr, tag('numRestart'))
            nr.set(tag('val'), 'eachSect')
    ppr.append(sect)
    return p

def _make_chapter_break(continue_sectpr, new_page, restart):
    """Build the inter-chapter paragraph break according to user settings.

    continue_sectpr — the template's 'continue' sectPr element, or None for
                      simple reference-doc templates with no structured sections.
    new_page        — True: each chapter starts on a new page.
    restart         — True: footnote numbering restarts at each chapter.

    The four cases:
      restart + new_page  → section break (nextPage type) with footnote restart
      restart + !new_page → continuous section break with footnote restart
      !restart + new_page → simple page-break paragraph (no section)
      !restart + !new_page→ None (no element inserted)

    Returns a <w:p> element, or None.
    """
    if restart:
        if continue_sectpr is None:
            # No template sectPr to base on — fall back to a page break when
            # new_page is set, else skip (can't do footnote restart without sectPr).
            if new_page:
                p = etree.Element(tag('p'))
                r = etree.SubElement(p, tag('r'))
                br = etree.SubElement(r, tag('br'))
                br.set(tag('type'), 'page')
                return p
            return None
        p = make_section_break(continue_sectpr)
        if not new_page:
            # Override the template's page-break type to continuous so the section
            # boundary carries footnote restart without starting a new page.
            sect = p.find('.//' + tag('sectPr'))
            if sect is not None:
                type_el = sect.find(tag('type'))
                if type_el is None:
                    type_el = etree.Element(tag('type'))
                    sect.insert(0, type_el)
                type_el.set(tag('val'), 'continuous')
        return p
    elif new_page:
        # Simple page break — no section element, so no footnote restart side-effect.
        p = etree.Element(tag('p'))
        r = etree.SubElement(p, tag('r'))
        br = etree.SubElement(r, tag('br'))
        br.set(tag('type'), 'page')
        return p
    else:
        return None  # no break at all

def _set_continuous(sect_para):
    """Patch a section-break paragraph's sectPr to use continuous type (no new page).
    Used to preserve page-numbering section boundaries without forcing a page break."""
    sect = sect_para.find('.//' + tag('sectPr'))
    if sect is not None:
        type_el = sect.find(tag('type'))
        if type_el is None:
            type_el = etree.Element(tag('type'))
            sect.insert(0, type_el)
        type_el.set(tag('val'), 'continuous')
    return sect_para

# ── body parsing ─────────────────────────────────────────────────────────────

def parse_body(doc):
    """Return the body element and its children list."""
    body = doc.find(tag('body'))
    return body, list(body)

def classify_blocks(children, reset_text=None):
    """
    Walk pandoc body children. Return list of (kind, blocks) where kind is
    'frontmatter' or 'main', splitting at Heading 1 boundaries:
      - a Heading 1 "Table of Contents"/"Table of Figures" stays frontmatter
      - `main` begins at the Heading 1 whose text == reset_text (the export
        dialog's "Page 1 starts with" heading) when given, else at the first
        Heading 1 matching is_main_start
      - subsequent Heading 1s also begin 'main' (new chapter section)
    """
    sections = []           # list of (kind, [blocks])
    current_kind = 'frontmatter'
    current = []
    in_main = False

    def flush():
        nonlocal current
        if current:
            sections.append((current_kind, current))
            current = []

    for child in children:
        if child.tag == tag('sectPr'):
            continue  # drop pandoc's own final sectPr; template provides it
        is_h1 = False
        text = ''
        if child.tag == tag('p'):
            style = get_style(child)
            if style == 'Heading1':
                text = _para_text(child)
                is_h1 = True
        if is_h1:
            flush()
            if is_toc_heading(text) or is_tof_heading(text):
                current_kind = 'frontmatter'
            elif not in_main and (text == reset_text if reset_text
                                  else is_main_start(text)):
                current_kind = 'main'
                in_main = True
            elif in_main:
                current_kind = 'main'
            else:
                # Heading 1 before the first main-start heading (e.g. Preface)
                # stays frontmatter.
                current_kind = 'frontmatter'
        current.append(child)
    flush()
    return sections

# ── chapter numbering ────────────────────────────────────────────────────────

def apply_chapter_numbering(sections, numid):
    """For every Heading 1 whose text looks like a numbered chapter
    ('Chapter 1: Title' or '1. Title'), strip the literal number/prefix (via the
    shared strip_chapter_prefix) and add Word numbering (numPr → numid) so Word
    supplies the chapter number. Non-numbered headings (Preface, Introduction,
    Conclusion, ...) keep plain Heading 1. Only Heading 1 is treated; sections
    (Heading 2+) never get chapter numbers.
    """
    if not numid:
        return
    for kind, blocks in sections:
        for b in blocks:
            if b.tag != tag('p') or get_style(b) != 'Heading1':
                continue
            text = _para_text(b)
            stripped = strip_chapter_prefix(text)
            if stripped == text:
                continue
            # Replace text with the number-stripped remainder.
            for r in list(b.findall(tag('r'))):
                b.remove(r)
            r = etree.SubElement(b, tag('r'))
            t = etree.SubElement(r, tag('t'))
            t.text = stripped
            # Add numPr into pPr (after pStyle).
            ppr = b.find(tag('pPr'))
            if ppr is None:
                ppr = etree.Element(tag('pPr'))
                b.insert(0, ppr)
            numpr = etree.Element(tag('numPr'))
            ilvl = etree.SubElement(numpr, tag('ilvl'))
            ilvl.set(tag('val'), '0')
            ni = etree.SubElement(numpr, tag('numId'))
            ni.set(tag('val'), numid)
            pstyle = ppr.find(tag('pStyle'))
            if pstyle is not None:
                pstyle.addnext(numpr)
            else:
                ppr.insert(0, numpr)

# ── figure captions + ToF ────────────────────────────────────────────────────

def _make_field_run(para, instr_text, cached=''):
    """Append a begin/instr/separate/cached/end field sequence to para."""
    r = etree.SubElement(para, tag('r'))
    fc = etree.SubElement(r, tag('fldChar'))
    fc.set(tag('fldCharType'), 'begin')
    r = etree.SubElement(para, tag('r'))
    it = etree.SubElement(r, tag('instrText'))
    it.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    it.text = ' ' + instr_text + ' '
    r = etree.SubElement(para, tag('r'))
    fc = etree.SubElement(r, tag('fldChar'))
    fc.set(tag('fldCharType'), 'separate')
    if cached:
        r = etree.SubElement(para, tag('r'))
        t = etree.SubElement(r, tag('t'))
        t.text = cached
    r = etree.SubElement(para, tag('r'))
    fc = etree.SubElement(r, tag('fldChar'))
    fc.set(tag('fldCharType'), 'end')

def _flatten_caption_fields(sections):
    """Replace the SEQ/STYLEREF field runs in each figure caption with their
    cached number as plain runs. Static path only — Word/LibreOffice recompute
    those fields (STYLEREF reads chapter numbering that may not resolve, SEQ
    without a working \\s restart goes continuous), so 'Figure 1.1' comes back
    as 'Figure 1'. Freezing the text sidesteps that, like the cached TOC/ToF."""
    for _kind, blocks in sections:
        for p in blocks:
            if p.tag != tag('p') or get_style(p) not in _DOCX_CAPTION_STYLES:
                continue
            depth = 0
            after_sep = False
            for r in list(p.findall(tag('r'))):
                fc = r.find(tag('fldChar'))
                if fc is not None:
                    ft = fc.get(tag('fldCharType'))
                    if ft == 'begin':
                        depth += 1
                        after_sep = False
                    elif ft == 'separate':
                        after_sep = True
                    elif ft == 'end':
                        depth = max(0, depth - 1)
                        after_sep = False
                    p.remove(r)
                elif r.find(tag('instrText')) is not None:
                    p.remove(r)
                elif depth and not after_sep:
                    p.remove(r)


def _make_figure_caption(description, number, chapter_scoped):
    """Build a Caption-style paragraph 'Figure {number}. {description}'.

    The number (computed by process_figures) is written as the field's CACHED
    value so the exported file shows the right number immediately — previously
    every caption cached '0.1'. The SEQ / STYLEREF field is kept so Ctrl+A→F9
    still renumbers in Word.
      chapter_scoped=True  (book): 'Figure {STYLEREF 1 \\s}.{SEQ Figure \\s 1}'
      chapter_scoped=False       : 'Figure {SEQ Figure \\* ARABIC}'
    Runs with leading/trailing spaces need xml:space="preserve" or Word trims
    them ('Figure 0.1' would render 'Figure0.1')."""
    p = etree.Element(tag('p'))
    ppr = etree.SubElement(p, tag('pPr'))
    etree.SubElement(ppr, tag('pStyle')).set(tag('val'), 'Caption')
    r = etree.SubElement(p, tag('r'))
    t = etree.SubElement(r, tag('t'))
    t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    t.text = 'Figure '
    if chapter_scoped:
        chap, _, fnum = number.partition('.')
        _make_field_run(p, 'STYLEREF 1 \\s', chap)
        r = etree.SubElement(p, tag('r'))
        etree.SubElement(r, tag('t')).text = '.'
        _make_field_run(p, 'SEQ Figure \\* ARABIC \\s 1', fnum or '1')
    else:
        _make_field_run(p, 'SEQ Figure \\* ARABIC', number)
    r = etree.SubElement(p, tag('r'))
    t = etree.SubElement(r, tag('t'))
    t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    t.text = '. ' + description
    return p

def _make_alttext_para(alt_text):
    """Build a 'caption-Alt-text' style paragraph: 'Alt-text.' + text, or a
    blank 'Alt-text.' label when there is no alt text. Emitted only when the
    template defines the caption-Alt-text style (see transform_figures)."""
    p = etree.Element(tag('p'))
    ppr = etree.SubElement(p, tag('pPr'))
    etree.SubElement(ppr, tag('pStyle')).set(tag('val'), 'caption-Alt-text')
    r = etree.SubElement(p, tag('r'))
    t = etree.SubElement(r, tag('t'))
    t.text = 'Alt-text.' + (' ' + alt_text if alt_text else '')
    return p

def transform_figures(sections, chapter_scoped, has_alttext_style):
    """Convert pandoc figure blocks in `sections` to the template's caption
    layout via the shared sw_merge_helpers.process_figures (the walk logic is
    identical for DOCX and ODT). Also restyles pandoc TableCaption paragraphs
    to plain Caption (not numbered, not in the ToF).

      chapter_scoped    — number captions 'C.N' (book) vs 'N' (sequential)
      has_alttext_style — template defines caption-Alt-text; emit alt-text paras
    Returns True if any figures were found (→ keep the ToF section).
    """
    for _kind, blocks in sections:
        for b in blocks:
            if b.tag == tag('p') and get_style(b) == 'TableCaption':
                set_style(b, 'Caption')

    def _make_caption(desc, number, _ordinal):
        return _make_figure_caption(desc, number, chapter_scoped)

    def _make_alttext(alt):
        return _make_alttext_para(alt) if has_alttext_style else None

    state = None
    any_figs = False
    for _kind, blocks in sections:
        new_blocks, hf, state = process_figures(
            blocks,
            get_style=get_style, get_text=_para_text,
            set_body_style=lambda el: set_style(el, 'BodyText'),
            is_heading1=lambda el: el.tag == tag('p') and get_style(el) == 'Heading1',
            image_styles={'CaptionedFigure'},
            caption_styles={'ImageCaption'},
            body_styles={'BodyText', 'FirstParagraph'},
            make_caption=_make_caption, make_alttext=_make_alttext,
            chapter_scoped=chapter_scoped, start_state=state)
        blocks[:] = new_blocks
        any_figs = any_figs or hf
    return any_figs


# ── build output body ────────────────────────────────────────────────────────

def build_body(template_body, layout, sections, used, has_figures, toc=False,
               tof=False, new_page_headings=True, restart_footnotes=True,
               extra_sections=None, has_bibliography=False, style_remap=None):
    """
    Rebuild the template body:
      1. title block (cover values filled by the caller)
      2. TOC section: template's heading + field para (sample entries dropped)
         — ONLY when toc is True (the checkbox in the export modal; when the
         user unchecks it, the template's TOC is removed entirely)
      3. ToF section: template's heading + field para, ONLY if figures present
      4. pandoc content sections with roman/first_main/continue breaks
      5. re-append the template's body-level final sectPr
    """
    kinds = layout['kinds']
    final_sect = layout['final_sect']
    # Structured = the template has inline section breaks (page-numbering
    # sections); this drives section-break insertion and TOC/ToF placement.
    # A simple template can still have a cover title_block to fill (document.docx).
    structured = layout['has_section_breaks']

    # Cached-TOC support: bookmark every heading + figure caption so TOC/ToF
    # entries can link to them, honour the template TOC field's configured
    # depth, and compute chapter numbers ("Chapter 1.") Word only renders on
    # a numbering update.
    _toc_bookmarks = (_add_toc_bookmarks(sections, extra_styles=_DOCX_CAPTION_STYLES)
                      if (toc or tof) else {})
    _toc_max_level = _toc_depth_from_instr(layout.get('toc_instr'))
    _toc_chap_fmt = layout.get('chapter_number_format')
    _toc_entry_list = lambda: _docx_toc_entries(
        sections, _toc_max_level, _toc_bookmarks, _toc_chap_fmt)
    _tof_entry_list = lambda: _docx_tof_entries(
        sections, bookmarks=_toc_bookmarks)

    # Clear the template body.
    for c in list(template_body):
        template_body.remove(c)

    # 1. Title block (cover). Prepend whenever the template gives us one; add a
    #    section closer only for structured templates.
    if layout['title_block']:
        for p in layout['title_block']:
            template_body.append(p)
        if structured:
            _brk = make_section_break(kinds['title'])
            if not new_page_headings:
                _set_continuous(_brk)
            template_body.append(_brk)

    # 2. TOC section. Structured templates emit it here, after the cover, with a
    #    section closer. Simple templates defer it into the content stream (just
    #    before the first content heading) so no spurious section break appears.
    if toc and layout['toc_instr'] and structured:
        if layout['toc_heading'] is not None:
            template_body.append(layout['toc_heading'])
        for _p in make_field_paragraphs(layout['toc_instr'],
                                        _toc_entry_list(),
                                        lambda l: 'TOC%d' % l):
            template_body.append(_p)
        _brk = make_section_break(kinds['toc'])
        if not new_page_headings:
            _set_continuous(_brk)
        template_body.append(_brk)
    toc_deferred = toc and bool(layout['toc_instr']) and not structured

    # The user asked for a table of figures AND the document has figures AND a
    # ToF heading + field code is available (from the template or the bundled
    # fallback). Structured templates emit it here (after the TOC); simple
    # templates emit it in the deferred path alongside the TOC.
    want_tof = (tof and has_figures
                and layout['tof_heading'] is not None and layout['tof_instr'])
    tof_deferred = want_tof and not structured

    # 3. ToF section — structured (book) templates.
    if want_tof and structured:
        template_body.append(layout['tof_heading'])
        for _p in make_field_paragraphs(layout['tof_instr'],
                                        _tof_entry_list(),
                                        lambda l: 'TableofFigures'):
            template_body.append(_p)
        _brk = make_section_break(kinds['tof'])
        if not new_page_headings:
            _set_continuous(_brk)
        template_body.append(_brk)

    # 4. Content sections.
    # For simple templates: track whether we have injected an abstract heading
    # before pandoc's "Abstract"-styled body paragraph(s).  Structured templates
    # already carry the heading in their title_block, so we skip injection there.
    _abstract_heading_injected = bool(layout['title_block'])

    seen_main = False
    content_sections = []
    skipped_bookmark_ids = set()
    for kind, blocks in sections:
        # Skip the leading title/author block if it's the first section —
        # but only when the template has its own title block (book-style).
        # For simple reference-doc templates (title_block is []), let pandoc's
        # Title/Author paragraphs through so the note title renders normally.
        if layout['title_block'] and content_sections == [] and blocks \
                and blocks[0].tag == tag('p') \
                and get_style(blocks[0]) in ('Title', 'Author'):
            # Collect any bookmarkStart ids from the skipped block so we can
            # remove orphaned bookmarkEnds from the remaining content.
            for b in blocks:
                for el in b.iter(tag('bookmarkStart')):
                    bid = el.get(tag('id'))
                    if bid:
                        skipped_bookmark_ids.add(bid)
            continue
        content_sections.append((kind, blocks))

    # Remove bookmarkEnd elements whose starts were in the skipped block;
    # leaving them creates orphan ends that Word flags as unreadable content.
    if skipped_bookmark_ids:
        for _, blocks in content_sections:
            for b in blocks:
                for el in list(b.iter(tag('bookmarkEnd'))):
                    if el.get(tag('id')) in skipped_bookmark_ids:
                        parent = el.getparent()
                        if parent is not None:
                            parent.remove(el)

    for idx, (kind, blocks) in enumerate(content_sections):
        # Deferred TOC for simple templates: emit just before the first real
        # content heading (any Heading 1 that isn't a TOC/ToF heading), so
        # the TOC lands after Title/Author/frontmatter text but before body.
        # We do NOT restrict to kind=='main': is_main_start matches only a
        # handful of names, so most document headings stay 'frontmatter' and
        # the TOC would never fire if we gated on kind.
        if (toc_deferred or tof_deferred) and blocks and blocks[0].tag == tag('p') \
                and get_style(blocks[0]) == 'Heading1':
            h1_text = _para_text(blocks[0])
            if not is_toc_heading(h1_text) and not is_tof_heading(h1_text):
                # Remove the preceding bare page-break paragraph (emitted as the
                # frontmatter section break) so the first inserted heading can
                # carry the page break itself, avoiding a blank paragraph.
                if len(template_body) > 0:
                    _prev = template_body[-1]
                    _brs  = list(_prev.iter(tag('br')))
                    _is_bare_pb = (
                        _prev.tag == tag('p')
                        and any(b.get(tag('type'), '') == 'page' for b in _brs)
                        and _prev.find('.//' + tag('sectPr')) is None
                        and not any(el.text for el in _prev.iter(tag('t')))
                    )
                    if _is_bare_pb:
                        template_body.remove(_prev)

                def _emit_field_section(heading_el, field_instr, entries, style_fn):
                    """Append heading (+ optional page break) + field paragraphs
                    + optional trailing page break for a deferred TOC / ToF."""
                    if heading_el is not None:
                        _ppr = heading_el.find(tag('pPr'))
                        if _ppr is None:
                            _ppr = etree.Element(tag('pPr'))
                            heading_el.insert(0, _ppr)
                        _pbb = _ppr.find(tag('pageBreakBefore'))
                        if new_page_headings and _pbb is None:
                            etree.SubElement(_ppr, tag('pageBreakBefore'))
                        elif not new_page_headings and _pbb is not None:
                            _ppr.remove(_pbb)
                        template_body.append(heading_el)
                    elif new_page_headings:
                        _pb = etree.SubElement(
                            etree.SubElement(etree.SubElement(
                                template_body, tag('p')), tag('r')), tag('br'))
                        _pb.set(tag('type'), 'page')
                    for _fp in make_field_paragraphs(field_instr, entries, style_fn):
                        template_body.append(_fp)
                    if new_page_headings:
                        _b = etree.SubElement(etree.SubElement(etree.SubElement(
                            template_body, tag('p')), tag('r')), tag('br'))
                        _b.set(tag('type'), 'page')

                if toc_deferred:
                    _emit_field_section(layout['toc_heading'], layout['toc_instr'],
                                        _toc_entry_list(),
                                        lambda l: 'TOC%d' % l)
                    toc_deferred = False
                if tof_deferred:
                    _emit_field_section(layout['tof_heading'], layout['tof_instr'],
                                        _tof_entry_list(),
                                        lambda l: 'TableofFigures')
                    tof_deferred = False
        for b in blocks:
            # Remap pandoc-specific styles to template names.
            if b.tag == tag('p'):
                st = get_style(b)
                # Pandoc emits blockquotes as styleId "BlockText" and first
                # paragraphs as "FirstParagraph"; remap both onto the
                # template's styles. `style_remap` is STYLE_REMAP['docx']
                # (shared with ODT) with 'BlockText' resolved per-template by
                # resolve_style_alias in merge() — see STYLE_ALIASES.
                remap = style_remap or STYLE_REMAP['docx']
                if st in remap:
                    set_style(b, remap[st])
                elif st == 'Abstract' and not _abstract_heading_injected:
                    # Pandoc emits the YAML 'abstract:' field as one or more
                    # paragraphs with "Abstract" paragraph style.  For simple
                    # reference-doc templates (title_block is []) no heading
                    # precedes them, so we inject one here using the template's
                    # Abstractkeywordsheading style before the first such paragraph.
                    _abstract_heading_injected = True
                    h = etree.Element(tag('p'))
                    hpr = etree.SubElement(h, tag('pPr'))
                    hstyle = etree.SubElement(hpr, tag('pStyle'))
                    hstyle.set(tag('val'), _ABSTRACTKEYWORDS_STYLE_ID)
                    hr = etree.SubElement(h, tag('r'))
                    ht = etree.SubElement(hr, tag('t'))
                    ht.text = 'Abstract'
                    template_body.append(h)
            template_body.append(b)
        # For simple reference-doc templates (no structured title block),
        # inject note/sw-* extra sections after the first frontmatter section
        # (Title/Author/Abstract area) so they appear between the cover and the
        # first heading rather than replacing the title entirely.
        if extra_sections and not layout['title_block'] and idx == 0 \
                and kind == 'frontmatter':
            _extra_buf = []
            _append_extra_sections(_extra_buf, extra_sections)
            for _ep in _extra_buf:
                template_body.append(_ep)
        if idx == len(content_sections) - 1:
            break  # final section closed by final_sect
        has_template_sects = structured
        if kind == 'frontmatter':
            if has_template_sects:
                # Book template: use roman sectPr (lowerRoman page numbering).
                # Between frontmatter sections, respect new_page_headings.
                # At the frontmatter→main boundary, always force a new page
                # because the numbering system changes (roman→arabic).
                next_kind = content_sections[idx + 1][0]
                brk = make_section_break(kinds['roman'])
                if next_kind == 'frontmatter' and not new_page_headings:
                    _set_continuous(brk)
                template_body.append(brk)
            else:
                # Simple document template: no page-numbering sections, so treat
                # frontmatter breaks the same as chapter breaks — user-controlled.
                brk = _make_chapter_break(None, new_page_headings, restart_footnotes)
                if brk is not None:
                    template_body.append(brk)
        elif not seen_main:
            # First main section: sets arabic page numbering (first_main break).
            # This break falls between Chapter 1 and Chapter 2 (same numbering
            # system), so it respects new_page_headings like any other chapter.
            seen_main = True
            if has_template_sects:
                brk = make_section_break(kinds['first_main'])
                if not new_page_headings:
                    _set_continuous(brk)
                template_body.append(brk)
            else:
                # Simple template: apply user-controlled break before first chapter.
                brk = _make_chapter_break(None, new_page_headings, restart_footnotes)
                if brk is not None:
                    template_body.append(brk)
        else:
            # Subsequent chapters: apply user-controlled new-page / restart settings.
            brk = _make_chapter_break(
                kinds.get('continue') if has_template_sects else None,
                new_page_headings, restart_footnotes)
            if brk is not None:
                template_body.append(brk)

    # 5. Bibliography section — appended when the pandoc output contained a
    #    bibliography (pandoc's plain-text entries were stripped in merge() via
    #    _strip_bibliography_from_sections so that a fresh Zotero field is used
    #    instead, giving consistent output regardless of template structure).
    if has_bibliography:
        _has_template_sects = structured
        if _has_template_sects and kinds.get('continue'):
            _bbrk = make_section_break(kinds['continue'])
            if not new_page_headings:
                _set_continuous(_bbrk)
            template_body.append(_bbrk)
        else:
            _bbrk = _make_chapter_break(None, new_page_headings, restart_footnotes)
            if _bbrk is not None:
                template_body.append(_bbrk)
        _bh = etree.Element(tag('p'))
        _bhpr = etree.SubElement(_bh, tag('pPr'))
        _bhst = etree.SubElement(_bhpr, tag('pStyle'))
        _bhst.set(tag('val'), 'Heading1')
        _bhr = etree.SubElement(_bh, tag('r'))
        _bht = etree.SubElement(_bhr, tag('t'))
        _bht.text = 'Bibliography'
        template_body.append(_bh)
        template_body.append(make_field_paragraph(
            'Bibliography',
            ZOTERO_BIBL_INSTR,
            'Refresh Zotero to view this content.'))

    # 6. Final body sectPr.
    template_body.append(final_sect)

# ── merge ────────────────────────────────────────────────────────────────────

def merge(template_path, input_path, output_path, title=None, author=None,
          subtitle=None, date_val=None, toc=False, tof=False, short_title=None,
          basename=None, abstract=None, extra_sections=None,
          new_page_headings=True, restart_footnotes=True, generate_date=True,
          roman_frontmatter=False, page1_starts_with='', static_citations=False,
          csl_style=None):
    global _SUPPRESS_FN_RESTART
    _SUPPRESS_FN_RESTART = static_citations
    with zipfile.ZipFile(template_path) as z:
        tmpl_doc = etree.fromstring(z.read('word/document.xml'))
        _tmpl_styles_bytes = z.read('word/styles.xml') if 'word/styles.xml' in z.namelist() else b''
    tmpl_body = tmpl_doc.find(tag('body'))
    used = collect_ids(tmpl_doc)

    with zipfile.ZipFile(input_path) as z:
        pdc_doc = etree.fromstring(z.read('word/document.xml'))
    pdc_body, pdc_children = parse_body(pdc_doc)

    layout = extract_template_layout(template_path)

    # Resolve semantic-role style aliases (currently: the blockquote style)
    # against THIS template's own defined styles, so a template that names its
    # blockquote style differently from pandoc's fixed output id (book.docx
    # has its own "Blockquote"/"Block quote" instead of document.docx's
    # "BlockText") gets its own style instead of an undefined-style fallback
    # to Normal. See sw_merge_helpers.STYLE_ALIASES.
    def _style_display_name(style_el):
        name_el = style_el.find(tag('name'))
        return name_el.get(tag('val')) if name_el is not None else None

    _style_remap = dict(STYLE_REMAP['docx'])
    _extra_style_ids_for_aliases = []
    if _tmpl_styles_bytes:
        _defined_styles = [
            (s.get(tag('styleId')), _style_display_name(s))
            for s in etree.fromstring(_tmpl_styles_bytes).findall(tag('style'))
        ]
        for _pandoc_id, (_candidates, _fallback) in STYLE_ALIASES['docx'].items():
            _target = resolve_style_alias(_defined_styles, _candidates, _fallback)
            _style_remap[_pandoc_id] = _target
            if _target == _fallback:
                # Nothing in the template matched — make sure the fallback
                # style actually exists (borrowed from document.docx).
                _extra_style_ids_for_aliases.append(_fallback)

    # When the user asked for a table of figures but their template has no ToF
    # structure, borrow the heading + field code from the bundled document.docx
    # (its styles are pulled in later by _write_docx via _tof_style_ids).
    _tof_style_ids = []
    if tof and (layout['tof_heading'] is None or layout['tof_instr'] is None):
        try:
            _fallback = extract_template_layout(bundled_template('document.docx'))
            if layout['tof_heading'] is None:
                layout['tof_heading'] = _fallback['tof_heading']
            if layout['tof_instr'] is None:
                layout['tof_instr'] = _fallback['tof_instr']
            _tof_style_ids = ['TOFHeading', 'TableofFigures']
        except Exception as e:
            print(f'WARNING: could not load fallback ToF from document.docx: {e}')

    # Roman-frontmatter page numbering: find the Heading 1 where page 1 (arabic)
    # begins. When roman numbering is off, or no such heading exists, strip the
    # roman/start page-number formatting from every section kind so the whole
    # document is arabic.
    _h1_texts = [_para_text(c) for c in pdc_children
                 if c.tag == tag('p') and get_style(c) == 'Heading1']
    _reset_idx = (find_page_reset_index(_h1_texts, page1_starts_with)
                  if roman_frontmatter else None)
    _apply_roman = roman_frontmatter and _reset_idx is not None
    if not _apply_roman:
        for _k in list(layout['kinds']):
            _pg = layout['kinds'][_k].find(tag('pgNumType'))
            if _pg is not None:
                layout['kinds'][_k].remove(_pg)
    else:
        # Guarantee the roman/start formatting even for a template that doesn't
        # carry it (e.g. document.docx used as a book).
        for _k in ('title', 'toc', 'tof', 'roman'):
            _pg = _ensure_pgnumtype(layout['kinds'][_k])
            _pg.set(tag('fmt'), 'lowerRoman')
            _pg.attrib.pop(tag('start'), None)
        _pg = _ensure_pgnumtype(layout['kinds']['first_main'])
        _pg.set(tag('start'), '1')
        _pg.attrib.pop(tag('fmt'), None)
        _cn = layout['kinds']['continue'].find(tag('pgNumType'))
        if _cn is not None:
            layout['kinds']['continue'].remove(_cn)

    sections = classify_blocks(
        pdc_children,
        reset_text=(_h1_texts[_reset_idx] if _apply_roman else None))

    # Strip the bibliography section (pandoc plain-text entries) so the merge
    # can append a fresh Zotero field at the end instead.  This mirrors the ODT
    # approach and ensures both formats use identical bibliography handling.
    #
    # In static_citations mode (PDF path — see DocumentCompiler.py), pandoc's
    # own --citeproc already produced a real, populated bibliography (Heading1
    # + rendered entries, already last) — there's no live field to refresh, so
    # it's left as ordinary body content instead of being stripped and
    # replaced with the "Refresh Zotero" placeholder.
    if static_citations:
        _has_bibliography = False
    else:
        sections, _has_bibliography = _strip_bibliography_from_sections(sections)

    # Figure captions → template caption layout; decides ToF presence.
    # Chapter-scoped numbering (Figure C.N) tracks the footnote-restart setting
    # (both are the "per chapter" behaviour); otherwise captions are 'Figure N'.
    has_figures = transform_figures(
        sections, restart_footnotes, layout['has_alttext_style'])

    if static_citations:
        _flatten_caption_fields(sections)

    # Chapter numbering: strip literal "Chapter N:" and let Word number.
    apply_chapter_numbering(sections, layout['chapter_numid'])

    # Cover values (Title/Subtitle/Author/Date resolution).
    title, subtitle, author, date_val = resolve_cover(
        title, subtitle, author, date_val, basename, generate_date=generate_date)

    # Fill title/subtitle/author/date into the template title block and DROP
    # its abstract/keywords placeholder slots. The abstract is then re-injected
    # like any other extra section (drop-and-reinject) — the same policy the ODT
    # merge uses, so both formats build the abstract identically.
    _fill_title_block(layout['title_block'], title, subtitle, author, date_val)

    all_extra = ([('abstract', abstract)] if abstract else []) + (extra_sections or [])

    # Structured (book-style) templates: append the extra sections to the title
    # block (build_body renders it before the first section break). Simple
    # reference-doc templates have title_block == [] — appending here would make
    # build_body skip pandoc's own Title/Author paragraphs, so pass them to
    # build_body, which injects them into the content stream after the cover.
    _has_structured_title = bool(layout['title_block'])
    if _has_structured_title:
        _append_extra_sections(layout['title_block'], all_extra)

    build_body(tmpl_body, layout, sections, used, has_figures, toc=toc, tof=tof,
               new_page_headings=new_page_headings,
               restart_footnotes=restart_footnotes,
               extra_sections=all_extra if not _has_structured_title else None,
               has_bibliography=_has_bibliography, style_remap=_style_remap)

    # Remove ORPHANED bookmarkEnd elements: any end whose matching
    # bookmarkStart is absent from the final body. This happens when a
    # bookmark STARTS in the skipped title/author block but ENDS deep in the
    # content (pandoc spans "preface" etc. across the block boundary) — the
    # start gets dropped with the skipped block, leaving an orphan end that
    # Word flags as unreadable content.
    _remove_orphan_bookmark_ends(tmpl_body)

    if toc and layout['toc_instr'] is None:
        inject_toc(tmpl_body)

    # Rebuild footnotes: template separators + real footnotes from clean
    # docx, renumbered sequentially; rewrite the body's footnoteReference
    # ids to match (Word treats non-sequential ids as unreadable content).
    new_footnotes, fn_id_map = rebuild_footnotes(template_path, input_path)
    if fn_id_map:
        _remap_footnote_refs(tmpl_doc, fn_id_map)

    # Every paragraph needs a w14:paraId + w14:textId, and Word expects
    # w:rsidR/w:rsidRDefault on paragraphs (it adds them to all 256 on
    # repair — missing rsid attrs are treated as unreadable content).
    # The IAFR merge skill flags the paraId/textId requirement too.
    for p in tmpl_doc.iter(tag('p')):
        ensure_para_id(p, used)
        if p.get(tag('rsidR')) is None:
            p.set(tag('rsidR'), '00DE2936')
        if p.get(tag('rsidRDefault')) is None:
            p.set(tag('rsidRDefault'), '00DE2936')

    # Apply the same paraId/rsid patching to footnotes.xml paragraphs.
    # footnotes.xml is a separate XML part not covered by the loop above;
    # pandoc's footnote paragraphs lack these attrs, which Word flags as
    # unreadable content (it adds them to every footnote para on repair).
    if new_footnotes is not None:
        fn_root = etree.fromstring(new_footnotes)
        for p in fn_root.iter(tag('p')):
            ensure_para_id(p, used)
            if p.get(tag('rsidR')) is None:
                p.set(tag('rsidR'), '00DE2936')
            if p.get(tag('rsidRDefault')) is None:
                p.set(tag('rsidRDefault'), '00DE2936')
        new_footnotes = etree.tostring(fn_root, xml_declaration=True,
                                       encoding='UTF-8', standalone=True)

    # Cap images to template text area dimensions, preserving aspect ratio.
    n_scaled = resize_images(tmpl_doc, 'docx')
    if n_scaled:
        print(f'DOCX: capped {n_scaled} image extent(s) to text area')
    if new_footnotes is not None:
        fn_root = etree.fromstring(new_footnotes)
        resize_images(fn_root, 'docx')  # footnotes: A4 fallback (no sectPr)
        new_footnotes = etree.tostring(fn_root, xml_declaration=True,
                                       encoding='UTF-8', standalone=True)

    # Save via python-docx-like zip write (preserve all other parts).
    _write_docx(template_path, output_path, tmpl_doc, new_footnotes,
                short_title=short_title, author=author, title=title,
                subtitle=subtitle, input_path=input_path,
                extra_style_ids=_tof_style_ids + _extra_style_ids_for_aliases,
                csl_style=csl_style, restart_footnotes=restart_footnotes)

def _fill_title_block(title_block, title, subtitle, author, date_val):
    """Replace placeholder text in the template title block (a list of deep
    copies used by build_body):
      - Title / Subtitle styles → resolved values
      - Subtitle / Date paragraph REMOVED when there is no subtitle / date
      - first non-Date Body Text after Subtitle → author
      - Body Text 'Date' → date
      - every Abstract/keywords/Note placeholder slot is DROPPED; the abstract
        and note/sw-* sections are re-injected by _append_extra_sections (the
        same drop-and-reinject policy as the ODT merge)
    """
    author_done = not author
    date_done = not date_val
    after_subtitle = False
    i = 0
    while i < len(title_block):
        p = title_block[i]
        style = get_style(p)
        cur = _para_text(p)
        if style == 'Title' and title:
            _fill_markdown_text(p, title)
            i += 1
        elif style == 'Subtitle':
            after_subtitle = True
            if subtitle:
                _fill_markdown_text(p, subtitle)
            else:
                title_block.pop(i)   # remove the paragraph entirely
                continue
            i += 1
        elif style == 'Author':
            # Canonical Author style (document.odt/docx). book.docx instead uses
            # a BodyText paragraph after the Subtitle (handled below).
            _replace_text(p, author or '')
            author_done = True
            i += 1
        elif style == 'Date':
            if date_val:
                _replace_text(p, date_val)
                date_done = True
                i += 1
            else:
                title_block.pop(i)     # no date → drop the Date paragraph
                continue
        elif style == 'BodyText' and after_subtitle:
            if not author_done and cur != 'Date':
                _replace_text(p, author)
                author_done = True
                i += 1
            elif cur == 'Date':
                if date_val and not date_done:
                    _replace_text(p, date_val)
                    date_done = True
                    i += 1
                elif not date_val:
                    title_block.pop(i)   # no date → drop the Date slot
                    continue
                else:
                    i += 1
            else:
                i += 1
        elif style == 'Abstractkeywordsheading':
            # Abstract/keywords/note placeholder heading (article/book templates).
            # Always drop it and its following body paragraph(s); the real
            # abstract + note/sw-* sections are re-injected by
            # _append_extra_sections.
            title_block.pop(i)
            while i < len(title_block) and get_style(title_block[i]) in ('BodyText', 'Abstract'):
                title_block.pop(i)
            continue
        elif style == 'Abstract':
            # Standalone abstract body paragraph (document.docx template style).
            # Only reached when no preceding Abstractkeywordsheading paired with it
            # (e.g. the heading was absent or already processed for a simple template).
            # Drop it — it is a template placeholder.
            title_block.pop(i)
            continue
        else:
            i += 1
    # No subtitle in this template: fall back to the first empty/'Author' slot.
    if not after_subtitle:
        i = 0
        while i < len(title_block):
            p = title_block[i]
            if get_style(p) == 'BodyText':
                cur = _para_text(p)
                if not author_done and (cur == '' or cur == 'Author'):
                    _replace_text(p, author)
                    author_done = True
                elif cur == 'Date':
                    if date_val and not date_done:
                        _replace_text(p, date_val)
                        date_done = True
                    elif not date_val:
                        title_block.pop(i)
                        continue
            i += 1

def _strip_bibliography_from_sections(sections):
    """Remove the section whose first block is a Heading1 'Bibliography' paragraph.

    DOCX content comes pre-split into (kind, blocks) sections at each Heading1,
    so the bibliography section is self-contained.  Returns (cleaned, found).
    Scans from the end so the bibliography (always last) is found quickly.
    The shared strip_bibliography helper is used for flat ODT element lists;
    this companion handles the DOCX section-list structure.
    """
    for i in range(len(sections) - 1, -1, -1):
        kind, blocks = sections[i]
        if not blocks:
            continue
        first = blocks[0]
        if first.tag == tag('p') and get_style(first) == 'Heading1':
            if _para_text(first).strip().lower() == 'bibliography':
                return list(sections[:i]), True
    return list(sections), False


def _make_akh_heading(label):
    h = etree.Element(tag('p'))
    ps = etree.SubElement(etree.SubElement(h, tag('pPr')), tag('pStyle'))
    ps.set(tag('val'), _ABSTRACTKEYWORDS_STYLE_ID)
    etree.SubElement(etree.SubElement(h, tag('r')), tag('t')).text = label
    return h

def _make_akh_body(chunk):
    b = etree.Element(tag('p'))
    ps = etree.SubElement(etree.SubElement(b, tag('pPr')), tag('pStyle'))
    ps.set(tag('val'), 'BodyText')
    _fill_markdown_text(b, chunk)   # operates on b in place; pPr already set
    return b

def _append_extra_sections(title_block, extra_sections):
    """Append an Abstractkeywordsheading heading + BodyText paragraph(s) for
    each (key, value) in extra_sections (abstract + note/sw-* properties).
    Shared loop lives in sw_merge_helpers.append_extra_sections."""
    append_extra_sections(title_block, extra_sections,
                          _make_akh_heading, _make_akh_body)

def _ensure_abstractkeywords_style(data):
    """Inject the Abstractkeywordsheading paragraph style into word/styles.xml
    if it is not already present. This ensures user-supplied templates that lack
    the style can still render dynamic note/sw-* sections correctly.

    The injected style: based on Normal, with bold + italic run formatting
    (default document font), matching how the style appears in the bundled
    templates.
    """
    styles_key = 'word/styles.xml'
    if styles_key not in data:
        return
    W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

    def wtag(n):
        return '{%s}%s' % (W_NS, n)

    root = etree.fromstring(data[styles_key])
    # Check if style already present.
    for s in root.findall(wtag('style')):
        if s.get(wtag('styleId')) == _ABSTRACTKEYWORDS_STYLE_ID:
            return  # already there
    # Build and append a minimal style definition.
    style = etree.SubElement(root, wtag('style'))
    style.set(wtag('type'), 'paragraph')
    style.set(wtag('styleId'), _ABSTRACTKEYWORDS_STYLE_ID)
    name_el = etree.SubElement(style, wtag('name'))
    name_el.set(wtag('val'), 'Abstract keywords heading')
    based = etree.SubElement(style, wtag('basedOn'))
    based.set(wtag('val'), 'Normal')
    rpr = etree.SubElement(style, wtag('rPr'))
    etree.SubElement(rpr, wtag('b'))
    etree.SubElement(rpr, wtag('bCs'))
    etree.SubElement(rpr, wtag('i'))
    etree.SubElement(rpr, wtag('iCs'))
    data[styles_key] = etree.tostring(
        root, xml_declaration=True, encoding='UTF-8', standalone=True)

def _replace_text(p, text):
    """Replace all content runs (including fldSimple fields) in a paragraph
    with a single run containing text."""
    for r in list(p.findall(tag('r'))):
        p.remove(r)
    for f in list(p.findall(tag('fldSimple'))):
        p.remove(f)
    r = etree.SubElement(p, tag('r'))
    t = etree.SubElement(r, tag('t'))
    t.text = text

_MD_RE = re.compile(r'[*_]{1,2}([^*_]+)[*_]{1,2}|`([^`]+)`')

def _fill_markdown_text(p, markdown):
    """Replace a paragraph's content with runs rendered from MARKDOWN text
    (e.g. the YAML abstract/title/subtitle may contain *italics* or
    **bold**). Obsidian doesn't render these in the property, so the merge
    converts them to real docx formatting. Falls back to plain text when
    there is no markdown formatting."""
    if not markdown or not re.search(r'[*_`]', markdown):
        _replace_text(p, markdown or '')
        return

    # Remove existing runs + fldSimple (keep the paragraph's pPr).
    for r in list(p.findall(tag('r'))):
        p.remove(r)
    for f in list(p.findall(tag('fldSimple'))):
        p.remove(f)

    def add_run(text, italic=False, bold=False):
        if text == '':
            return
        r = etree.SubElement(p, tag('r'))
        if italic or bold:
            rpr = etree.SubElement(r, tag('rPr'))
            if bold:
                b = etree.SubElement(rpr, tag('b'))
                bcs = etree.SubElement(rpr, tag('bCs'))
            if italic:
                i = etree.SubElement(rpr, tag('i'))
                ics = etree.SubElement(rpr, tag('iCs'))
        t = etree.SubElement(r, tag('t'))
        if text != text.strip():
            t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        t.text = text

    # Walk the markdown: split on *italic* / **bold** / `code` spans.
    pos = 0
    for m in _MD_RE.finditer(markdown):
        add_run(markdown[pos:m.start()])
        if m.group(2) is not None:
            add_run(m.group(2))            # `code` → plain (no monospace style in template)
        else:
            delim = m.group(0)[:m.group(0).find(m.group(1))]
            is_bold = '**' in delim or '__' in delim
            add_run(m.group(1), italic=not is_bold, bold=is_bold)
        pos = m.end()
    add_run(markdown[pos:])

def _set_fld_cached_rich(fld, text):
    """Replace a fldSimple's cached result runs with runs rendered from
    MARKDOWN text (the even-page header's short title may contain
    *italics*). Keeps the fldSimple element; replaces its <w:r> children."""
    for r in list(fld.findall(tag('r'))):
        fld.remove(r)
    if not text or not re.search(r'[*_`]', text):
        r = etree.SubElement(fld, tag('r'))
        t = etree.SubElement(r, tag('t'))
        t.text = text or ''
        return
    def add_run(text, italic=False, bold=False):
        if text == '':
            return
        r = etree.SubElement(fld, tag('r'))
        if italic or bold:
            rpr = etree.SubElement(r, tag('rPr'))
            if bold:
                etree.SubElement(rpr, tag('b'))
                etree.SubElement(rpr, tag('bCs'))
            if italic:
                etree.SubElement(rpr, tag('i'))
                etree.SubElement(rpr, tag('iCs'))
        t = etree.SubElement(r, tag('t'))
        if text != text.strip():
            t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        t.text = text
    pos = 0
    for m in _MD_RE.finditer(text):
        add_run(text[pos:m.start()])
        if m.group(2) is not None:
            add_run(m.group(2))
        else:
            delim = m.group(0)[:m.group(0).find(m.group(1))]
            is_bold = '**' in delim or '__' in delim
            add_run(m.group(1), italic=not is_bold, bold=is_bold)
        pos = m.end()
    add_run(text[pos:])

def rebuild_footnotes(template_path, input_path):
    """
    Build word/footnotes.xml: keep the template's separator/
    continuationSeparator entries (styled for the book), drop its sample
    footnotes, and append the REAL footnotes from the pandoc clean docx,
    RENUMBERED sequentially (1, 2, 3, …; separators stay -1/0).

    Word treats non-sequential footnote ids as "unreadable content", and
    pandoc's ids are sparse (20, 25, 27, …). So we renumber AND return the
    old→new mapping so the caller can rewrite the body's footnoteReference
    ids to match.

    Returns (footnotes_xml_bytes, {old_id: new_id}) or (None, None).
    """
    import zipfile
    W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    ET = etree

    with zipfile.ZipFile(template_path) as z:
        if 'word/footnotes.xml' not in z.namelist():
            return None, None
        tpl_root = ET.fromstring(z.read('word/footnotes.xml'))
    with zipfile.ZipFile(input_path) as z:
        if 'word/footnotes.xml' not in z.namelist():
            return None, None
        in_root = ET.fromstring(z.read('word/footnotes.xml'))

    # Template: keep only separator/continuationSeparator footnotes, with
    # their original ids (-1, 0 in the book template).
    keep = [fn for fn in tpl_root if fn.get('{%s}type' % W_NS) in
            ('separator', 'continuationSeparator')]

    # Real footnotes from the clean docx: renumber sequentially from 1.
    id_map = {}
    next_id = 1
    for fn in in_root:
        if fn.get('{%s}type' % W_NS) in ('separator', 'continuationSeparator'):
            continue
        old = fn.get('{%s}id' % W_NS)
        fn.set('{%s}id' % W_NS, str(next_id))
        if old is not None:
            id_map[old] = str(next_id)
        next_id += 1
        keep.append(fn)

    new_root = ET.Element('{%s}footnotes' % W_NS, nsmap=tpl_root.nsmap)
    for fn in keep:
        new_root.append(fn)

    # Strip whitespace-only text nodes inside element-only containers
    # (w:rPr, w:pPr, …). Pandoc's pretty-printed footnotes.xml puts
    # newlines/indentation inside <w:rPr>; lxml preserves them, and Word
    # treats text nodes in element-only containers as unreadable content.
    _strip_ws_text_nodes(new_root)

    return (ET.tostring(new_root, xml_declaration=True, encoding='UTF-8',
                        standalone=True), id_map)

def _remap_footnote_refs(doc, id_map):
    """Rewrite every w:footnoteReference w:id in the body per id_map (the
    old pandoc id → new sequential id)."""
    W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    def tag(n): return '{%s}%s' % (W_NS, n)
    for ref in doc.iter(tag('footnoteReference')):
        old = ref.get(tag('id'))
        if old in id_map:
            ref.set(tag('id'), id_map[old])

#: Elements whose text content is meaningful even when it is only whitespace —
#: <w:t xml:space="preserve"> </w:t> is the standalone-space run pandoc emits
#: between a word and a citation / emphasis. Never blank these.
_WS_TEXT_ELEMENTS = frozenset(
    '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}%s' % n
    for n in ('t', 'instrText', 'delText', 'delInstrText'))


def _strip_ws_text_nodes(root):
    """Remove whitespace-only text/tail nodes from element-only containers.
    Word treats raw whitespace text inside element-only elements (w:rPr,
    w:pPr, w:sectPr, …) as unreadable content — pandoc's pretty-printed
    XML leaves newlines/indentation there, and lxml preserves them. Tails
    between sibling elements are always noise in OOXML; element text is only
    noise when the element isn't one that carries literal text."""
    for el in list(root.iter()):
        if (el.text and el.text.strip() == ''
                and el.tag not in _WS_TEXT_ELEMENTS):
            el.text = None
        if el.tail and el.tail.strip() == '':
            el.tail = None

def _remove_orphan_bookmark_ends(doc):
    """Remove every w:bookmarkEnd whose matching w:bookmarkStart is absent
    from the document. Bookmark starts can be dropped with a skipped block
    (e.g. pandoc's 'preface' bookmark starts in the title block, which the
    merge skips) while the end survives deep in the content — an orphaned
    bookmarkEnd is "unreadable content" to Word."""
    W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    def tag(n): return '{%s}%s' % (W_NS, n)
    starts = set()
    for el in doc.iter(tag('bookmarkStart')):
        bid = el.get(tag('id'))
        if bid:
            starts.add(bid)
    for el in list(doc.iter(tag('bookmarkEnd'))):
        bid = el.get(tag('id'))
        if bid and bid not in starts:
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)

_CT_NS = 'http://schemas.openxmlformats.org/package/2006/content-types'
_REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
_CP_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/custom-properties'
_VT_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes'


def _write_zotero_prefs_docx(zipdata, csl_style):
    """Write Zotero document preferences (ZOTERO_PREF_1/_2/...) into
    docProps/custom.xml so a later Word/LibreOffice "Refresh" uses `csl_style`
    without prompting. Replaces any existing ZOTERO_PREF_* properties; creates
    custom.xml (and its content-type + relationship) when the template has none.
    `csl_style` is a Zotero style name or a path to a .csl file."""
    blob = zotero_pref_blob(csl_style_id(csl_style), field_type='Field')
    chunks = zotero_pref_chunks(blob)

    if 'docProps/custom.xml' in zipdata:
        root = etree.fromstring(zipdata['docProps/custom.xml'])
    else:
        root = etree.fromstring(
            ('<Properties xmlns="%s" xmlns:vt="%s"/>' % (_CP_NS, _VT_NS)).encode())
        _register_custom_xml_part(zipdata)

    # Drop existing ZOTERO_PREF_* and note the highest pid in use.
    max_pid = 1
    for prop in list(root):
        name = prop.get('name', '')
        try:
            max_pid = max(max_pid, int(prop.get('pid', '1')))
        except ValueError:
            pass
        if re.fullmatch(r'ZOTERO_PREF_\d+', name):
            root.remove(prop)

    for i, chunk in enumerate(chunks, start=1):
        prop = etree.SubElement(root, '{%s}property' % _CP_NS)
        prop.set('fmtid', '{D5CDD505-2E9C-101B-9397-08002B2CF9AE}')
        prop.set('pid', str(max_pid + i))
        prop.set('name', 'ZOTERO_PREF_%d' % i)
        lp = etree.SubElement(prop, '{%s}lpwstr' % _VT_NS)
        lp.text = chunk  # lxml XML-escapes on serialize

    zipdata['docProps/custom.xml'] = etree.tostring(
        root, xml_declaration=True, encoding='UTF-8', standalone=True)


def _register_custom_xml_part(zipdata):
    """Add the [Content_Types].xml override and docProps/_rels entry a
    freshly-created docProps/custom.xml needs."""
    ct_path = '[Content_Types].xml'
    if ct_path in zipdata:
        ct = etree.fromstring(zipdata[ct_path])
        if not any(o.get('PartName') == '/docProps/custom.xml'
                   for o in ct.findall('{%s}Override' % _CT_NS)):
            o = etree.SubElement(ct, '{%s}Override' % _CT_NS)
            o.set('PartName', '/docProps/custom.xml')
            o.set('ContentType',
                  'application/vnd.openxmlformats-officedocument.custom-properties+xml')
        zipdata[ct_path] = etree.tostring(ct, xml_declaration=True,
                                          encoding='UTF-8', standalone=True)

    rels_path = '_rels/.rels'
    if rels_path in zipdata:
        rels = etree.fromstring(zipdata[rels_path])
        if not any(r.get('Target') in ('docProps/custom.xml', '/docProps/custom.xml')
                   for r in rels.findall('{%s}Relationship' % _REL_NS)):
            used = {r.get('Id') for r in rels}
            n = 1
            while ('rId%d' % n) in used:
                n += 1
            r = etree.SubElement(rels, '{%s}Relationship' % _REL_NS)
            r.set('Id', 'rId%d' % n)
            r.set('Type', 'http://schemas.openxmlformats.org/officeDocument/'
                          '2006/relationships/custom-properties')
            r.set('Target', 'docProps/custom.xml')
        zipdata[rels_path] = etree.tostring(rels, xml_declaration=True,
                                            encoding='UTF-8', standalone=True)


def normalize_headers_footers(zipdata, short_title=None, author=None,
                              title=None, subtitle=None):
    """
    Patch header/footer XML parts to match the ScholarWeave header/footer spec:
      - even-page header (header1): Author – Short Title, LEFT aligned,
        WITHOUT the vestigial STYLEREF "Heading 1 - frontmatter"
      - odd-page headers (header2 TOC / header4 chapters): RIGHT aligned
      - footers with a PAGE field: CENTERED
      - first-page headers/footers: blank (already)
    Also replace document-specific docProps: dc:title, dc:creator,
    TitlesOfParts, 'Short Title', 'Subtitle'.
    """
    from lxml import etree
    W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    def tag(n): return '{%s}%s' % (W, n)

    # Set header alignment: header1 left; header2/header4 right.
    for hdr in ('word/header1.xml', 'word/header2.xml', 'word/header4.xml'):
        if hdr not in zipdata: continue
        root = etree.fromstring(zipdata[hdr])
        for p in root.iter(tag('p')):
            ppr = p.find(tag('pPr'))
            if ppr is None:
                ppr = etree.SubElement(p, tag('pPr'))
                p.move(ppr, 0)
            for jc in ppr.findall(tag('jc')):
                ppr.remove(jc)
            align = 'left' if hdr == 'word/header1.xml' else 'right'
            jc = etree.SubElement(ppr, tag('jc'))
            jc.set(tag('val'), align)
        # header1: remove the trailing STYLEREF "Heading 1 - frontmatter" field
        if hdr == 'word/header1.xml':
            for p in root.iter(tag('p')):
                for fldsimple in list(p.findall(tag('fldSimple'))):
                    instr = fldsimple.get(tag('instr')) or ''
                    if 'Heading 1 - frontmatter' in instr:
                        p.remove(fldsimple)
                runs = list(p.findall(tag('r')))
                for i in range(len(runs)):
                    r = runs[i]
                    it = r.find(tag('instrText'))
                    if it is not None and 'Heading 1 - frontmatter' in (it.text or ''):
                        for j in (i-1, i, i+1):
                            if 0 <= j < len(runs):
                                p.remove(runs[j])
                        break
        # header1: refresh the cached DOCPROPERTY results so the exported
        # file shows the REAL author / short title without needing F9
        # (the template's header1 caches "Joseph Hill" / "The Book's Short
        # Title" inside its fldSimple fields). A field may combine both:
        #   DOCPROPERTY "Author" - DOCPROPERTY "Short Title"
        # → cached "Author – Short Title".
        if hdr == 'word/header1.xml':
            for p in root.iter(tag('p')):
                for fld in list(p.findall(tag('fldSimple'))):
                    instr = fld.get(tag('instr')) or ''
                    if 'DOCPROPERTY' not in instr:
                        continue
                    has_author = 'Author' in instr
                    has_short = 'Short Title' in instr
                    if has_author and has_short and author and short_title:
                        new_text = f'{author} – {short_title}'
                    elif has_short and short_title:
                        new_text = short_title
                    elif has_author and author:
                        new_text = author
                    else:
                        continue
                    cached = ''.join(t.text or '' for t in fld.iter(tag('t')))
                    if cached != new_text:
                        _set_fld_cached_rich(fld, new_text)
        zipdata[hdr] = etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)

    # Set footer alignment: any footer with a PAGE field → centered.
    for ftr in zipdata:
        if not (ftr.startswith('word/footer') and ftr.endswith('.xml')):
            continue
        root = etree.fromstring(zipdata[ftr])
        has_page = False
        for instr in root.iter(tag('instrText')):
            if 'PAGE' in (instr.text or '').upper():
                has_page = True
                break
        if has_page:
            for p in root.iter(tag('p')):
                ppr = p.find(tag('pPr'))
                if ppr is None:
                    ppr = etree.SubElement(p, tag('pPr'))
                    p.move(ppr, 0)
                for jc in ppr.findall(tag('jc')):
                    ppr.remove(jc)
                jc = etree.SubElement(ppr, tag('jc'))
                jc.set(tag('val'), 'center')
        zipdata[ftr] = etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)

    # ── docProps: replace every document-specific placeholder ──────────────
    # core.xml: dc:title ← title, dc:creator ← author
    if 'docProps/core.xml' in zipdata:
        root = etree.fromstring(zipdata['docProps/core.xml'])
        dc = 'http://purl.org/dc/elements/1.1/'
        if title:
            for el in root.iter('{%s}title' % dc):
                el.text = _strip_markdown(title)
                break
        if author:
            for creator in root.iter('{%s}creator' % dc):
                creator.text = author
                break
        zipdata['docProps/core.xml'] = etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)

    # app.xml: TitlesOfParts ← title (Word shows this in the document map).
    # Target the lpstr INSIDE <TitlesOfParts> (the template also has a
    # "Title" lpstr inside HeadingPairs — replace only the ToP one).
    if 'docProps/app.xml' in zipdata:
        root = etree.fromstring(zipdata['docProps/app.xml'])
        vt_ns = 'http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes'
        if title:
            for top in root.iter():
                if top.tag.endswith('TitlesOfParts'):
                    for lp in top.iter('{%s}lpstr' % vt_ns):
                        lp.text = _strip_markdown(title)
                        break
                    break
        zipdata['docProps/app.xml'] = etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)

    # custom.xml: 'Short Title' ← short_title; 'Subtitle' ← subtitle or drop
    if 'docProps/custom.xml' in zipdata:
        root = etree.fromstring(zipdata['docProps/custom.xml'])
        vt = 'http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes'
        cp_ns = 'http://schemas.openxmlformats.org/officeDocument/2006/custom-properties'
        def set_custom(name, value, pid):
            for prop in root:
                if prop.tag == '{%s}property' % cp_ns and prop.get('name') == name:
                    for child in prop:
                        child.text = value
                    return True
            prop = etree.SubElement(root, '{%s}property' % cp_ns)
            prop.set('fmtid', '{D5CDD505-2E9C-101B-9397-08002B2CF9AE}')
            prop.set('pid', str(pid))
            prop.set('name', name)
            lp = etree.SubElement(prop, '{%s}lpwstr' % vt)
            lp.text = value
            return True

        if short_title:
            set_custom('Short Title', _strip_markdown(short_title), 4)
        if subtitle is not None:
            set_custom('Subtitle', _strip_markdown(subtitle), 5)
        else:
            # No subtitle → remove the placeholder 'Subtitle' property.
            for prop in list(root):
                if prop.tag == '{%s}property' % cp_ns and prop.get('name') == 'Subtitle':
                    root.remove(prop)
        zipdata['docProps/custom.xml'] = etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


def _merge_numbering(data, pdc_num_bytes, doc_root):
    """Merge pandoc's list numbering definitions into the template's
    numbering.xml and rewrite numId references in doc_root in-place.

    Pandoc generates abstractNum/num entries for bullet and numbered lists in
    its own numbering.xml.  Without this merge those numIds don't exist in the
    template's numbering, so Word falls back to its default (often numbered)
    for every list type, turning bullets into numbers.
    """
    pdc_num = etree.fromstring(pdc_num_bytes)

    tmpl_num_xml = data.get('word/numbering.xml')
    if not tmpl_num_xml:
        return  # template has no numbering.xml — skip (uncommon)
    tmpl_num = etree.fromstring(tmpl_num_xml)

    # Max abstractNumId / numId already in the template.
    max_abstract = max(
        (int(an.get(tag('abstractNumId'), -1))
         for an in tmpl_num.findall(tag('abstractNum'))),
        default=-1)
    max_num = max(
        (int(n.get(tag('numId'), -1))
         for n in tmpl_num.findall(tag('num'))),
        default=-1)

    # Map pandoc abstractNumId → new id; collect remapped elements.
    abstract_map = {}
    new_abstracts = []
    for an in pdc_num.findall(tag('abstractNum')):
        old_id = an.get(tag('abstractNumId'))
        if old_id is None:
            continue
        new_id = str(max_abstract + 1 + len(abstract_map))
        abstract_map[old_id] = new_id
        new_an = copy.deepcopy(an)
        new_an.set(tag('abstractNumId'), new_id)
        new_abstracts.append(new_an)

    # Map pandoc numId → new id.
    num_map = {}
    new_nums = []
    for n in pdc_num.findall(tag('num')):
        old_numid = n.get(tag('numId'))
        if old_numid is None or old_numid == '0':
            continue
        ani_el = n.find(tag('abstractNumId'))
        if ani_el is None:
            continue
        new_abstract = abstract_map.get(ani_el.get(tag('val')))
        if new_abstract is None:
            continue
        new_numid = str(max_num + 1 + len(num_map))
        num_map[old_numid] = new_numid
        new_n = copy.deepcopy(n)
        new_n.set(tag('numId'), new_numid)
        new_n.find(tag('abstractNumId')).set(tag('val'), new_abstract)
        new_nums.append(new_n)

    if not num_map:
        return

    # Insert abstractNums before the first existing w:num (OPC ordering req).
    children = list(tmpl_num)
    insert_at = next(
        (i for i, c in enumerate(children) if c.tag == tag('num')),
        len(children))
    for i, an in enumerate(new_abstracts):
        tmpl_num.insert(insert_at + i, an)
    for n in new_nums:
        tmpl_num.append(n)

    # Rewrite numId references in the merged document body.
    for numid_el in doc_root.iter(tag('numId')):
        old_val = numid_el.get(tag('val'))
        if old_val and old_val in num_map:
            numid_el.set(tag('val'), num_map[old_val])

    data['word/numbering.xml'] = etree.tostring(
        tmpl_num, xml_declaration=True, encoding='UTF-8', standalone=True)


# ── image size capping (DOCX) ─────────────────────────────────────────────────

def _write_docx(template_path, output_path, new_document_xml, new_footnotes_xml=None,
                short_title=None, author=None, title=None, subtitle=None,
                input_path=None, extra_style_ids=None, csl_style=None,
                restart_footnotes=True):
    """Write output docx = template parts with document.xml (and optionally
    footnotes.xml) replaced, and headers/footers/docProps normalized.

    Media: the clean docx's word/media/* files (figures pandoc embedded) are
    merged into the output, and the clean docx's image relationships are
    merged into the template's rels (renumbered to avoid collisions), so
    drawings referenced in the body resolve to the actual image files.

    extra_style_ids: styleIds the merged body now references that the template
    may not define (e.g. the ToF styles when the ToF was borrowed from
    document.docx) — copied in from the bundled document.docx.
    """
    with zipfile.ZipFile(template_path) as zin:
        names = zin.namelist()
        data = {n: zin.read(n) for n in names}
    _ensure_abstractkeywords_style(data)
    if extra_style_ids and 'word/styles.xml' in data:
        try:
            with zipfile.ZipFile(bundled_template('document.docx')) as _z:
                _src = _z.read('word/styles.xml')
            data['word/styles.xml'] = ensure_docx_styles(
                data['word/styles.xml'], extra_style_ids, _src)
        except Exception as e:
            print(f'WARNING: could not inject ToF styles: {e}')
    _normalize_footnote_restart(new_document_xml, data, restart_footnotes)
    data['word/document.xml'] = etree.tostring(
        new_document_xml, xml_declaration=True, encoding='UTF-8', standalone=True
    )
    if new_footnotes_xml is not None and 'word/footnotes.xml' in data:
        data['word/footnotes.xml'] = new_footnotes_xml

    # ── Merge media + image rels from the clean docx ──────────────────────
    if input_path:
        with zipfile.ZipFile(input_path) as zin:
            in_names = zin.namelist()
            # 1. Copy media files. Pandoc names them after the rId
            #    (word/media/rId24.png); Word dislikes that and renames them on
            #    repair, so give them plain image{N} names and remember the
            #    rename to patch the relationship Targets below.
            _media_rename = {}   # 'media/rId24.png' -> 'media/image1.png'
            _img_n = 1
            for n in in_names:
                if not (n.startswith('word/media/') and n not in data):
                    continue
                base = n.rsplit('/', 1)[-1]
                ext = base.rsplit('.', 1)[-1].lower() if '.' in base else 'png'
                newn = n
                if not re.match(r'^image\d+\.', base):
                    while True:
                        cand = 'word/media/image%d.%s' % (_img_n, ext)
                        _img_n += 1
                        if cand not in data:
                            newn = cand
                            break
                    _media_rename['media/' + base] = 'media/' + newn.rsplit('/', 1)[-1]
                data[newn] = zin.read(n)
            # 2. Merge image relationships: pandoc names media files after
            #    the rId (word/media/rId22.jpg) and embeds r:embed="rId22".
            #    Keep pandoc's own rIds when possible (no collision), else
            #    renumber to a free rId and rewrite the relationship + the
            #    body's r:embed/r:link references.
            if 'word/_rels/document.xml.rels' in in_names:
                in_rels = etree.fromstring(zin.read('word/_rels/document.xml.rels'))
            else:
                in_rels = None
            if in_rels is not None:
                rels_path = 'word/_rels/document.xml.rels'
                out_rels = etree.fromstring(data[rels_path])
                rel_ns = 'http://schemas.openxmlformats.org/package/2006/relationships'
                existing = set()
                for r in out_rels:
                    rid = r.get('Id')
                    if rid:
                        existing.add(rid)
                # Map pandoc rId → output rId (identity when free).
                rid_map = {}
                for r in in_rels:
                    rid = r.get('Id')
                    rtype = r.get('Type') or ''
                    if not rid or ('image' not in rtype and 'hyperlink' not in rtype):
                        continue
                    # Patch the Target for any media file we renamed above.
                    if 'image' in rtype and (r.get('Target') or '') in _media_rename:
                        r.set('Target', _media_rename[r.get('Target')])
                    target = r.get('Target') or ''
                    if rid not in existing:
                        out_rels.append(copy.deepcopy(r))
                        existing.add(rid)
                        rid_map[rid] = rid
                    else:
                        # renumber: rIdImg1/rIdHlnk1, … to avoid collisions
                        _pfx = 'rIdHlnk' if 'hyperlink' in rtype else 'rIdImg'
                        n = 1
                        new_rid = f'{_pfx}{n}'
                        while new_rid in existing:
                            n += 1
                            new_rid = f'{_pfx}{n}'
                        r2 = copy.deepcopy(r)
                        r2.set('Id', new_rid)
                        out_rels.append(r2)
                        existing.add(new_rid)
                        rid_map[rid] = new_rid
                # Rewrite body references to the remapped ids, merge pandoc's
                # numbering, THEN serialize document.xml ONCE. (Earlier code
                # serialized after the hyperlink rewrite and again after
                # _merge_numbering — the second write clobbered the first, so
                # colliding pandoc hyperlinks kept pointing at whatever the
                # template had at that rId, and their renamed rels became
                # orphans that Word flags as unreadable content.)
                W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
                R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
                if rid_map:
                    # Only image references (r:embed / r:link, anywhere) and
                    # hyperlink references (r:id on <w:hyperlink> ONLY) point at
                    # the merged pandoc rels. r:id elsewhere — footerReference,
                    # headerReference, etc. — belongs to the template and must
                    # NOT be touched, or the footer/header parts get orphaned
                    # and Word drops them.
                    _rmap = {k: v for k, v in rid_map.items() if v != k}
                    for el in new_document_xml.iter():
                        for attr in ('{%s}embed' % R, '{%s}link' % R):
                            if el.get(attr) in _rmap:
                                el.set(attr, _rmap[el.get(attr)])
                        if el.tag == '{%s}hyperlink' % W and el.get('{%s}id' % R) in _rmap:
                            el.set('{%s}id' % R, _rmap[el.get('{%s}id' % R)])
                # Merge pandoc's list numbering (bullets vs numbers) — mutates
                # new_document_xml's numId refs in place.
                if 'word/numbering.xml' in in_names:
                    _merge_numbering(
                        data, zin.read('word/numbering.xml'), new_document_xml)
                data['word/document.xml'] = etree.tostring(
                    new_document_xml, xml_declaration=True, encoding='UTF-8',
                    standalone=True)

                # Drop UNREFERENCED image AND hyperlink relationships: the
                # template's leftover sample image (rId… → media/image1.jpeg),
                # its sample hyperlink (rId… → http://example.com), and any
                # pandoc hyperlink whose <w:hyperlink> landed in a dropped
                # block. Word flags a relationship that nothing references as
                # unreadable content. Structural rels (styles/settings/
                # numbering/fontTable/theme/footnotes/endnotes/header/footer)
                # are referenced by OPC convention, not by r:id, and are never
                # of type image/hyperlink — so they are untouched.
                body_text = data['word/document.xml'].decode('utf-8')
                used = set(re.findall(r'r:(?:embed|link|id)="([^"]+)"', body_text))
                # Header/footer parts can reference image/hyperlink rels too.
                for _pn, _pd in data.items():
                    if re.match(r'word/(header|footer)\d+\.xml$', _pn):
                        used |= set(re.findall(
                            r'r:(?:embed|link|id)="([^"]+)"',
                            _pd.decode('utf-8', 'replace')))
                dropped_targets = set()
                for r in list(out_rels):
                    rid = r.get('Id')
                    rtype = r.get('Type') or ''
                    if rid and ('image' in rtype or 'hyperlink' in rtype) \
                            and rid not in used:
                        tgt = r.get('Target') or ''
                        if tgt and 'image' in rtype:
                            dropped_targets.add(tgt)
                        out_rels.remove(r)
                used_targets = {r.get('Target') for r in out_rels
                                if 'image' in (r.get('Type') or '')
                                and r.get('Target')}
                for tgt in dropped_targets:
                    part = tgt if tgt.startswith('word/') else 'word/' + tgt
                    if part in data and tgt not in used_targets:
                        del data[part]
                data[rels_path] = etree.tostring(
                    out_rels, xml_declaration=True, encoding='UTF-8',
                    standalone=True)

            # The endnote chain (word/endnotes.xml + settings.xml <w:endnotePr>
            # + [Content_Types] + the .rels entry) is left EXACTLY as the
            # template ships it. The template carries only the separator /
            # continuationSeparator entries (ids -1 / 0), which is the correct
            # minimal setup. Earlier code stripped <w:endnotePr> from
            # settings.xml while keeping endnotes.xml — that mismatch is itself
            # "unreadable content" and Word re-adds the element on repair.

            # Prune [Content_Types].xml: remove <Default Extension> entries
            # for file extensions no longer present in the package. The
            # template declares "jpeg" for its sample image; after dropping
            # that image, an orphaned Default (extension with no matching
            # part) is "unreadable content" to Word — it replaced ours with
            # jpg→application/octet-stream during repair.
            present_exts = set()
            for part in data:
                if part.startswith('word/media/'):
                    ext = part.rsplit('.', 1)[-1].lower() if '.' in part else ''
                    if ext:
                        present_exts.add(ext)
            ct_path = '[Content_Types].xml'
            if ct_path in data:
                ct_root = etree.fromstring(data[ct_path])
                CT_NS = 'http://schemas.openxmlformats.org/package/2006/content-types'
                removed = False
                # Drop Default entries for media extensions no longer present.
                # Only prune image/media types — never 'xml' or 'rels', which
                # are required OPC defaults; removing them causes Word to flag
                # the package as unreadable content and add them back on repair.
                _OPC_REQUIRED = {'xml', 'rels'}
                for dflt in list(ct_root):
                    if dflt.tag == '{%s}Default' % CT_NS:
                        ext = (dflt.get('Extension') or '').lower()
                        if ext and ext not in present_exts and ext not in _OPC_REQUIRED:
                            ct_root.remove(dflt)
                            removed = True
                # Ensure every present media extension is declared (Word
                # treats an undeclared media part as unreadable content; its
                # own repair adds jpg→application/octet-stream).
                declared = set()
                for dflt in ct_root:
                    if dflt.tag == '{%s}Default' % CT_NS:
                        declared.add((dflt.get('Extension') or '').lower())
                for ext in sorted(present_exts):
                    if ext not in declared:
                        dflt = etree.SubElement(ct_root, '{%s}Default' % CT_NS)
                        dflt.set('Extension', ext)
                        dflt.set('ContentType',
                                 'image/jpeg' if ext in ('jpg', 'jpeg') else
                                 'image/png' if ext == 'png' else
                                 'image/gif' if ext == 'gif' else
                                 'application/octet-stream')
                        removed = True
                if removed:
                    data[ct_path] = etree.tostring(
                        ct_root, xml_declaration=True, encoding='UTF-8',
                        standalone=True)

    normalize_headers_footers(data, short_title=short_title, author=author,
                              title=title, subtitle=subtitle)
    if csl_style:
        _write_zotero_prefs_docx(data, csl_style)
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zout:
        for n in data:
            zout.writestr(n, data[n])

def make_toc_paragraph():
    """Build a TOC field paragraph: 'Table of Contents' style heading with a
    TOC field. Returns a <w:p> element."""
    p = etree.Element(tag('p'))
    ppr = etree.SubElement(p, tag('pPr'))
    pstyle = etree.SubElement(ppr, tag('pStyle'))
    pstyle.set(tag('val'), 'TOCHeading')  # may not exist in template; harmless
    r1 = etree.SubElement(p, tag('r'))
    fc = etree.SubElement(r1, tag('fldChar'))
    fc.set(tag('fldCharType'), 'begin')
    r2 = etree.SubElement(p, tag('r'))
    it = etree.SubElement(r2, tag('instrText'))
    it.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    it.text = ' TOC \\o "1-3" \\h \\z \\u '
    r3 = etree.SubElement(p, tag('r'))
    fc2 = etree.SubElement(r3, tag('fldChar'))
    fc2.set(tag('fldCharType'), 'separate')
    r4 = etree.SubElement(p, tag('r'))
    t = etree.SubElement(r4, tag('t'))
    t.text = 'Right-click to update field.'
    r5 = etree.SubElement(p, tag('r'))
    fc3 = etree.SubElement(r5, tag('fldChar'))
    fc3.set(tag('fldCharType'), 'end')
    return p

def inject_toc(template_body, before_heading='Introduction'):
    """Insert a TOC field paragraph into the body just before the first
    main-text heading (Introduction/Chapter 1)."""
    children = list(template_body)
    idx = None
    for i, c in enumerate(children):
        if c.tag == tag('p'):
            style = get_style(c)
            if style == 'Heading1':
                text = _para_text(c)
                if is_main_start(text):
                    idx = i
                    break
    toc_p = make_toc_paragraph()
    if idx is not None:
        template_body.insert(idx, toc_p)
    else:
        template_body.append(toc_p)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--template', required=True)
    ap.add_argument('--input', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--title', default=None)
    ap.add_argument('--author', default=None)
    ap.add_argument('--subtitle', default=None)
    ap.add_argument('--date', default=None, dest='date_val')
    ap.add_argument('--toc', action='store_true')
    ap.add_argument('--list-of-figures', action='store_true', dest='tof',
                    help='Include a table of figures (only when the doc has figures)')
    ap.add_argument('--shorttitle', default=None)
    ap.add_argument('--basename', default=None)
    ap.add_argument('--abstract', default=None)
    ap.add_argument('--extra-sections', default=None, dest='extra_sections',
                    help='JSON array of [key, value] pairs for note/sw-* sections')
    ap.add_argument('--note', default=None,
                    help='Backward-compat: single note value (treated as [["note", value]])')
    ap.add_argument('--new-page-headings', action='store_true', default=True,
                    dest='new_page_headings',
                    help='Start each top-level heading on a new page (default: on)')
    ap.add_argument('--no-new-page-headings', action='store_true',
                    dest='no_new_page_headings',
                    help='Do not insert page breaks before top-level headings')
    ap.add_argument('--no-global-footnotes', action='store_true',
                    dest='no_global_footnotes',
                    help='Restart footnote numbering at each top-level heading/chapter')
    ap.add_argument('--global-footnotes', action='store_true',
                    dest='global_footnotes',
                    help='Continuous footnote numbering across the whole document')
    ap.add_argument('--no-generated-date', action='store_true',
                    dest='no_generated_date',
                    help="Don't insert today's date when the doc has no date property")
    ap.add_argument('--roman-frontmatter', action='store_true', dest='roman_frontmatter',
                    help='Roman-numeral frontmatter page numbers, arabic from the reset heading')
    ap.add_argument('--page1-starts-with', default='', dest='page1_starts_with',
                    help='Heading text that begins arabic page 1 (blank = auto)')
    ap.add_argument('--static-citations', action='store_true',
                    dest='static_citations',
                    help='Pandoc already rendered final citations + a real '
                         'bibliography via --citeproc (PDF path) — preserve '
                         'it instead of stripping and replacing with a live '
                         'Zotero field placeholder')
    ap.add_argument('--csl-style', default=None, dest='csl_style',
                    help='Write this citation style (a Zotero style name or a '
                         '.csl path) into the output\'s Zotero document '
                         'preferences (ZOTERO_PREF_*), replacing any the '
                         'template carries.')
    args = ap.parse_args()
    extra_sections = None
    if args.extra_sections:
        try:
            extra_sections = json.loads(args.extra_sections)
        except (json.JSONDecodeError, ValueError):
            extra_sections = None
    elif args.note:
        extra_sections = [['note', args.note]]
    new_page_headings = not args.no_new_page_headings
    restart_footnotes = args.no_global_footnotes  # --no-global-footnotes = restart per chapter
    merge(args.template, args.input, args.output, args.title, args.author,
          args.subtitle, args.date_val, args.toc, args.tof, args.shorttitle,
          args.basename, args.abstract, extra_sections,
          new_page_headings=new_page_headings,
          restart_footnotes=restart_footnotes,
          generate_date=not args.no_generated_date,
          roman_frontmatter=args.roman_frontmatter,
          page1_starts_with=args.page1_starts_with,
          static_citations=args.static_citations,
          csl_style=args.csl_style)
    print(f'Merged: {args.output}')

if __name__ == '__main__':
    main()
