-- sw-export.lua
--
-- ScholarWeave export filter. Runs in the --lua-filter chain and applies
-- the vault's document-formatting conventions before the docx/odt writer.
--
-- Reads YAML frontmatter and:
--   1. Selects the reference docx from the "template" property (default
--      "document"), mapped to Export Templates/<template>.docx.
--   2. Maps YAML properties to docx core properties (Author, Keywords, …);
--      missing values fall back to the template's own core-property defaults;
--      the export pipeline's merge step applies the author from plugin settings
--      when no YAML author is present.
--
-- Poetry callouts ([!poetry], [!arabic-poetry], …) are handled by the
-- optional sw-poetry.lua filter, which runs after this one.
-- Footnotes ([^1] ...) and headings (# / ##) are handled natively by pandoc's
-- docx writer (Footnote Text, Heading 1/2 styles) — no lua needed.
--
-- Usage (ScholarWeave export pipeline or CLI):
--   --lua-filter=sw-export.lua
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
  -- category. When 'author' is absent, the merge step (lc_export_merge.py)
  -- fills it from the plugin's default-author setting, so we leave it unset
  -- here and let the merge handle the fallback.
  if not meta['keywords'] and meta['tags'] then meta['keywords'] = meta['tags'] end
  return meta
end

-- ── Generic callout handler ─────────────────────────────────────────────────

-- Callout types handled by the dedicated sw-poetry.lua filter.
-- Return nil here so they flow through to that filter unchanged.
local POETRY_CALLOUT_TYPES = {
  ['poetry'] = true, ['english-poetry'] = true,
  ['arabic-poetry'] = true, ['arabic-poetry-callout'] = true,
  ['poetry-callout'] = true,
}

function BlockQuote(el)
  if #el.content == 0 then return nil end
  local first = el.content[1]
  if first.t ~= 'Para' and first.t ~= 'Header' then return nil end
  local marker = pandoc.utils.stringify(first.content):match('^%[!([^%]]+)%]')
  if not marker then return nil end
  marker = marker:lower()

  -- Skip poetry callouts — sw-poetry.lua handles them.
  if POETRY_CALLOUT_TYPES[marker] then return nil end

  -- Strip the marker paragraph; keep the body blocks.
  local body = pandoc.List()
  for i = 2, #el.content do body:insert(el.content[i]) end
  if #body == 0 then return pandoc.List{} end

  -- Wrap body in a Div carrying the "Callout heading" custom Word/ODT style.
  return pandoc.Div(body, pandoc.Attr('', {}, {['custom-style'] = 'Callout heading'}))
end

-- ── Notes-section suppression ────────────────────────────────────────────────

-- Drop the DocumentCompiler "Notes" organizational section: a "# Notes" heading
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
  return nil
end

return { { Meta = Meta, Block = Block, Header = Header, BlockQuote = BlockQuote } }
