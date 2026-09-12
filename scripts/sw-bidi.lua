-- sw-bidi.lua
--
-- ScholarWeave Lua filter: give block-level Arabic content an explicit
-- right-to-left direction (LaTeX language container / docx-odt dir=rtl).
--
-- babel's `onchar=ids fonts` (see the templates' "Arabic / non-Latin RTL
-- scripts" note) auto-detects Arabic at the CHARACTER level: it switches the
-- font and hyphenation for Arabic runs inside a paragraph, and inline Arabic
-- in a mostly-Latin paragraph is joined and ordered correctly with no markup.
-- But a paragraph's BASE direction is fixed when the paragraph starts (its
-- \pardirection is read then), before the first Arabic character is seen — so
-- a standalone Arabic paragraph or blockquote still comes out left-to-right,
-- and its last, partial line lands on the LEFT instead of the right. Fixing
-- that needs the language active BEFORE the paragraph starts:
-- `\begin{otherlanguage}{arabic}` switches both the language and the
-- paragraph direction for its body (confirmed: a direct Arabic \begin{quote}
-- stays LTR, the same quote wrapped in \begin{otherlanguage}{arabic} is RTL).
--
-- This filter wraps a BlockQuote / Div whose text is predominantly
-- Arabic-script, per output format:
--   * LaTeX: `\begin{otherlanguage}{arabic}` (sets language + paragraph dir);
--   * DOCX/ODT/HTML: a Div with dir=rtl, which pandoc's writers translate to
--     `w:bidi` + run `w:rtl` (docx) and `style:writing-mode="rl-tb"` +
--     right alignment (odt) — verified to propagate through a wrapping Div.
-- It is deliberately limited to block containers — a short Arabic word inside
-- an English sentence must NOT be wrapped, since babel already handles inline
-- runs correctly and adding a language switch would also add LTR/RTL boundary
-- effects mid-line.

local ARABIC_RANGES = {
  { 0x0600, 0x06FF },  -- Arabic
  { 0x0750, 0x077F },  -- Arabic Supplement
  { 0x0870, 0x08FF },  -- Arabic Extended-B + Extended-A
  { 0xFB50, 0xFDFF },  -- Arabic Presentation Forms-A
  { 0xFE70, 0xFEFF },  -- Arabic Presentation Forms-B
}

local function is_arabic_cp(cp)
  for _, r in ipairs(ARABIC_RANGES) do
    if cp >= r[1] and cp <= r[2] then return true end
  end
  return false
end

-- Rough "is this Arabic?" share of a string's significant characters. Latin
-- letters, digits, and other scripts count as "not Arabic"; whitespace,
-- punctuation, and symbols are ignored so a short Arabic line with a citation
-- or punctuation still reads as Arabic.
local function arabic_share(text)
  local ar, other = 0, 0
  for _, cp in utf8.codes(text) do
    if is_arabic_cp(cp) then
      ar = ar + 1
    elseif cp > 0x40 then
      other = other + 1
    end
  end
  local total = ar + other
  return total > 0 and (ar / total) or 0
end

local function wrap(el)
  -- Poetry callouts are handled by sw-poetry.lua (which runs before this);
  -- this guard covers the case where that filter is disabled.
  if pandoc.utils.stringify(el):match('^%[!') then return nil end
  if arabic_share(pandoc.utils.stringify(el)) <= 0.5 then return nil end

  if FORMAT == 'latex' then
    return {
      pandoc.RawBlock('latex', '\\begin{otherlanguage}{arabic}'),
      el,
      pandoc.RawBlock('latex', '\\end{otherlanguage}'),
    }
  end
  return pandoc.Div({ el }, pandoc.Attr('', {}, { dir = 'rtl' }))
end

return { { BlockQuote = wrap, Div = wrap } }
