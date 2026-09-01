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

import random
from lxml import etree


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
