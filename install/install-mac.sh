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
fail() { printf '  \033[31m✗\033[0m %s%s\n' "$*" "${2:+ — $2}"; FAILED+=("$*${2:+ — $2}"); }
skip() { SKIPPED+=("$*"); }
have() { command -v "$1" >/dev/null 2>&1; }

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

# ── download helpers ─────────────────────────────────────────────────────────
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
    find "$HOME" -maxdepth 5 -type d -name '.obsidian' \
      -not -path '*/Library/*' -not -path '*/.Trash/*' -not -path '*/node_modules/*' \
      -not -path '*/.cache/*' -not -path '*/.local/*' -not -path '*/.npm/*' \
      -not -path '*/Applications/*' 2>/dev/null \
    | sed 's:/.obsidian/*$::' \
    | grep -viE '(\.bk| copy|\.20[0-9]{2}-[0-9]{2}-[0-9]{2})(/|$)' \
    | sort -u
  )
  for v in "${list[@]}"; do printf '%s\n' "$v"; done
}
VAULT=""
pick_vault() {
  [ -n "$VAULT" ] && return 0
  local vaults=() v n i
  while IFS= read -r v; do [ -n "$v" ] && vaults+=("$v"); done < <(find_vaults)
  case "${#vaults[@]}" in
    0) step "No Obsidian vault found in your home folder."
       IFS= read -r -p "  Path to your vault: " VAULT ;;
    1) VAULT="${vaults[0]}"; step "Found vault: $VAULT" ;;
    *) echo "  Found ${#vaults[@]} Obsidian vaults:"
       i=1; for v in "${vaults[@]}"; do printf '    %d) %s\n' "$i" "$v"; i=$((i+1)); done
       IFS= read -r -p "  Which one should I use? (1-${#vaults[@]}) " n
       if [[ "$n" =~ ^[0-9]+$ ]] && [ "$n" -ge 1 ] && [ "$n" -le "${#vaults[@]}" ]; then VAULT="${vaults[$((n-1))]}"
       else fail "Choose vault" "no valid choice made"; return 1; fi ;;
  esac
  if [ ! -d "$VAULT" ]; then fail "Choose vault" "folder does not exist"; VAULT=""; return 1; fi
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
  local repo="$1" id="$2" vault="$3" dir="$vault/.obsidian/plugins/$id" a u
  mkdir -p "$dir"
  for a in main.js manifest.json styles.css; do
    u="$(gh_asset_url "$repo" "/$a")"
    if [ -n "$u" ]; then download "$u" "$dir/$a" "$a" || return 1
    else fail "Install $id plugin" "could not find $a in $repo releases"; return 1; fi
  done
  enable_plugin "$vault" "$id"
  pass "Installed the $id Obsidian plugin"
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
say "ScholarWeft setup"
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

if ask "Install the ScholarWeft plugin into Obsidian?" "Install ScholarWeft plugin"; then
  pick_vault && install_obsidian_plugin "nebedaay/ScholarWeft" "scholar-weft" "$VAULT"
fi
if ask "Install the ZotLit plugin into Obsidian?" "Install ZotLit plugin"; then
  pick_vault && install_obsidian_plugin "PKM-er/obsidian-zotlit" "zotlit" "$VAULT"
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
    for cask in font-noto-serif font-noto-sans font-noto-emoji font-scheherazade-new; do
      step "Installing $cask…"; brew install --cask "$cask" && pass "Installed $cask" || fail "Install $cask" "brew install failed"
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
