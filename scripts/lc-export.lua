-- lc-export.lua
--
-- Linked Citations export filter. Runs in the --lua-filter chain and applies
-- the vault's document-formatting conventions before the docx/odt writer.
--
-- Reads YAML frontmatter and:
--   1. Selects the reference docx from the "template" property (default
--      "document"), mapped to Export Templates/<template>.docx.
--   2. Maps YAML properties to docx core properties (Author, Keywords, ...);
--      empty/missing properties fall back to the template's own defaults.
--   3. Applies markdown conventions that pandoc can't express directly:
--      - >[!arabic-poetry] callouts  -> "Arabic poetry" style paragraphs
--        (pandoc's -style-name class convention maps a `-arabic-poetry`
--        paragraph class to the Word style named "Arabic poetry")
--
-- Footnotes ([^1] ...) and headings (# / ##) are handled natively by pandoc's
-- docx writer (Footnote Text, Heading 1/2 styles) — no lua needed.
--
-- Usage (Enhancing Export customArguments or CLI):
--   --lua-filter=lc-export.lua
--
-- Set VAULT_ROOT if your vault lives elsewhere.

local VAULT_ROOT = '/Users/josephhill/Documents/Obsidian Vault'
local EXPORT_TEMPLATES_DIR = VAULT_ROOT .. '/Export Templates'

-- ── template selection + core properties ────────────────────────────────────

function Meta(meta)
  -- Resolve "template" frontmatter → reference docx path.
  local tpl = pandoc.utils.stringify(meta['template'] or '')
  if tpl == '' or tpl == 'null' then tpl = 'document' end
  tpl = tpl:gsub('%.docx$', '')  -- strip extension if given

  local tpl_path = EXPORT_TEMPLATES_DIR .. '/' .. tpl .. '.docx'
  local f = io.open(tpl_path, 'r')
  if not f then
    tpl_path = EXPORT_TEMPLATES_DIR .. '/document.docx'  -- default fallback
  else
    f:close()
  end
  meta['reference-doc'] = tpl_path

  -- Map YAML properties to docx core properties. Pandoc reads these from
  -- metadata: author (list ok), keywords (list ok), subject, description,
  -- category. Missing → template's own defaults (e.g. Author: Joseph Hill).
  if not meta['author'] then meta['author'] = 'Joseph Hill' end
  if not meta['keywords'] and meta['tags'] then meta['keywords'] = meta['tags'] end
  return meta
end

-- ── conventions ─────────────────────────────────────────────────────────────

-- Wrap a paragraph in a Div with the given Word custom-style.
local function styled_para(content, style_name)
  return pandoc.Div({ content }, pandoc.Attr('', {}, { ['custom-style'] = style_name }))
end

-- Parse a poetry callout's inline stream into a list of verses, each a list
-- of hemistichs. The vault convention:
--   English:   - h1  [SoftBreak]  \t- h2   → one verse, two hemistichs
--              (a new "-" starts the next verse)
--   Arabic:    >- (empty) then  \t- h1 [SoftBreak] \t- h2 → one verse
-- The "[!type]" marker is the first Str and is dropped.
-- `\t-` arrives as RawInline(tex "\\t-"); a verse-starting "-" is Str "-" +
-- Space; SoftBreak separates hemistichs within a verse.
local function parse_poetry(inlines, is_arabic)
  local verses = pandoc.List()  -- each verse = pandoc.List of hemistich lists
  local cur_verse = pandoc.List()
  local cur_hemi = pandoc.List()

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
  -- Skip the "[!type]" marker (first Str).
  if inlines[1] and inlines[1].t == 'Str' and inlines[1].text:match('^%[!') then
    i = 2
    -- skip a SoftBreak after the marker
    if inlines[i] and inlines[i].t == 'SoftBreak' then i = i + 1 end
  end

  -- Distinguish the two input conventions:
  --   original  : each line starts with Str('-') (verse) or RawInline(tex
  --               "\t-")/<br> (hemistich) — SoftBreak separates hemistichs
  --               within a verse.
  --   rewritten : verses are one line each ('h1<br>h2'), so a SoftBreak
  --               starts a NEW verse. Detectable because the first content
  --               token is NOT a bare '-' Str.
  local rewritten = not (inlines[i] and inlines[i].t == 'Str'
                         and inlines[i].text == '-')

  while i <= n do
    local inl = inlines[i]
    if inl.t == 'RawInline' and (
        (inl.format == 'tex' and inl.text:match('\\t%-')) or
        (inl.format == 'html' and inl.text:match('^<%s*/?%s*br%s*/?>$'))
    ) then
      -- "\t-" or "<br>" ends hemistich1; a new hemistich begins after.
      flush_hemi()
      i = i + 1
      if inlines[i] and inlines[i].t == 'Space' then i = i + 1 end
    elseif inl.t == 'SoftBreak' then
      -- SoftBreak: end of hemistich. In the rewritten format each source
      -- line is one verse, so a SoftBreak starts a new verse.
      flush_hemi()
      i = i + 1
      if rewritten and #cur_verse > 0 then
        flush_verse()
      end
    elseif inl.t == 'Str' and inl.text == '-' and not is_arabic then
      -- A bare "-" starts a NEW verse (English poetry, original format).
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

-- Build one verse paragraph from hemistichs. Arabic: join with a tab into a
-- single line. English: join with a line break (hanging indent shows the
-- second line indented, per the template's "English poetry" style).
local function verse_from_hemistichs(hemistichs, is_arabic)
  local inlines = pandoc.List()
  for i, h in ipairs(hemistichs) do
    if i > 1 then
      if is_arabic then
        inlines:insert(pandoc.RawInline('openxml', '<w:tab/>'))
      else
        inlines:insert(pandoc.LineBreak())
      end
    end
    for _, inl in ipairs(h) do
      inlines:insert(inl)
    end
  end
  return pandoc.Para(inlines)
end

-- Handle Obsidian callouts (>[!type] ...). Pandoc parses these as BlockQuote
-- whose first block is the "[!type]" marker (a Para or Header).
local function handle_blockquote(block)
  local content = block.content
  if #content == 0 then return nil end
  local marker
  local first = content[1]
  if first.t == 'Para' then
    marker = pandoc.utils.stringify(first.content):match('^%[!([^%]]+)%]')
  elseif first.t == 'Header' then
    marker = pandoc.utils.stringify(first.content):match('^%[!([^%]]+)%]')
  end
  if not marker then return nil end
  marker = marker:lower()

  if marker == 'poetry' or marker == 'english-poetry' then
    -- English poetry: each verse = two hemistichs joined with <br/>.
    local out = pandoc.List()
    for i = 1, #content do
      local b = content[i]
      if b.t == 'Para' then
        local verses = parse_poetry(b.content, false)
        for _, v in ipairs(verses) do
          if #v > 0 then
            out:insert(styled_para(verse_from_hemistichs(v, false), 'English poetry'))
          end
        end
      end
    end
    if #out > 0 then return out end
  end

  if marker == 'arabic-poetry' or marker == 'arabic-poetry-callout' then
    -- Arabic poetry: the empty ">-" line is ignored; each verse = the two
    -- "\t- hemistich" lines joined with a tab.
    local out = pandoc.List()
    for i = 1, #content do
      local b = content[i]
      if b.t == 'Para' then
        local verses = parse_poetry(b.content, true)
        for _, v in ipairs(verses) do
          if #v > 0 then
            out:insert(styled_para(verse_from_hemistichs(v, true), 'Arabic poetry'))
          end
        end
      end
    end
    if #out > 0 then return out end
  end

  return nil
end

-- Drop the BookCompiler "Notes" organizational section: a "# Notes" heading
-- and any following "## <chapter>" subheadings whose content pandoc turned
-- into real Word footnotes. Without this they render as empty headings.
local in_notes = false
function Header(el)
  local text = pandoc.utils.stringify(el.content):lower()
  if el.level == 1 and text == 'notes' then
    in_notes = true
    return pandoc.List{}  -- drop the Notes heading itself
  end
  if in_notes then
    -- Drop chapter subheadings inside Notes (they have no body content).
    return pandoc.List{}
  end
  return nil
end

function Block(block)
  -- A non-header block (paragraph, list, etc.) after Notes marks the end of
  -- the organizational section — reset the flag. (Footnotes are converted by
  -- the writer, so the Notes body is headings-only in practice.)
  if in_notes and block.t ~= 'Header' then
    in_notes = false
  end
  if block.t == 'BlockQuote' then
    return handle_blockquote(block)
  end
  return nil
end

return { { Meta = Meta, Block = Block, Header = Header } }
