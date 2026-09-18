#!/usr/bin/env bash
#
# ScholarWeft setup helper — Linux (Debian/Ubuntu and Fedora).
#
# Asks before each step (y / n / esc to quit), reports progress, reuses what
# you already have, and ends with a summary. Nothing is changed without a yes.
#
# Usage:  bash install-linux.sh
#
set -o pipefail

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
step() { printf '  \033[36m…\033[0m %s\n' "$*"; }
pass() { printf '  \033[32m✓\033[0m %s\n' "$*"; DONE+=("$*"); }
fail() { printf '  \033[31m✗\033[0m %s%s\n' "$*" "${2:+ — $2}"; FAILED+=("$*${2:+ — $2}"); }
skip() { SKIPPED+=("$*"); }
have() { command -v "$1" >/dev/null 2>&1; }

DONE=(); FAILED=(); SKIPPED=()

ask() {
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

PKG=""
have apt && PKG=apt
have dnf && PKG=dnf
apt_install() { if [ "$PKG" = apt ]; then sudo apt-get install -y "$@"; else sudo dnf install -y "$@"; fi; }

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
      -not -path '*/.Trash/*' -not -path '*/node_modules/*' -not -path '*/.cache/*' \
      -not -path '*/.local/*' -not -path '*/.npm/*' -not -path '*/.var/*' \
      -not -path '*/snap/*' 2>/dev/null \
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
    0) step "No Obsidian vault found in your home folder."; IFS= read -r -p "  Path to your vault: " VAULT ;;
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

ZPROFILE=""
for p in "$HOME/.zotero/zotero"/*/prefs.js; do [ -f "$p" ] && { ZPROFILE="$(dirname "$p")"; break; }; done
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
  if grep -qF "user_pref(\"$k\"" "$f"; then sed -i "s|^user_pref(\"$k\".*|user_pref(\"$k\", $v);|" "$f"
  else printf 'user_pref("%s", %s);\n' "$k" "$v" >> "$f"; fi
}

PY=""
for c in python3 "$HOME/miniconda3/bin/python3" "$HOME/anaconda3/bin/python3" \
         /opt/miniconda3/bin/python3 /opt/anaconda3/bin/python3 \
         /usr/bin/python3 "$HOME/.pyenv/shims/python3"; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import lxml, docx, requests' >/dev/null 2>&1; then PY="$(command -v "$c")"; break; fi
done
ensure_python() {
  [ -n "$PY" ] && { pass "Python libraries already present ($PY)"; return 0; }
  step "Installing Python packages (lxml, python-docx, requests)…"
  local c
  for c in "$HOME/miniconda3/bin/python3" "$HOME/anaconda3/bin/python3" \
           /opt/miniconda3/bin/python3 /opt/anaconda3/bin/python3 \
           "$(command -v python3 2>/dev/null || true)"; do
    [ -n "$c" ] && [ -x "$c" ] || continue
    if "$c" -m pip install --quiet lxml python-docx requests >/dev/null 2>&1 \
       && "$c" -c 'import lxml, docx, requests' >/dev/null 2>&1; then PY="$c"; pass "Python libraries installed into $c"; return 0; fi
  done
  [ "$PKG" = apt ] && { step "Installing python3-venv…"; apt_install python3-venv || fail "Install python3-venv" "package install failed"; }
  step "Creating a private Python environment…"
  mkdir -p "$HOME/ScholarWeft"
  python3 -m venv "$HOME/ScholarWeft/venv" || { fail "Create Python env" "venv creation failed"; return 1; }
  PY="$HOME/ScholarWeft/venv/bin/python3"
  "$HOME/ScholarWeft/venv/bin/pip" install --quiet --upgrade pip >/dev/null 2>&1 || true
  "$HOME/ScholarWeft/venv/bin/pip" install --quiet lxml python-docx requests \
    && pass "Created a private Python environment ($PY)" || fail "Python libraries" "pip install failed"
}

# ═════════════════════════════════════════════════════════════════════════════
say "ScholarWeft setup"
echo "  I'll ask before each step — y to install/configure, n to skip, esc to quit."
echo "  Safe to re-run; nothing is changed without a yes."
[ -z "$PKG" ] && printf '  \033[33m!\033[0m Neither apt nor dnf found — package installs will be skipped.\n'

if ! have obsidian || ! have zotero; then
  if ask "Install the Obsidian and Zotero apps with Flatpak?" "Install apps"; then
    if have flatpak; then
      step "Installing Obsidian…"; flatpak install -y flathub md.obsidian.Obsidian && pass "Installed Obsidian" || fail "Install Obsidian" "flatpak failed"
      step "Installing Zotero…";   flatpak install -y flathub org.zotero.Zotero      && pass "Installed Zotero"   || fail "Install Zotero" "flatpak failed"
    else fail "Install apps" "Flatpak is not installed (get the apps from obsidian.md and zotero.org)"; fi
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
  if [ -n "$PKG" ]; then
    have python3 || { step "Installing Python…"; apt_install python3 python3-pip python3-venv && pass "Installed Python" || fail "Install Python" "package install failed"; }
    have pandoc  || { step "Installing Pandoc…"; apt_install pandoc && pass "Installed Pandoc" || fail "Install Pandoc" "package install failed"; }
  fi
  ensure_python
fi

if ask "Install LibreOffice (required for PDF export using DOCX/ODT templates)?" "Install LibreOffice"; then
  if have soffice; then pass "LibreOffice already installed"
  elif [ -n "$PKG" ]; then step "Installing LibreOffice…"; apt_install libreoffice && pass "Installed LibreOffice" || fail "Install LibreOffice" "package install failed"
  else fail "Install LibreOffice" "no package manager — install from libreoffice.org"; fi
fi

if ask "Install LaTeX (required for PDF export using .tex templates; may be several GB)?" "Install LaTeX"; then
  if have lualatex; then pass "LaTeX already installed"
  elif [ -n "$PKG" ]; then
    step "Installing TeX Live…"
    if [ "$PKG" = apt ]; then apt_install texlive-luatex texlive-latex-recommended texlive-fonts-recommended && pass "Installed LaTeX" || fail "Install LaTeX" "package install failed"
    else apt_install texlive-scheme-basic texlive-luatex && pass "Installed LaTeX" || fail "Install LaTeX" "package install failed"; fi
  else fail "Install LaTeX" "no package manager — install TeX Live from tug.org"; fi
fi

if have lualatex; then
  if ask "Install the fonts the LaTeX templates expect (Noto; Scheherazade for Arabic)?" "Install fonts"; then
    if [ "$PKG" = apt ]; then apt_install fonts-noto-core fonts-noto-extra && pass "Installed Noto fonts" || fail "Install fonts" "package install failed"; fi
    cat <<'EOF'
  For emoji, install the MONOCHROME "Noto Emoji" (https://github.com/googlefonts/noto-emoji);
  colour emoji fonts can't be used by LaTeX. For Arabic, Scheherazade New
  (https://software.sil.org/scheherazade/): copy the .ttf files to ~/.fonts, then run  fc-cache -f
EOF
    luaotfload-tool --update 2>/dev/null || true
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
