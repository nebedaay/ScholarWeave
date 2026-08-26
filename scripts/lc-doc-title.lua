-- doc-title.lua
--
-- Document title for docx/odt exports: use the note's YAML frontmatter
-- "title" property when present; otherwise fall back to the note's file name,
-- which Enhancing Export passes in as --metadata source-note="${currentFileName}".
--
-- Example command line:
--   --metadata source-note="My Note" --lua-filter=doc-title.lua

function Meta(meta)
  local title = meta.title
  if not title or pandoc.utils.stringify(title) == '' then
    if meta['source-note'] then
      meta.title = meta['source-note']
    end
  end
  return meta
end

return { { Meta = Meta } }
