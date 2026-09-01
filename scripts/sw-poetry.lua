-- sw-poetry.lua
--
-- Optional ScholarWeave Lua filter: converts Obsidian poetry callouts to
-- paragraph styles in the exported Word/ODT document.
--
-- Supported callout types:
--   [!poetry]               → "English poetry" paragraph style
--   [!english-poetry]       → "English poetry" paragraph style
--   [!arabic-poetry]        → "Arabic poetry" paragraph style
--   [!arabic-poetry-callout]→ "Arabic poetry" paragraph style
--
-- Vault poetry conventions:
--
--   English:
--     > [!poetry]
--     > - First hemistich  \t- Second hemistich
--     > - Next verse first  \t- Next verse second
--
--   Arabic (RTL; hemistichs are tab-separated on a single line):
--     > [!arabic-poetry]
--     > - First hemistich  \t- Second hemistich
--
-- Each "[!type]" marker line is the callout header (first block); body lines
-- are list items or plain paragraphs. The filter collects ALL body content
-- into one combined inline stream before calling parse_poetry, so multi-line
-- verse assembly works correctly regardless of how pandoc structures the AST.
--
-- Usage (ScholarWeave export pipeline or CLI):
--   --lua-filter=sw-poetry.lua
--
-- Place this file in the same directory as sw-export.lua. In ScholarWeave,
-- enable it via the "Lua filters" checkbox in Settings or the Export modal.

-- ── helpers ─────────────────────────────────────────────────────────────────

-- Wrap a paragraph in a Div carrying the given Word/ODT custom paragraph style.
local function styled_para(content, style_name)
  return pandoc.Div(
    { content },
    pandoc.Attr('', {}, { ['custom-style'] = style_name })
  )
end

-- ── poetry parser ────────────────────────────────────────────────────────────

-- Parse a combined inline stream into a list of verses, each a list of
-- hemistich inline-lists.
--
-- Input conventions (both arrive in the same stream after collect_all_inlines):
--
--   English (original format):
--     Str('-') Space <hemistich-1> RawInline("\t-") Space <hemistich-2>
--     SoftBreak  ← between list items (added by collect_all_inlines)
--     Str('-') Space <next-verse-1> RawInline("\t-") Space <next-verse-2>
--
--   English (rewritten / single-line format):
--     <hemistich-1> RawInline("<br>"|"\t-") <hemistich-2>
--     SoftBreak  ← between source lines / list items
--     <next-verse-1> RawInline("<br>"|"\t-") <next-verse-2>
--
--   Arabic: same as English rewritten (no leading '-').
--
-- Returns: pandoc.List of verses; each verse is a pandoc.List of hemistich
-- inline-lists (pandoc.List of Inline).
local function parse_poetry(inlines, is_arabic)
  local verses  = pandoc.List()
  local cur_verse = pandoc.List()
  local cur_hemi  = pandoc.List()

  local function flush_hemi()
    if #cur_hemi > 0 then
      cur_verse:insert(cur_hemi)
      cur_hemi = pandoc.List()
    end
  end
  local function flush_verse()
    flush_hemi()
    if #cur_verse > 0 then
      verses:insert(cur_verse)
      cur_verse = pandoc.List()
    end
  end

  local i = 1
  local n = #inlines

  -- Skip the "[!type]" marker if it somehow survived into the stream.
  if inlines[1] and inlines[1].t == 'Str'
      and inlines[1].text:match('^%[!') then
    i = 2
    if inlines[i] and inlines[i].t == 'SoftBreak' then i = i + 1 end
  end

  -- Detect format: "original" starts with a bare '-' Str; "rewritten" does not.
  local rewritten = not (
    inlines[i] and inlines[i].t == 'Str' and inlines[i].text == '-'
  )

  while i <= n do
    local inl = inlines[i]

    if inl.t == 'RawInline' and (
        (inl.format == 'tex'  and inl.text:match('\\t%-'))  or
        (inl.format == 'html' and inl.text:match('^<%s*/?%s*br%s*/?>$'))
    ) then
      -- "\t-" or "<br>" separates hemistichs within one verse.
      flush_hemi()
      i = i + 1
      -- Skip optional Space after the separator.
      if inlines[i] and inlines[i].t == 'Space' then i = i + 1 end

    elseif inl.t == 'SoftBreak' then
      -- SoftBreak inserted between list items by collect_all_inlines.
      -- In rewritten format each source line is one verse → start new verse.
      flush_hemi()
      i = i + 1
      if rewritten and #cur_verse > 0 then
        flush_verse()
      end

    elseif inl.t == 'Str' and inl.text == '-' and not is_arabic then
      -- Bare '-' starts a new verse in original English format.
      flush_verse()
      i = i + 1
      if inlines[i] and inlines[i].t == 'Space' then i = i + 1 end

    else
      cur_hemi:insert(inl)
      i = i + 1
    end
  end
  flush_verse()
  return verses
end

-- ── verse builder ────────────────────────────────────────────────────────────

-- Build one verse paragraph from a list of hemistich inline-lists.
-- Arabic: join hemistichs with a format-appropriate tab character.
--   DOCX: OpenXML <w:tab/>; ODT: ODF <text:tab/>; other: literal tab Str.
-- English: join with a LineBreak (hanging indent from the "English poetry"
-- Word style shows the second hemistich indented).
local function verse_from_hemistichs(hemistichs, is_arabic)
  local inlines = pandoc.List()
  for i, h in ipairs(hemistichs) do
    if i > 1 then
      if is_arabic then
        if FORMAT == 'docx' then
          inlines:insert(pandoc.RawInline('openxml', '<w:r><w:tab/></w:r>'))
        elseif FORMAT == 'odt' then
          inlines:insert(pandoc.RawInline('opendocument', '<text:tab/>'))
        else
          inlines:insert(pandoc.Str('\t'))
        end
      else
        inlines:insert(pandoc.LineBreak())
      end
    end
    for _, inl in ipairs(h) do inlines:insert(inl) end
  end
  return pandoc.Para(inlines)
end

-- ── inline collector ─────────────────────────────────────────────────────────

-- Walk an AST block list and combine ALL leaf Para/Plain inline content into
-- one flat inline stream, inserting a SoftBreak between each Para so that
-- parse_poetry can see verse/hemistich boundaries across AST boundaries.
--
-- This is the key fix over the old per-Para approach: parse_poetry receives
-- the full stream and can correctly assemble multi-hemistich verses even when
-- pandoc has split callout content into a BulletList with one item per line.
local function collect_all_inlines(blocks)
  local combined = pandoc.List()

  local function walk(bs)
    for _, b in ipairs(bs) do
      if b.t == 'Para' or b.t == 'Plain' then
        if #combined > 0 then
          combined:insert(pandoc.SoftBreak())
        end
        for _, inl in ipairs(b.content) do combined:insert(inl) end
      elseif b.t == 'BulletList' or b.t == 'OrderedList' then
        for _, item in ipairs(b.content) do walk(item) end
      elseif b.t == 'BlockQuote' or b.t == 'Div' then
        walk(b.content)
      end
      -- Header, HorizontalRule, CodeBlock, Table, etc. are skipped.
    end
  end

  walk(blocks)
  return combined
end

-- ── filter entry point ───────────────────────────────────────────────────────

-- Use BlockQuote (more specific than Block) so we only see actual blockquotes.
-- Returning a replacement removes the block from subsequent filter passes.
function BlockQuote(el)
  local content = el.content
  if #content == 0 then return nil end

  -- The callout marker is in the first block (Para or Header in older pandoc).
  local first = content[1]
  local marker
  if first.t == 'Para' or first.t == 'Header' then
    marker = pandoc.utils.stringify(first.content):match('^%[!([^%]]+)%]')
  end
  if not marker then return nil end
  marker = marker:lower()

  local is_arabic  = (marker == 'arabic-poetry' or marker == 'arabic-poetry-callout')
  local is_english = (marker == 'poetry' or marker == 'english-poetry')
  if not (is_arabic or is_english) then return nil end

  local style_name = is_arabic and 'Arabic poetry' or 'English poetry'

  -- Collect the body (everything after the marker block) into one stream.
  local rest = pandoc.List()
  for i = 2, #content do rest:insert(content[i]) end

  local combined = collect_all_inlines(rest)
  if #combined == 0 then return nil end

  local verses = parse_poetry(combined, is_arabic)
  if #verses == 0 then return nil end

  local out = pandoc.List()
  for _, v in ipairs(verses) do
    if #v > 0 then
      out:insert(styled_para(verse_from_hemistichs(v, is_arabic), style_name))
    end
  end

  return #out > 0 and out or nil
end

return { { BlockQuote = BlockQuote } }
