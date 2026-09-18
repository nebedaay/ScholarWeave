#!/usr/bin/env bash
#
# ScholarWeft setup helper — macOS.
#
# Asks before each step (y / n / esc to quit), reports progress, reuses what
# you already have, and ends with a summary of what succeeded / failed / was
# skipped. Nothing is changed without a yes; safe to re-run.
#
# Usage:  bash install-mac.sh
#
set -o pipefail

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
step() { printf '  \033[36m…\033[0m %s\n' "$*"; }
pass() { printf '  \033[32m✓\033[0m %s\n' "$*"; DONE+=("$*"); }
fail() { printf '  \033[31m✗\033[0m %s%s\n' "$1" "${2:+ — $2}"; FAILED+=("$1${2:+ — $2}"); }
skip() { SKIPPED+=("$*"); }
have() { command -v "$1" >/dev/null 2>&1; }

# Is a font family installed — whether by brew cask OR dragged into a Fonts
# folder (Font Book)? Uses fc-list when available, else scans the font folders.
# So we don't reinstall fonts the user already has (a cask installs one file
# per weight; a variable font is a single file).
font_present() { # <family with spaces>  <filename substring>
  if have fc-list && fc-list 2>/dev/null | grep -qiF "$1"; then return 0; fi
  local d
  for d in "$HOME/Library/Fonts" /Library/Fonts; do
    [ -d "$d" ] && find "$d" -maxdepth 1 -iname "*$2*" 2>/dev/null | grep -q . && return 0
  done
  return 1
}
FONT_SPECS=(
  "font-noto-serif|Noto Serif|NotoSerif"
  "font-noto-sans|Noto Sans|NotoSans"
  "font-noto-emoji|Noto Emoji|NotoEmoji"
  "font-scheherazade-new|Scheherazade|Scheherazade"
)

# Bump when the script changes, and print it at start-up so it's obvious which
# copy is running (a stale download has caused confusion).
SCRIPT_REV="2026-09-18l"

DONE=(); FAILED=(); SKIPPED=()

ask() { # <question> [label-for-summary]  → 0 yes, 1 no (records a skip)
  local a
  while :; do
    printf '%s (y/n/esc) ' "$1"
    IFS= read -r a || exit 0
    case "${a:-}" in
      [yY]*) return 0 ;;
      [nN]*) [ -n "${2:-}" ] && skip "$2"; return 1 ;;
      [qQ]|esc|ESC|$'\e') echo '  Cancelled — nothing more will be changed.'; exit 0 ;;
      *) printf '  Please answer y or n (or esc/q to quit).\n' ;;
    esac
  done
}

# ── JSON settings writers (for other plugins' data.json) ─────────────────────
# A plugin needn't have been enabled for us to create its data.json: plugins
# merge `DEFAULT_SETTINGS` with whatever the file holds, so a partial file is
# fine. We only edit with Obsidian CLOSED so nothing rewrites it under us, and
# we back up first. Uses python3 when available, else writes only if absent.
_pyjson() { command -v python3 2>/dev/null || printf '%s' "${PY:-}"; }
json_append_list() { # <file> <key> <value>  → dedupe-add to a JSON array
  local f="$1" k="$2" v="$3" py; py="$(_pyjson)"
  if [ -n "$py" ]; then
    "$py" - "$f" "$k" "$v" <<'PYEOF'
import json, sys
path, key, val = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    d = json.load(open(path))
    if not isinstance(d, dict): d = {}
except FileNotFoundError:
    d = {}
except Exception:
    sys.exit(3)
lst = d.get(key)
if not isinstance(lst, list): lst = []
if val not in lst: lst.append(val)
d[key] = lst
json.dump(d, open(path, 'w'), indent=2)
PYEOF
    return $?
  fi
  if [ ! -s "$f" ]; then printf '{\n  "%s": ["%s"]\n}\n' "$k" "$v" > "$f"; return 0; fi
  return 2
}
json_set_key() { # <file> <key> <json-literal>
  local f="$1" k="$2" v="$3" py; py="$(_pyjson)"
  if [ -n "$py" ]; then
    "$py" - "$f" "$k" "$v" <<'PYEOF'
import json, sys
path, key, val = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    d = json.load(open(path))
    if not isinstance(d, dict): d = {}
except FileNotFoundError:
    d = {}
except Exception:
    sys.exit(3)
d[key] = json.loads(val)
json.dump(d, open(path, 'w'), indent=2)
PYEOF
    return $?
  fi
  if [ ! -s "$f" ]; then printf '{\n  "%s": %s\n}\n' "$k" "$v" > "$f"; return 0; fi
  return 2
}


gh_asset_url() {
  curl -fsSL "https://api.github.com/repos/$1/releases/latest" 2>/dev/null \
    | grep -o "\"browser_download_url\": *\"[^\"]*$2\"" | head -1 \
    | sed 's/.*"\(https[^"]*\)"/\1/'
}
download() { step "Downloading $3…"; curl -fsSL "$1" -o "$2" || { fail "Download $3" "download failed"; return 1; }; }

# ── Obsidian vault discovery ─────────────────────────────────────────────────
find_vaults() {
  local list=() v x keep
  while IFS= read -r v; do
    [ -n "$v" ] || continue
    keep=1
    for x in "${list[@]}"; do case "$v/" in "$x"/*) keep=0; break ;; esac; done
    [ "$keep" = 1 ] && list+=("$v")
  done < <(
    # Depth 4 is enough for normal nests (e.g. ~/Documents/KIN/KIN), and we
    # PRUNE the heavy trees (macOS Library, Zotero storage, node_modules, …)
    # so this is quick instead of walking the whole home folder.
    find "$HOME" -maxdepth 4 \
      \( -name Library -o -name .Trash -o -name node_modules -o -name .git \
         -o -name .cache -o -name .local -o -name .npm -o -name Applications \
         -o -name Zotero -o -name storage -o -name snap -o -name .var \
         -o -name Dropbox -o -name .dropbox -o -name venv -o -name .venv \) -prune -o \
      -type d -name '.obsidian' -print 2>/dev/null \
    | sed 's:/.obsidian/*$::' \
    | grep -viE '(\.bk| copy|\.20[0-9]{2}-[0-9]{2}-[0-9]{2})(/|$)' \
    | sort -u
  )
  for v in "${list[@]}"; do printf '%s\n' "$v"; done
}
VAULT=""
VAULTS=()
locate_vaults() {
  local v n i
  step "Searching for Obsidian vaults (a few seconds)…"
  while IFS= read -r v; do [ -n "$v" ] && VAULTS+=("$v"); done < <(find_vaults)
  case "${#VAULTS[@]}" in
    0) echo "  No Obsidian vault found in your home folder — I'll ask for the path only if you choose to install a plugin." ;;
    1) VAULT="${VAULTS[0]}"; pass "Found vault: $VAULT" ;;
    *) echo "  Found ${#VAULTS[@]} Obsidian vaults:"
       i=1; for v in "${VAULTS[@]}"; do printf '    %d) %s\n' "$i" "$v"; i=$((i+1)); done
       IFS= read -r -p "  Which one should I use? (1-${#VAULTS[@]}, or type a path) " n
       if [[ "$n" =~ ^[0-9]+$ ]] && [ "$n" -ge 1 ] && [ "$n" -le "${#VAULTS[@]}" ]; then VAULT="${VAULTS[$((n-1))]}"
       else VAULT="${n/#\~/$HOME}"; VAULT="${VAULT%/}"; fi ;;
  esac
  if [ -n "$VAULT" ]; then
    if [ -d "$VAULT" ]; then printf '  Plugins will be installed into: %s\n' "$VAULT"
    else warn "Not a folder: $VAULT — I'll ask again if you choose to install a plugin."; VAULT=""; fi
  fi
}
pick_vault() { # used when a plugin install is chosen but no vault was located up front
  [ -n "$VAULT" ] && [ -d "$VAULT" ] && return 0
  IFS= read -r -p "  Path to your Obsidian vault: " VAULT
  VAULT="${VAULT/#\~/$HOME}"; VAULT="${VAULT%/}"
  if [ ! -d "$VAULT" ]; then fail "Choose vault" "not a folder: ${VAULT:-<empty>}"; VAULT=""; return 1; fi
  return 0
}

# ── Obsidian plugin install ──────────────────────────────────────────────────
enable_plugin() {
  local f="$1/.obsidian/community-plugins.json" id="$2" body
  mkdir -p "$1/.obsidian/plugins/$id"
  if [ ! -s "$f" ]; then printf '[\n  "%s"\n]\n' "$id" > "$f"; return; fi
  grep -q "\"$id\"" "$f" && return
  body="$(tr -d '\n' < "$f")"; body="${body%]}"; body="${body%,}"
  if [ "$body" = "[" ] || [ -z "$body" ]; then printf '[\n  "%s"\n]\n' "$id" > "$f"
  else printf '%s,\n  "%s"\n]\n' "$body" "$id" > "$f"; fi
}
install_obsidian_plugin() {
  local repo="$1" id="$2" vault="$3"
  # NOTE: compute `dir` on its own line. In one `local a=… b=$a/…` statement
  # bash 3.2 (macOS) expands `$a` BEFORE assigning it, so b would be built from
  # an empty value — that bug made the install path `/.obsidian/…`.
  local dir="$vault/.obsidian/plugins/$id" a u json tag latest inst
  if [ -z "$vault" ] || [ ! -d "$vault" ]; then
    fail "Install $id plugin" "no valid vault folder was chosen"; return 1
  fi
  mkdir -p "$dir"
  # GitHub's /releases/latest is 404 for repos whose releases are ALL marked
  # pre-release (ScholarWeft's are), so fall back to the releases LIST, which
  # includes pre-releases and is newest-first.
  json="$(curl -fsSL -H 'User-Agent: ScholarWeft' "https://api.github.com/repos/$repo/releases/latest" 2>/dev/null || true)"
  if ! printf '%s' "$json" | grep -q '"browser_download_url"'; then
    json="$(curl -fsSL -H 'User-Agent: ScholarWeft' "https://api.github.com/repos/$repo/releases?per_page=1" 2>/dev/null || true)"
  fi
  if ! printf '%s' "$json" | grep -q '"browser_download_url"'; then
    fail "Install $id plugin" "could not read release info (network or GitHub rate limit)"; return 1
  fi
  # Skip if the installed version is already the latest (no needless overwrite).
  tag="$(printf '%s' "$json" | grep -o '"tag_name": *"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/')"
  latest="${tag#v}"
  if [ -f "$dir/manifest.json" ]; then
    inst="$(grep -o '"version"[[:space:]]*:[[:space:]]*"[^"]*"' "$dir/manifest.json" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')"
    if [ -n "$inst" ] && [ -n "$latest" ] && [ "$(printf '%s\n%s\n' "$latest" "$inst" | sort -V | tail -1)" = "$inst" ]; then
      pass "$id already installed (v$inst, latest)"; return 0
    fi
    [ -n "$inst" ] && [ -n "$latest" ] && step "Updating $id v$inst → v$latest"
  fi
  mkdir -p "$dir"
  for a in main.js manifest.json styles.css; do
    u="$(printf '%s' "$json" | grep -o "\"browser_download_url\": *\"[^\"]*/$a\"" | head -1 | sed 's/.*"\(https[^"]*\)"/\1/')"
    if [ -n "$u" ]; then download "$u" "$dir/$a" "$a" || return 1
    else fail "Install $id plugin" "could not find $a in $repo releases"; return 1; fi
  done
  enable_plugin "$vault" "$id"
  pass "Installed the $id Obsidian plugin${latest:+ (v$latest)}"
}

# Copy ScholarWeft's ZotLit templates into the vault. We only copy FILES here;
# pointing ZotLit's "Template folder" at them is done in-plugin (which writes
# ZotLit's settings safely) — the script doesn't touch another plugin's data.
install_zotlit_templates() {
  local vault="$1"
  local dir="$vault/sw-zotlit-templates" name u
  if [ -z "$vault" ] || [ ! -d "$vault" ]; then
    fail "Copy ZotLit templates" "no valid vault folder was chosen"; return 1
  fi
  mkdir -p "$dir"
  for name in zotlit-annotation.eta.md zotlit-content.eta.md zotlit-filename.liquid.md zotlit-note.eta.md; do
    u="https://raw.githubusercontent.com/nebedaay/ScholarWeft/main/zotlit-templates/$name"
    download "$u" "$dir/$name" "$name" || return 1
  done
  pass "Copied ScholarWeft's ZotLit templates to sw-zotlit-templates/"
}

# Register repos in BRAT's beta list so they update automatically. BRAT needn't
# have run yet: its data.json is a partial file it merges with its defaults.
brat_register() {
  local vault="$1"
  local data="$vault/.obsidian/plugins/obsidian42-brat/data.json" ok=1 r
  [ -f "$vault/.obsidian/plugins/obsidian42-brat/manifest.json" ] || { fail "Register with BRAT" "BRAT isn't installed"; return 1; }
  [ -s "$data" ] && cp "$data" "$data.scholarweft.bak"
  for r in "nebedaay/ScholarWeft" "PKM-er/obsidian-zotlit"; do
    json_append_list "$data" "pluginList" "$r" || ok=0
  done
  if [ "$ok" = 1 ]; then pass "Registered ScholarWeft and ZotLit with BRAT for automatic updates"
  else fail "Register with BRAT" "couldn't write BRAT's settings (a backup was kept) — add the repos in BRAT's settings"; fi
}

# Point ZotLit's "Template folder" at our folder. ZotLit needn't have run yet.
set_zotlit_folder() {
  local vault="$1"
  local data="$vault/.obsidian/plugins/zotlit/data.json"
  [ -f "$vault/.obsidian/plugins/zotlit/manifest.json" ] || return 0
  [ -s "$data" ] && cp "$data" "$data.scholarweft.bak"
  if json_set_key "$data" "template.folder" '"sw-zotlit-templates"'
  then pass "Set ZotLit's Template folder to sw-zotlit-templates/"
  else fail "Set ZotLit template folder" "couldn't write ZotLit's settings — set its Template folder to sw-zotlit-templates/ manually"; fi
}

# Plugins that register the SAME sidebar view type ("ReferenceListView") as
# ScholarWeft: the ancestral "Pandoc Reference List" (which this fork derives
# from) and the fork's earlier names. With any of them enabled, Obsidian has two
# registrations of the same view type. Disable them (non-destructive) so they
# won't load — the user can remove the folders later if they wish.
disable_conflicting_plugins() {
  local vault="$1"
  local cfg="$vault/.obsidian/community-plugins.json"
  [ -f "$cfg" ] || return 0
  local id
  for id in obsidian-pandoc-reference-list pandoc-reference-list scholar-weave linked-citations; do
    if grep -q "\"$id\"" "$cfg"; then
      if remove_json_list_item "$cfg" "$id"; then
        pass "Disabled '$id' (it registers the same sidebar view as ScholarWeft)"
      else
        fail "Disable $id" "edit $cfg and remove \"$id\""
      fi
    fi
  done
  return 0
}

remove_json_list_item() { # <json-array-file> <value>
  local f="$1" v="$2" py; py="$(_pyjson)"
  [ -f "$f" ] || return 1
  if [ -n "$py" ]; then
    "$py" - "$f" "$v" <<'PYEOF'
import json, sys
path, val = sys.argv[1], sys.argv[2]
try:
    d = json.load(open(path))
except Exception:
    sys.exit(3)
if isinstance(d, list):
    json.dump([x for x in d if x != val], open(path, 'w'), indent=2)
PYEOF
    return $?
  fi
  return 2
}

# ── Zotero add-ons / prefs ───────────────────────────────────────────────────
ZPROFILE=""
for p in "$HOME/Library/Application Support/Zotero/Profiles"/*/prefs.js; do
  [ -f "$p" ] && { ZPROFILE="$(dirname "$p")"; break; }
done
zotero_running() { pgrep -x zotero >/dev/null 2>&1; }
install_zotero_addon() {
  local repo="$1" id="$2" url=""
  if [ "$repo" = "zotlit" ]; then
    step "Looking up the latest ZotLit Zotero add-on…"
    url="$(curl -fsSL 'https://api.github.com/repos/aidenlx/zotlit/releases?per_page=100' 2>/dev/null \
           | grep -o 'https://[^"]*zotlit-zotero-[0-9.]*\.xpi' | sort -uV | tail -1)"
  else
    url="$(gh_asset_url "$repo" ".xpi")"
  fi
  [ -n "$url" ] || { fail "Install $id" "could not resolve the download URL"; return 1; }
  mkdir -p "$ZPROFILE/extensions"
  download "$url" "$ZPROFILE/extensions/$id.xpi" "$id.xpi"
}
set_pref() {
  local f="$1" k="$2" v="$3"
  if grep -qF "user_pref(\"$k\"" "$f"; then sed -i '' "s|^user_pref(\"$k\".*|user_pref(\"$k\", $v);|" "$f"
  else printf 'user_pref("%s", %s);\n' "$k" "$v" >> "$f"; fi
}

# ── Python / Pandoc ──────────────────────────────────────────────────────────
PY=""
for c in python3 "$HOME/miniconda3/bin/python3" "$HOME/anaconda3/bin/python3" \
         /opt/miniconda3/bin/python3 /opt/anaconda3/bin/python3 \
         /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3 \
         "$HOME/.pyenv/shims/python3"; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import lxml, docx, requests' >/dev/null 2>&1; then PY="$(command -v "$c")"; break; fi
done
ensure_python() {
  [ -n "$PY" ] && { pass "Python libraries already present ($PY)"; return 0; }
  step "Installing Python packages (lxml, python-docx, requests)…"
  local c
  for c in "$HOME/miniconda3/bin/python3" "$HOME/anaconda3/bin/python3" \
           /opt/miniconda3/bin/python3 /opt/anaconda3/bin/python3 \
           /usr/local/bin/python3 "$(command -v python3 2>/dev/null || true)"; do
    [ -n "$c" ] && [ -x "$c" ] || continue
    if "$c" -m pip install --quiet lxml python-docx requests >/dev/null 2>&1 \
       && "$c" -c 'import lxml, docx, requests' >/dev/null 2>&1; then PY="$c"; pass "Python libraries installed into $c"; return 0; fi
  done
  have python3 || { step "Installing Python…"; brew install python || { fail "Install Python" "brew install failed"; return 1; }; }
  PY="$HOME/ScholarWeft/venv/bin/python3"
  step "Creating a private Python environment…"
  mkdir -p "$HOME/ScholarWeft"
  python3 -m venv "$HOME/ScholarWeft/venv" 2>/dev/null || "$(brew --prefix)"/bin/python3 -m venv "$HOME/ScholarWeft/venv" \
    || { fail "Create Python env" "venv creation failed"; return 1; }
  "$HOME/ScholarWeft/venv/bin/pip" install --quiet --upgrade pip >/dev/null 2>&1 || true
  "$HOME/ScholarWeft/venv/bin/pip" install --quiet lxml python-docx requests \
    && pass "Created a private Python environment ($PY)" || fail "Python libraries" "pip install failed"
}

# ═════════════════════════════════════════════════════════════════════════════
say "ScholarWeft setup (script $SCRIPT_REV)"
echo "  Running: $0"
echo "  I'll ask before each step — y to install/configure, n to skip, esc to quit."
echo "  Safe to re-run; nothing is changed without a yes."

OBSIDIAN_APP=0; [ -d /Applications/Obsidian.app ] && OBSIDIAN_APP=1
ZOTERO_APP=0;   [ -d /Applications/Zotero.app ] && ZOTERO_APP=1
if [ "$OBSIDIAN_APP" = 0 ] || [ "$ZOTERO_APP" = 0 ]; then
  if ask "Install the Obsidian and Zotero apps with Homebrew?" "Install apps"; then
    if have brew; then
      [ "$OBSIDIAN_APP" = 1 ] || { step "Installing Obsidian…"; brew install --cask obsidian && pass "Installed Obsidian" || fail "Install Obsidian" "brew install failed"; }
      [ "$ZOTERO_APP" = 1 ]   || { step "Installing Zotero…";   brew install --cask zotero   && pass "Installed Zotero"   || fail "Install Zotero" "brew install failed"; }
    else fail "Install apps" "Homebrew is not installed (see https://brew.sh)"; fi
  fi
fi

# Locate the vault up front, so it's clear where plugins would go before we ask.
locate_vaults

if ask "Set up the Obsidian plugins (ScholarWeft, ZotLit, BRAT) and their settings? (Close Obsidian first.)" "Set up Obsidian plugins"; then
  if pgrep -x Obsidian >/dev/null 2>&1; then
    fail "Set up Obsidian plugins" "Obsidian was running — quit Obsidian and re-run (the settings writes need it closed)"
  elif pick_vault; then
    disable_conflicting_plugins "$VAULT"
    install_obsidian_plugin "nebedaay/ScholarWeft" "scholar-weft" "$VAULT"
    install_obsidian_plugin "PKM-er/obsidian-zotlit" "zotlit" "$VAULT"
    install_obsidian_plugin "TfTHacker/obsidian42-brat" "obsidian42-brat" "$VAULT"
    install_zotlit_templates "$VAULT"
    set_zotlit_folder "$VAULT"
    brat_register "$VAULT"
  fi
fi

if ask "Install the Better BibTeX and ZotLit extensions into Zotero? (Close Zotero first.)" "Install Zotero extensions"; then
  if zotero_running; then fail "Install Zotero extensions" "Zotero was running — quit Zotero and re-run"
  elif [ -z "$ZPROFILE" ]; then fail "Install Zotero extensions" "Zotero profile not found — open Zotero once, then re-run"
  else
    if [ -f "$ZPROFILE/extensions/better-bibtex@iris-advies.com.xpi" ]; then pass "Better BibTeX already installed"
    else install_zotero_addon "retorquere/zotero-better-bibtex" "better-bibtex@iris-advies.com" && pass "Installed Better BibTeX"; fi
    if [ -f "$ZPROFILE/extensions/zotlit@aidenlx.site.xpi" ]; then pass "ZotLit Zotero add-on already installed"
    else install_zotero_addon "zotlit" "zotlit@aidenlx.site" && pass "Installed the ZotLit Zotero add-on"; fi
  fi
fi

if ask "Set Zotero to allow other applications (like Obsidian) to connect? (Close Zotero first.)" "Enable Zotero local connection"; then
  if zotero_running; then fail "Enable Zotero local connection" "Zotero was running — quit Zotero and re-run"
  elif [ -z "$ZPROFILE" ]; then fail "Enable Zotero local connection" "Zotero profile not found — open Zotero once, then re-run"
  elif grep -q 'extensions.zotero.httpServer.localAPI.enabled", true' "$ZPROFILE/prefs.js"; then pass "Zotero local connection already enabled"
  else
    step "Editing Zotero's preferences (a backup is saved)…"
    cp "$ZPROFILE/prefs.js" "$ZPROFILE/prefs.js.scholarweft.bak.$(date +%s)"
    set_pref "$ZPROFILE/prefs.js" "extensions.zotero.httpServer.enabled" "true"
    set_pref "$ZPROFILE/prefs.js" "extensions.zotero.httpServer.localAPI.enabled" "true"
    if grep -q 'better-bibtex.citekeyFormat"' "$ZPROFILE/prefs.js"; then
      set_pref "$ZPROFILE/prefs.js" "extensions.zotero.translators.better-bibtex.citekeyFormat" '"auth(15).lower.alphanum.nopunct + shorttitle(2,2).nopunct.alphanum + year.alphanum.nopunct"'
      set_pref "$ZPROFILE/prefs.js" "extensions.zotero.translators.better-bibtex.citekeyFormatEditing" '"auth(15).lower.alphanum.nopunct + shorttitle(2,2).nopunct.alphanum + year.alphanum.nopunct"'
    fi
    pass "Enabled Zotero's local connection (start Zotero again to apply)"
  fi
fi

if ask "Install Python, its packages, and Pandoc (required for document import/export)?" "Install Python + Pandoc"; then
  ensure_python
  if have pandoc; then pass "Pandoc already installed"
  else step "Installing Pandoc…"; brew install pandoc && pass "Installed Pandoc" || fail "Install Pandoc" "brew install failed"; fi
fi

if ask "Install LibreOffice (required for PDF export using DOCX/ODT templates)?" "Install LibreOffice"; then
  if [ -d /Applications/LibreOffice.app ]; then pass "LibreOffice already installed"
  else step "Installing LibreOffice (large download)…"; brew install --cask libreoffice && pass "Installed LibreOffice" || fail "Install LibreOffice" "brew install failed"; fi
fi

if ask "Install LaTeX (required for PDF export using .tex templates; may be several GB)?" "Install LaTeX"; then
  if have lualatex || [ -x /Library/TeX/texbin/lualatex ]; then pass "LaTeX already installed"
  else step "Installing MacTeX…"; brew install --cask mactex-no-gui && pass "Installed LaTeX" || fail "Install LaTeX" "brew install failed"; fi
fi

if have lualatex || [ -x /Library/TeX/texbin/lualatex ]; then
  if ask "Install the fonts the LaTeX templates expect (Noto Serif/Sans/Emoji, Scheherazade)?" "Install fonts"; then
    for spec in "${FONT_SPECS[@]}"; do
      cask="${spec%%|*}"; rest="${spec#*|*}"; fam="${rest%%|*}"; pat="${rest##*|}"
      if font_present "$fam" "$pat"; then pass "$fam already installed"
      else
        step "Installing $cask…"
        if out="$(brew install --cask "$cask" 2>&1)"; then pass "Installed $fam"
        elif printf '%s' "$out" | grep -qi 'already a Font'; then
          pass "$fam already present (a font file with that name exists — skipped)"
        else fail "Install $fam" "brew install failed"; fi
      fi
    done
    /Library/TeX/texbin/luaotfload-tool --update 2>/dev/null || true
  fi
fi

# ── Summary ──────────────────────────────────────────────────────────────────
say "Summary"
if [ "${#DONE[@]}" -gt 0 ]; then echo "  Succeeded:"; for x in "${DONE[@]}"; do echo "    ✓ $x"; done; fi
if [ "${#FAILED[@]}" -gt 0 ]; then echo; echo "  Failed:"; for x in "${FAILED[@]}"; do echo "    ✗ $x"; done; fi
if [ "${#SKIPPED[@]}" -gt 0 ]; then echo; echo "  Skipped:"; for x in "${SKIPPED[@]}"; do echo "    – $x"; done; fi
if [ "${#FAILED[@]}" -gt 0 ]; then
  echo
  echo "  Fix the reason shown next to each failure (e.g. quit Zotero and re-run for"
  echo "  the Zotero steps) and run this script again. If a failure has no clear reason,"
  echo "  follow the matching step in the manual instructions: docs/setup.md."
fi
[ -n "$PY" ] && echo "  Python ScholarWeft can use: $PY"
echo
echo "  Then: start Zotero if it was closed; restart Obsidian and enable any plugins"
echo "  in Settings → Community plugins; click Retry in ScholarWeft's settings if it"
echo "  says \"Cannot connect to Zotero\"."
echo "  If you installed ZotLit: the templates are in sw-zotlit-templates/. Point"
echo "  ZotLit's \"Template folder\" there, or click ScholarWeft's \"Install and use"
echo "  ScholarWeft's ZotLit import templates\" button, which sets it for you."
