#!/usr/bin/env python3
"""
lc_export_merge.py — Linked Citations export merge.

Takes the clean docx produced by pandoc (via the lc-*.lua filters) and the
target Export Template (book2/article2/document), and copies the pandoc body
into the template's XML, RETAINING the template's frontmatter structure:
  - title block (Title/Subtitle/Author/Date/Abstract/Note) filled from YAML
    with cover-value resolution (Title = before ':' → whole title → basename
    before '-'/'–' → full basename; Subtitle = after ':' → 'subtitle' prop →
    none; Author = 'author' → "Joseph Hill"; Date = current date)
  - the template's TOC section (heading + field para) is preserved
  - the template's ToF section is preserved ONLY if the input has figures
  - frontmatter content sections get lowerRoman page numbers; the first main
    section (Introduction/Chapter 1) gets arabic start=1; later chapters
    continue arabic; per-section footnote restart
  - numbered chapters ("Chapter N:" or "N." Heading 1) get Word numbering
    (numPr) instead of literal text, so Word assigns chapter numbers that
    figure-caption fields can reference
  - figure captions become "Figure {chapter}.{n}. Description" via
    STYLEREF/SEQ fields, with an Alt-text paragraph in "Caption - Alt-text"
  - headers/footers: first section defines them, rest are "same as previous"

Reuses hardened helpers from iafr_template_pipeline.py (style mapping,
cleanup, paraId minting) rather than reinventing them.

Usage:
    python3 scripts/lc_export_merge.py \
        --template "Export Templates/book2.docx" \
        --input "path/to/clean.docx" \
        --output "path/to/final.docx" \
        [--title "Title"] [--subtitle "Subtitle"] [--author "Name"]
        [--date "Month D, YYYY"] [--basename "file stem"]
"""

import argparse
import copy
import datetime
import re
import sys
import os
import zipfile
from lxml import etree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lc_merge_helpers import (
    tag, get_style, set_style, collect_ids, mint_id, ensure_para_id,
)

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

# ── cover-value resolution ───────────────────────────────────────────────────

def resolve_cover(title, subtitle, author, date_val, basename):
    """Resolve cover values per spec:
      Title    = whole 'title' property when a 'subtitle' property is given
                 (e.g. "Title: A Study of Important Things" stays whole, so
                 the document can have subtitle "A manuscript submitted to
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

# ── section-kind classification ──────────────────────────────────────────────

# Heading 1 text that marks the START of main text (arabic page numbers).
# Everything before the first such heading is frontmatter (roman).
_MAIN_START_RE = re.compile(
    r'^(introduction|chapter\s+\d+|prologue|part\s+\d+)\b', re.IGNORECASE
)

def is_main_start(text):
    return bool(_MAIN_START_RE.match(text.strip()))

def is_toc_heading(text):
    return text.strip().lower() == 'table of contents'

def is_tof_heading(text):
    return text.strip().lower() == 'table of figures'

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
    body = doc.find(tag('body'))
    children = list(body)

    # 1. Title block = children before the first sectPr-carrying paragraph.
    first_sect_idx = None
    for i, c in enumerate(children):
        if _is_sect_para(c):
            first_sect_idx = i
            break
    if first_sect_idx is None:
        first_sect_idx = len(children)
    title_block = [copy.deepcopy(c) for c in children[:first_sect_idx]]

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
        if toc_heading is None and style == 'Heading1-excludefromTOC':
            toc_heading = copy.deepcopy(c)
        elif toc_heading is None and style == 'Heading1' and is_toc_heading(text):
            toc_heading = copy.deepcopy(c)
        elif tof_heading is None and style == 'Heading1' and is_tof_heading(text):
            tof_heading = copy.deepcopy(c)
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
    }

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

def make_section_break(sectpr_kind, rsid='00DE2936'):
    """Build a paragraph whose pPr carries the given sectPr — a section break.
    Ensures footnote numbering restarts per section (eachSect) so chapters
    don't number footnotes document-wide."""
    p = etree.Element(tag('p'))
    ppr = etree.SubElement(p, tag('pPr'))
    sect = copy.deepcopy(sectpr_kind)
    sect.set(tag('rsidR'), rsid)
    sect.set(tag('rsidSect'), rsid)
    # Ensure per-section footnote restart.
    fnpr = sect.find(tag('footnotePr'))
    if fnpr is None:
        fnpr = etree.Element(tag('footnotePr'))
        pos = etree.SubElement(fnpr, tag('pos'))
        pos.set(tag('val'), 'beneathText')
        sect.insert(0, fnpr)
    if fnpr.find(tag('numRestart')) is None:
        nr = etree.SubElement(fnpr, tag('numRestart'))
        nr.set(tag('val'), 'eachSect')
    ppr.append(sect)
    return p

# ── body parsing ─────────────────────────────────────────────────────────────

def parse_body(doc):
    """Return the body element and its children list."""
    body = doc.find(tag('body'))
    return body, list(body)

def classify_blocks(children):
    """
    Walk pandoc body children. Return list of (kind, blocks) where kind is
    'frontmatter' or 'main', splitting at Heading 1 boundaries:
      - a Heading 1 "Table of Contents"/"Table of Figures" stays frontmatter
      - the first Heading 1 matching is_main_start begins 'main'
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
            elif not in_main and is_main_start(text):
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

_CHAPTER_NUM_RE = re.compile(r'^(?:chapter\s+)?\d+[.):]?\s+(.*)$', re.IGNORECASE)

def apply_chapter_numbering(sections, numid):
    """For every Heading 1 whose text looks like a numbered chapter
    ('Chapter 1: Title' or '1. Title'), strip the literal number/prefix and
    add Word numbering (numPr → numid) so Word supplies the chapter number.
    Non-numbered headings (Preface, Introduction, Conclusion, ...) keep plain
    Heading 1. Only Heading 1 is treated; sections (Heading 2+) never get
    chapter numbers.
    """
    if not numid:
        return
    for kind, blocks in sections:
        for b in blocks:
            if b.tag != tag('p') or get_style(b) != 'Heading1':
                continue
            text = _para_text(b)
            m = _CHAPTER_NUM_RE.match(text)
            if not m or m.group(1) == text:
                continue
            # Replace text with the number-stripped remainder.
            for r in list(b.findall(tag('r'))):
                b.remove(r)
            r = etree.SubElement(b, tag('r'))
            t = etree.SubElement(r, tag('t'))
            t.text = m.group(1)
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

def _strip_figure_prefix(desc):
    """Remove a leading 'Figure', 'Figure 2', 'Figure 2.4', 'Figure.' from a
    caption description (disregard any figure number in the original)."""
    return re.sub(r'^\s*figure\s*\d+(?:[.:-]\d+)*\s*[.:-]?\s*',
                  '', desc, flags=re.IGNORECASE).strip()

def _make_figure_caption(description):
    """Build a Caption-style paragraph:
    'Figure {STYLEREF 1 \s}.{SEQ Figure \* ARABIC \s 1}. {description}'
    Text runs that lead/trail whitespace need xml:space="preserve" or Word
    trims them ('Figure 0.1' would render 'Figure0.1')."""
    p = etree.Element(tag('p'))
    ppr = etree.SubElement(p, tag('pPr'))
    ps = etree.SubElement(ppr, tag('pStyle'))
    ps.set(tag('val'), 'Caption')
    r = etree.SubElement(p, tag('r'))
    t = etree.SubElement(r, tag('t'))
    t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    t.text = 'Figure '
    _make_field_run(p, 'STYLEREF 1 \\s', '0')
    r = etree.SubElement(p, tag('r'))
    t = etree.SubElement(r, tag('t'))
    t.text = '.'
    _make_field_run(p, 'SEQ Figure \\* ARABIC \\s 1', '1')
    r = etree.SubElement(p, tag('r'))
    t = etree.SubElement(r, tag('t'))
    t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    t.text = '. ' + description
    return p

def _make_alttext_para(alt_text):
    """Build a 'Caption - Alt-text' style paragraph: 'Alt-text.' + text, or a
    blank 'Alt-text.' label when there is no alt text."""
    p = etree.Element(tag('p'))
    ppr = etree.SubElement(p, tag('pPr'))
    ps = etree.SubElement(ppr, tag('pStyle'))
    ps.set(tag('val'), 'caption-Alt-text')
    r = etree.SubElement(p, tag('r'))
    t = etree.SubElement(r, tag('t'))
    t.text = 'Alt-text.' + (' ' + alt_text if alt_text else '')
    return p

_ALT_TEXT_RE = re.compile(r'^\s*alt[- ]?text\s*[:.-]?\s*(.*)$', re.IGNORECASE)

def transform_figures(sections):
    """Walk content sections; convert pandoc figure blocks into the template's
    caption layout:
      - CaptionedFigure (image) → BodyText (drawing kept)
      - ImageCaption (text)     → Caption with STYLEREF/SEQ fields
      - following 'Alt-text: …' BodyText → 'Caption - Alt-text' (normalized)
        or a blank 'Alt-text.' label if none
      - TableCaption            → plain Caption (no fields, not in ToF)
    Returns True if any figures were found (→ keep the template's ToF section).
    """
    has_figures = False
    for kind, blocks in sections:
        out = []
        i = 0
        while i < len(blocks):
            b = blocks[i]
            if b.tag != tag('p'):
                out.append(b)
                i += 1
                continue
            style = get_style(b)
            if style == 'CaptionedFigure':
                set_style(b, 'BodyText')
                out.append(b)
                has_figures = True
                i += 1
            elif style == 'ImageCaption':
                # Description: use a following "Figure N. …" BodyText if
                # present (the vault convention: ![[img]] then a separate
                # "Figure 1. …" caption line); otherwise the ImageCaption
                # text itself. Any figure number in the description is
                # disregarded (Word supplies 0.1/1.1/… via the fields).
                desc = _strip_figure_prefix(_para_text(b))
                i += 1
                nxt = blocks[i] if i < len(blocks) else None
                if nxt is not None and nxt.tag == tag('p') \
                        and get_style(nxt) in ('BodyText', 'FirstParagraph') \
                        and re.match(r'^\s*figure\b', _para_text(nxt), re.IGNORECASE):
                    desc = _strip_figure_prefix(_para_text(nxt))
                    i += 1
                out.append(_make_figure_caption(desc))
                has_figures = True
                # Consume a following 'Alt-text: …' BodyText paragraph.
                alt_text = None
                if i < len(blocks) and blocks[i].tag == tag('p'):
                    nxt_style = get_style(blocks[i])
                    if nxt_style in ('BodyText', 'FirstParagraph'):
                        m = _ALT_TEXT_RE.match(_para_text(blocks[i]))
                        if m:
                            alt_text = m.group(1).strip()
                            i += 1
                out.append(_make_alttext_para(alt_text))
            elif style == 'TableCaption':
                set_style(b, 'Caption')
                out.append(b)
                i += 1
            else:
                out.append(b)
                i += 1
        blocks[:] = out
    return has_figures

# ── figure sizing ───────────────────────────────────────────────────────────

# Full text width = 6.5 in (8.5 in page − 2×1 in margins), in EMU
# (914400 EMU = 1 in).
_FULL_TEXT_WIDTH_EMU = int(6.5 * 914400)

def _resize_figures_full_width(doc):
    """Scale every drawing in `doc` to the full text width (6.5 in),
    preserving the source aspect ratio.

    Pandoc embeds figures at their native size (pixel size ÷ DPI of the
    source image), which is almost never the full text width. Word uses the
    `wp:extent` (and the graphic's `a:extent`) for the rendered size, so we
    set cx = full text width and scale cy proportionally on BOTH extent
    elements (they must agree or Word's zoom-to-fit can disagree).
    """
    WP = '{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}'
    A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
    target = _FULL_TEXT_WIDTH_EMU
    # wp:extent (inline/anchor), a:extent (graphic), and a:ext (inside the
    # pic:spPr a:xfrm) all carry cx/cy and should agree — Word uses
    # wp:extent for the rendered size, but the drawing-level a:ext is what
    # some inspectors (and LibreOffice) read.
    for el in doc.iter():
        if el.tag not in (WP + 'extent', A + 'extent', A + 'ext'):
            continue
        cx = el.get('cx')
        cy = el.get('cy')
        if not cx or not cy:
            continue
        try:
            cx_i = int(cx)
            cy_i = int(cy)
        except ValueError:
            continue
        if cx_i <= 0 or cy_i <= 0 or cx_i == target:
            continue
        # Scale cy to preserve aspect ratio at the new full width.
        new_cy = int(round(cy_i * target / cx_i))
        el.set('cx', str(target))
        el.set('cy', str(new_cy))

# ── build output body ────────────────────────────────────────────────────────

def build_body(template_body, layout, sections, used, has_figures, toc=False):
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

    # Clear the template body.
    for c in list(template_body):
        template_body.remove(c)

    # 1. Title block + its own section closer.
    for p in layout['title_block']:
        template_body.append(p)
    template_body.append(make_section_break(kinds['title']))

    # 2. TOC section — heading + fresh field (from the template's code),
    #    then its closer. Only when the user wants a TOC; --no-toc removes
    #    the template's TOC section entirely (the checkbox overrides the
    #    template-aware default).
    if toc and layout['toc_heading'] is not None and layout['toc_instr']:
        template_body.append(layout['toc_heading'])
        template_body.append(make_field_paragraph('TOC1', layout['toc_instr']))
        template_body.append(make_section_break(kinds['toc']))

    # 3. ToF section — only when figures are present.
    if has_figures and layout['tof_heading'] is not None and layout['tof_instr']:
        template_body.append(layout['tof_heading'])
        template_body.append(make_field_paragraph('TableofFigures', layout['tof_instr']))
        template_body.append(make_section_break(kinds['tof']))

    # 4. Content sections.
    seen_main = False
    content_sections = []
    skipped_bookmark_ids = set()
    for kind, blocks in sections:
        # Skip the leading title/author block if it's the first section.
        if content_sections == [] and blocks and blocks[0].tag == tag('p') \
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
        for b in blocks:
            # Remap pandoc-specific styles to template names.
            if b.tag == tag('p'):
                st = get_style(b)
                remap = {
                    # Pandoc emits blockquotes as "Block Text"; the book
                    # template styles them "Block quote".
                    'BlockText': 'Blockquote',
                    'FirstParagraph': 'BodyText',
                }
                if st in remap:
                    set_style(b, remap[st])
            template_body.append(b)
        if idx == len(content_sections) - 1:
            break  # final section closed by final_sect
        if kind == 'frontmatter':
            brk = make_section_break(kinds['roman'])
        elif not seen_main:
            brk = make_section_break(kinds['first_main'])
            seen_main = True
        else:
            brk = make_section_break(kinds['continue'])
        template_body.append(brk)

    # 5. Bibliography section (from the template), if present — a Heading 1
    #    "Bibliography" followed by a fresh, EMPTY Zotero bibliography field
    #    (no cached content; Zotero populates it on refresh). The template's
    #    own cached bibliography entries are dropped.
    if layout['bibl_heading'] is not None:
        # If the previous section's break hasn't been emitted (last content
        # section was closed by the final sectPr), we still want the
        # bibliography in its own section — emit a continue break before it.
        template_body.append(make_section_break(kinds['continue']))
        template_body.append(layout['bibl_heading'])
        bibl_field = make_field_paragraph(
            'Bibliography',
            layout['bibl_instr'] or
            'ADDIN ZOTERO_BIBL {"uncited":[],"omitted":[],"custom":[]} CSL_BIBLIOGRAPHY',
            'Refresh Zotero to view this content.')
        template_body.append(bibl_field)

    # 6. Final body sectPr.
    template_body.append(final_sect)

# ── merge ────────────────────────────────────────────────────────────────────

def merge(template_path, input_path, output_path, title=None, author=None,
          subtitle=None, date_val=None, toc=False, short_title=None,
          basename=None, abstract=None, note=None):
    with zipfile.ZipFile(template_path) as z:
        tmpl_doc = etree.fromstring(z.read('word/document.xml'))
    tmpl_body = tmpl_doc.find(tag('body'))
    used = collect_ids(tmpl_doc)

    with zipfile.ZipFile(input_path) as z:
        pdc_doc = etree.fromstring(z.read('word/document.xml'))
    pdc_body, pdc_children = parse_body(pdc_doc)

    layout = extract_template_layout(template_path)

    sections = classify_blocks(pdc_children)

    # Figure captions → template caption layout; decides ToF presence.
    has_figures = transform_figures(sections)

    # Chapter numbering: strip literal "Chapter N:" and let Word number.
    apply_chapter_numbering(sections, layout['chapter_numid'])

    # Cover values (Title/Subtitle/Author/Date resolution).
    title, subtitle, author, date_val = resolve_cover(
        title, subtitle, author, date_val, basename)

    # Fill the title block (layout['title_block'] are the copies build_body
    # will append, so fill them in place).
    _fill_title_block(layout['title_block'], title, subtitle, author, date_val,
                      abstract=abstract, note=note)

    build_body(tmpl_body, layout, sections, used, has_figures, toc=toc)

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

    # Scale figures to full text width (6.5 in), preserving aspect ratio —
    # pandoc embeds images at native size, which is rarely the text width.
    _resize_figures_full_width(tmpl_doc)
    if new_footnotes is not None:
        fn_root = etree.fromstring(new_footnotes)
        _resize_figures_full_width(fn_root)
        new_footnotes = etree.tostring(fn_root, xml_declaration=True,
                                       encoding='UTF-8', standalone=True)

    # Save via python-docx-like zip write (preserve all other parts).
    _write_docx(template_path, output_path, tmpl_doc, new_footnotes,
                short_title=short_title, author=author, title=title,
                subtitle=subtitle, input_path=input_path)

def _fill_title_block(title_block, title, subtitle, author, date_val,
                      abstract=None, note=None):
    """Replace placeholder text in the template title block (a list of deep
    copies used by build_body):
      - Title / Subtitle styles → resolved values
      - Subtitle paragraph REMOVED when there is no subtitle
      - first non-Date Body Text after Subtitle → author
      - Body Text 'Date' → date
      - Abstract / Note heading+text sections dropped when there is no
        content (template placeholder text never survives)
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
        elif style == 'BodyText' and after_subtitle:
            if not author_done and cur != 'Date':
                _replace_text(p, author)
                author_done = True
            elif not date_done and cur == 'Date':
                _replace_text(p, date_val)
                date_done = True
            i += 1
        elif style == 'Abstractkeywordsheading':
            # Drop the section (heading + following BodyText) if no content.
            has_content = (cur.strip().lower() == 'abstract' and abstract) or \
                          (cur.strip().lower() == 'note' and note)
            if has_content:
                value = abstract if cur.strip().lower() == 'abstract' else note
                _replace_text(p, cur)          # keep heading text
                # replace the next BodyText (the abstract/note body),
                # converting any markdown formatting to docx runs.
                j = i + 1
                while j < len(title_block) and get_style(title_block[j]) == 'BodyText':
                    _fill_markdown_text(title_block[j], value)
                    break
                i += 1
            else:
                # Drop this heading AND the following BodyText paragraph(s).
                title_block.pop(i)
                while i < len(title_block) and get_style(title_block[i]) == 'BodyText':
                    title_block.pop(i)
                continue
        else:
            i += 1
    # No subtitle in this template: fall back to the first empty/'Author' slot.
    if not after_subtitle:
        for p in title_block:
            if get_style(p) == 'BodyText':
                cur = _para_text(p)
                if not author_done and (cur == '' or cur == 'Author'):
                    _replace_text(p, author)
                    author_done = True
                elif not date_done and cur == 'Date':
                    _replace_text(p, date_val)
                    date_done = True

def _strip_markdown(text):
    """Remove markdown delimiters (*italic*, **bold**, `code`) from a string,
    keeping the content. Used for document properties (docProps), which are
    plain-text only."""
    if not text:
        return text or ''
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'_([^_]+)_', r'\1', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    return text

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

def _strip_ws_text_nodes(root):
    """Remove whitespace-only text/tail nodes from element-only containers.
    Word treats raw whitespace text inside element-only elements (w:rPr,
    w:pPr, w:sectPr, …) as unreadable content — pandoc's pretty-printed
    XML leaves newlines/indentation there, and lxml preserves them."""
    for el in list(root.iter()):
        if el.text and el.text.strip() == '':
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

def normalize_headers_footers(zipdata, short_title=None, author=None,
                              title=None, subtitle=None):
    """
    Patch header/footer XML parts to match the Linked Citations spec:
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

def _write_docx(template_path, output_path, new_document_xml, new_footnotes_xml=None,
                short_title=None, author=None, title=None, subtitle=None,
                input_path=None):
    """Write output docx = template parts with document.xml (and optionally
    footnotes.xml) replaced, and headers/footers/docProps normalized.

    Media: the clean docx's word/media/* files (figures pandoc embedded) are
    merged into the output, and the clean docx's image relationships are
    merged into the template's rels (renumbered to avoid collisions), so
    drawings referenced in the body resolve to the actual image files.
    """
    with zipfile.ZipFile(template_path) as zin:
        names = zin.namelist()
        data = {n: zin.read(n) for n in names}
    data['word/document.xml'] = etree.tostring(
        new_document_xml, xml_declaration=True, encoding='UTF-8', standalone=True
    )
    if new_footnotes_xml is not None and 'word/footnotes.xml' in data:
        data['word/footnotes.xml'] = new_footnotes_xml

    # ── Merge media + image rels from the clean docx ──────────────────────
    if input_path:
        with zipfile.ZipFile(input_path) as zin:
            in_names = zin.namelist()
            # 1. Copy media files (skip ones already present, e.g. template's
            #    own sample image).
            for n in in_names:
                if n.startswith('word/media/') and n not in data:
                    data[n] = zin.read(n)
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
                    if 'image' not in rtype or not rid:
                        continue
                    target = r.get('Target') or ''
                    if rid not in existing:
                        out_rels.append(copy.deepcopy(r))
                        existing.add(rid)
                        rid_map[rid] = rid
                    else:
                        # renumber: rIdImg1, rIdImg2, …
                        n = 1
                        new_rid = f'rIdImg{n}'
                        while new_rid in existing:
                            n += 1
                            new_rid = f'rIdImg{n}'
                        r2 = copy.deepcopy(r)
                        r2.set('Id', new_rid)
                        out_rels.append(r2)
                        existing.add(new_rid)
                        rid_map[rid] = new_rid
                if rid_map:
                    # Rewrite r:embed / r:link in the body to the new ids.
                    W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
                    R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
                    body_root = etree.fromstring(data['word/document.xml'])
                    changed = False
                    for el in body_root.iter():
                        for attr in ('{%s}embed' % R, '{%s}link' % R):
                            old = el.get(attr)
                            if old in rid_map:
                                el.set(attr, rid_map[old])
                                changed = True
                    if changed:
                        data['word/document.xml'] = etree.tostring(
                            body_root, xml_declaration=True, encoding='UTF-8',
                            standalone=True)
                # Drop UNREFERENCED image relationships (the template's
                # leftover sample image, e.g. rId18 → media/image1.jpeg) and
                # their media parts. Runs UNCONDITIONALLY (even when pandoc
                # added no figures): Word flags an image relationship with no
                # drawing referencing it as unreadable content.
                body_text = data['word/document.xml'].decode('utf-8')
                used = set(re.findall(r'r:(?:embed|link|id)="(rId\w+)"', body_text))
                dropped_targets = set()
                for r in list(out_rels):
                    rid = r.get('Id')
                    rtype = r.get('Type') or ''
                    if rid and 'image' in rtype and rid not in used:
                        tgt = r.get('Target') or ''
                        if tgt:
                            dropped_targets.add(tgt)
                        out_rels.remove(r)
                # Remove the media parts those rels pointed at (skip the
                # ones the body actually uses).
                used_targets = set()
                for r in out_rels:
                    rtype = r.get('Type') or ''
                    if 'image' in rtype and r.get('Target'):
                        used_targets.add(r.get('Target'))
                for tgt in dropped_targets:
                    part = 'word/' + tgt if not tgt.startswith('word/') else tgt
                    if part in data and tgt not in used_targets:
                        del data[part]
                data[rels_path] = etree.tostring(
                    out_rels, xml_declaration=True, encoding='UTF-8',
                    standalone=True)

            # Keep word/endnotes.xml (with the template's separator entries)
            # in the package. Word requires the part to be present even when
            # no endnotes are used; deleting it causes Word to re-create it
            # on open and flag the file as repaired. The template already
            # contains exactly the right minimal content (separator +
            # continuationSeparator only), so we leave it untouched.
            # settings.xml: remove w:endnotePr
            if 'word/settings.xml' in data:
                s_root = etree.fromstring(data['word/settings.xml'])
                W2 = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
                def tag2(n): return '{%s}%s' % (W2, n)
                changed = False
                for el in list(s_root.iter(tag2('endnotePr'))):
                    parent = el.getparent()
                    if parent is not None:
                        parent.remove(el)
                        changed = True
                if changed:
                    data['word/settings.xml'] = etree.tostring(
                        s_root, xml_declaration=True, encoding='UTF-8',
                        standalone=True)
            # document.xml: remove w:endnotePr from every sectPr
            if 'word/document.xml' in data:
                d_root = etree.fromstring(data['word/document.xml'])
                W2 = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
                def tag2(n): return '{%s}%s' % (W2, n)
                changed = False
                for el in list(d_root.iter(tag2('endnotePr'))):
                    parent = el.getparent()
                    if parent is not None:
                        parent.remove(el)
                        changed = True
                if changed:
                    data['word/document.xml'] = etree.tostring(
                        d_root, xml_declaration=True, encoding='UTF-8',
                        standalone=True)

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
    ap.add_argument('--shorttitle', default=None)
    ap.add_argument('--basename', default=None)
    ap.add_argument('--abstract', default=None)
    ap.add_argument('--note', default=None)
    args = ap.parse_args()
    merge(args.template, args.input, args.output, args.title, args.author,
          args.subtitle, args.date_val, args.toc, args.shorttitle, args.basename,
          args.abstract, args.note)
    print(f'Merged: {args.output}')

if __name__ == '__main__':
    main()
