-- sw-wikilink-citations.lua
--
-- SUPERSEDED — kept for reference only.
--
-- This filter is no longer part of the active ScholarWeave export pipeline.
-- The current pipeline uses scripts/convert-citations.mjs (the plugin's own
-- TypeScript parser, compiled by esbuild) to convert citation wikilinks to
-- native pandoc citations BEFORE pandoc runs — which gives a single source of
-- truth between the Obsidian renderer and the exported document.
--
-- Original purpose: convert Obsidian citation wikilinks to pandoc Cite elements
-- so that Better BibTeX's sw-zotero.lua filter could turn them into native
-- Zotero citations on export. This was used when "Enhancing Export" (a
-- third-party Obsidian plugin) was driving single-note exports; that workflow
-- is now fully superseded by ScholarWeave's built-in export modal.
--
-- Must run BEFORE sw-zotero.lua in the --lua-filter chain (historical).
-- Links whose target does not start with '@' are left untouched.

local stringify = pandoc.utils.stringify

-- Convert a roman numeral string to arabic; unchanged when not roman.
local function roman_to_arabic(s)
  local map = { I = 1, V = 5, X = 10, L = 50, C = 100, D = 500, M = 1000 }
  local up = s:upper()
  if not up:match('^[IVXLCDM]+$') then return s end
  local total, prev = 0, 0
  for i = #up, 1, -1 do
    local v = map[up:sub(i, i)]
    if v < prev then total = total - v else total = total + v prev = v end
  end
  return tostring(total)
end

-- Expand an alias string: replace every '@'-token (a bare '@' or '@...',
-- matching the plugin parser's @[^\s,;]* proxy) with the link's own key;
-- detect and strip a trailing whitespace-separated '-' flag (author-in-text).
-- Also combine a multi-part locator "vol. X, p. Y" into the single Chicago
-- locator "X:Y" (roman volume → arabic), mirroring the plugin parser so the
-- exported docx carries ONE structured locator per citation (Zotero allows
-- only one).
-- Returns expanded text, author-in-text flag.
local function expand_alias(text, key)
  local t = text:gsub('%s+$', '')
  local intext = t:match('%s%-$') ~= nil
  if intext then
    t = t:gsub('%s%-$', '')
  end
  t = t:gsub('@[^%s,;]*', '@' .. key)
  -- Combine "vol. X, p. Y" / "vol. X, pp. Y-Z" into the single Chicago
  -- locator "X:Y" / "X:Y-Z" (roman volume → arabic). Two passes (roman,
  -- then arabic) because Lua patterns have no alternation with captures.
  t = t:gsub('vol%.%s*([IVXLCDM]+)%s*,%s*p+%.?%s*(%S+)',
    function(vol, page) return roman_to_arabic(vol) .. ':' .. page end)
  t = t:gsub('vol%.%s*([0-9]+)%s*,%s*p+%.?%s*(%S+)',
    function(vol, page) return vol .. ':' .. page end)
  return t, intext
end

-- Parse a bracketed citation expression ("[see also @key, 3]") with pandoc's
-- own markdown reader, which yields proper Citation objects.
local function parse_cite(bracket_markdown)
  local doc = pandoc.read(bracket_markdown, 'markdown')
  for _, block in ipairs(doc.blocks) do
    if block.t == 'Para' then
      for _, inl in ipairs(block.content) do
        if inl.t == 'Cite' then
          return inl
        end
      end
    end
  end
  return nil
end

local function link_key(target)
  return target:match('^@(%S+)$')
end

function Link(link)
  local key = link_key(link.target)
  if not key then
    return nil
  end

  local text = stringify(link.content)
  local expanded, intext = expand_alias(text, key)

  if text:find('@') then
    -- The alias carries citation material: parse it as a citation expression.
    local cite = parse_cite('[' .. expanded .. ']')
    if not cite then
      return nil
    end
    if intext then
      for _, c in ipairs(cite.citations) do
        c.mode = 'AuthorInText'
      end
    end
    cite.content = link.content
    return cite
  end

  -- Plain label with no citation material: keep the label as literal text and
  -- append the citation of the link's own key: "here (Smith 1992)".
  local cite = parse_cite('[@' .. key .. ']')
  if not cite then
    return nil
  end
  local out = pandoc.List()
  for _, inl in ipairs(link.content) do
    out:insert(inl)
  end
  out:insert(pandoc.Space())
  out:insert(cite)
  return out
end

-- Multi-work container: ⟦[[@a]]; [[@b]]⟧ or [ [[@a]]; [[@b]] ] -> one Cite
-- with several citations. Runs after the Link handler (pandoc walks
-- bottom-up), so the contained links are already Cites. Link-derived Cites
-- are identified by their content not starting with '[' (bracket citations'
-- content always starts with '[').
function Para(para)
  local content = para.content
  local out = pandoc.List()
  local i = 1
  local changed = false

  local function link_derived(el)
    return el.t == 'Cite' and not stringify(el.content):match('^%[')
  end

  -- Try to match a container starting at content[i] (which is '[' or '⟦').
  -- Returns the index AFTER the closing bracket, the list of cites, and any
  -- trailing text merged onto the closer (e.g. "]," or "]."), or nil if
  -- this is not a container (so '[' can be kept as literal text).
  -- Inside a container any Cite is accepted (link-derived wikilinks AND
  -- plain "[@bracket]" citations); the surrounding delimiters + ";" pattern
  -- is what distinguishes a container from ordinary bracketed text.
  local function try_container(start)
    local j = start + 1
    local cites = {}

    local function skip_spaces()
      while j <= #content and content[j].t == 'Space' do j = j + 1 end
    end

    skip_spaces()
    while j <= #content do
      local el = content[j]
      if el.t == 'Cite' then
        table.insert(cites, el)
        j = j + 1
        skip_spaces()
        if j <= #content and content[j].t == 'Str' and content[j].text == ';' then
          j = j + 1
          skip_spaces()
          if not (j <= #content and content[j].t == 'Cite') then
            return nil
          end
        else
          -- Closing bracket: '⟧' (U+27E7) for a '⟦' opener, ']' for '['.
          -- Pandoc may merge it with following punctuation ("],", "]."), so
          -- match by prefix and return the trailing text separately.
          if j <= #content and content[j].t == 'Str' then
            local opener = content[start].text
            local closer = content[j].text
            local is_close = (opener == '\226\159\166' and closer:sub(1, 1) == '\226\159\167')
                or (opener == '[' and closer:sub(1, 1) == ']')
            if is_close and #cites >= 2 then
              local rest = closer:sub(2)
              return j + 1, cites, rest
            end
          end
          return nil
        end
      else
        return nil
      end
    end
    return nil
  end

  while i <= #content do
    local inl = content[i]
    local is_opener = (inl.t == 'Str' and
      (inl.text == '\226\159\166' or inl.text == '['))
    if not is_opener then
      out:insert(inl)
      i = i + 1
    else
      local next_i, cites, rest = try_container(i)
      if not cites then
        out:insert(inl)
        i = i + 1
      else
        -- Merge all citations into a single Cite element.
        local all = pandoc.List()
        local body = pandoc.List()
        for n, c in ipairs(cites) do
          if n > 1 then
            body:insert(pandoc.Str('; '))
          end
          for _, cit in ipairs(c.citations) do
            all:insert(cit)
          end
          for _, x in ipairs(c.content) do
            body:insert(x)
          end
        end
        out:insert(pandoc.Cite(body, all))
        if rest and rest ~= '' then
          out:insert(pandoc.Str(rest))
        end
        i = next_i
        changed = true
      end
    end
  end

  if changed then
    return pandoc.Para(out)
  end
  return nil
end

return { { Link = Link, Para = Para } }
