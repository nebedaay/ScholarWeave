# ScholarWeft setup helper — Windows (PowerShell).
#
# Asks before each step (y / n / esc to quit), reports progress, reuses what
# you already have, and ends with a summary of what succeeded / failed / was
# skipped. Nothing is changed without a yes; safe to re-run.
#
# Usage (in PowerShell):
#   powershell -ExecutionPolicy Bypass -File .\install-windows.ps1

$ErrorActionPreference = 'Stop'
$script:Done = @(); $script:Failed = @(); $script:Skipped = @()

function Say($m)  { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Step($m) { Write-Host "  ... $m" -ForegroundColor DarkCyan }
function Pass($m) { Write-Host "  [ok] $m" -ForegroundColor Green;  $script:Done += $m }
function Fail($m, $why) {
  if ($why) { Write-Host "  [x] $m — $why" -ForegroundColor Red; $script:Failed += "$m — $why" }
  else      { Write-Host "  [x] $m" -ForegroundColor Red;         $script:Failed += $m }
}
function Skip($m) { if ($m) { $script:Skipped += $m } }
function Have($c) { [bool](Get-Command $c -ErrorAction SilentlyContinue) }

function Ask($q, $label) {
  while ($true) {
    $a = Read-Host "$q (y/n/esc)"
    if ($a -match '^[yY]') { return $true }
    if ($a -match '^[nN]') { Skip $label; return $false }
    if ($a -match '^(esc|q)$') { Write-Host '  Cancelled — nothing more will be changed.'; exit 0 }
    Write-Host '  Please answer y or n (or esc/q to quit).'
  }
}

$UA = @{ 'User-Agent' = 'ScholarWeft' }
function Download($url, $dest, $label) {
  Step "Downloading $label..."
  try { Invoke-WebRequest $url -OutFile $dest } catch { Fail "Download $label" 'download failed'; return $false }
  return $true
}

# ── Obsidian vault discovery ─────────────────────────────────────────────────
function Find-Vaults {
  $hits = Get-ChildItem -Path $HOME -Directory -Recurse -Depth 5 -Filter '.obsidian' -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '\\(AppData|node_modules|\.git|\.cache|\.local|\.npm)\\' } |
    ForEach-Object { $_.Parent.FullName } |
    Where-Object { $_ -notmatch '(\.bk| copy|\.20\d\d-\d\d-\d\d)$' } |
    Sort-Object -Unique
  $out = @()
  foreach ($v in $hits) { if (-not ($out | Where-Object { $v -like "$_*" })) { $out += $v } }
  return $out
}
$script:Vault = ''
function Pick-Vault {
  if ($script:Vault) { return $true }
  $vaults = @(Find-Vaults)
  if ($vaults.Count -eq 0) {
    Step 'No Obsidian vault found in your home folder.'
    $script:Vault = Read-Host '  Path to your vault'
  } elseif ($vaults.Count -eq 1) {
    $script:Vault = $vaults[0]; Step "Found vault: $($script:Vault)"
  } else {
    Write-Host "  Found $($vaults.Count) Obsidian vaults:"
    for ($i = 0; $i -lt $vaults.Count; $i++) { Write-Host ("    {0}) {1}" -f ($i + 1), $vaults[$i]) }
    $n = Read-Host "  Which one should I use? (1-$($vaults.Count))"
    if ($n -match '^\d+$' -and [int]$n -ge 1 -and [int]$n -le $vaults.Count) { $script:Vault = $vaults[[int]$n - 1] }
    else { Fail 'Choose vault' 'no valid choice made'; return $false }
  }
  if (-not (Test-Path $script:Vault)) { Fail 'Choose vault' 'folder does not exist'; $script:Vault = ''; return $false }
  return $true
}

function Enable-Plugin($vault, $id) {
  New-Item -ItemType Directory -Force -Path (Join-Path $vault ".obsidian\plugins\$id") | Out-Null
  $f = Join-Path $vault '.obsidian\community-plugins.json'
  $arr = @()
  if (Test-Path $f) {
    $txt = Get-Content $f -Raw
    if ($txt -match [regex]::Escape($id)) { return }
    try { $arr = @($txt | ConvertFrom-Json) } catch { $arr = @() }
  }
  $arr = @($arr) + $id
  ($arr | ConvertTo-Json) | Set-Content -Path $f
}
function Install-ObsidianPlugin($repo, $id, $vault) {
  $dir = Join-Path $vault ".obsidian\plugins\$id"
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  foreach ($a in 'main.js', 'manifest.json', 'styles.css') {
    try { $d = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest" -Headers $UA } catch { Fail "Install $id plugin" 'could not read the release info'; return }
    $u = ($d.assets | Where-Object { $_.name -eq $a } | Select-Object -First 1).browser_download_url
    if (-not $u) { Fail "Install $id plugin" "release is missing $a"; return }
    if (-not (Download $u (Join-Path $dir $a) $a)) { return }
  }
  Enable-Plugin $vault $id
  Pass "Installed the $id Obsidian plugin"
}

$ZPrefs = Get-ChildItem "$env:APPDATA\Zotero\Zotero\Profiles\*\prefs.js" -ErrorAction SilentlyContinue | Select-Object -First 1
$ZDir = if ($ZPrefs) { $ZPrefs.DirectoryName } else { $null }
function Zotero-Running { [bool](Get-Process zotero -ErrorAction SilentlyContinue) }
function Install-ZoteroAddon($repo, $id) {
  $url = $null
  try {
    if ($repo -eq 'zotlit') {
      Step 'Looking up the latest ZotLit Zotero add-on...'
      $rels = Invoke-RestMethod 'https://api.github.com/repos/aidenlx/zotlit/releases?per_page=100' -Headers $UA
      $withXpi = $rels | Where-Object { $_.assets | Where-Object { $_.name -like '*.xpi' } }
      $rel = ($withXpi | Where-Object { -not $_.prerelease } | Select-Object -First 1); if (-not $rel) { $rel = $withXpi | Select-Object -First 1 }
      $url = ($rel.assets | Where-Object { $_.name -like '*.xpi' } | Select-Object -First 1).browser_download_url
    } else {
      $d = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest" -Headers $UA
      $url = ($d.assets | Where-Object { $_.name -like '*.xpi' } | Select-Object -First 1).browser_download_url
    }
  } catch {}
  if (-not $url) { Fail "Install $id" 'could not resolve the download URL'; return $false }
  New-Item -ItemType Directory -Force -Path (Join-Path $ZDir 'extensions') | Out-Null
  return (Download $url (Join-Path $ZDir "extensions\$id.xpi") "$id.xpi")
}
function Set-Pref($key, $value) {
  $t = Get-Content $ZPrefs.FullName -Raw
  $line = 'user_pref("' + $key + '", ' + $value + ');'
  $pattern = 'user_pref\("' + [regex]::Escape($key) + '".*?\);'
  if ($t -match $pattern) { $t = [regex]::Replace($t, $pattern, $line) } else { $t = $t.TrimEnd() + "`r`n" + $line + "`r`n" }
  Set-Content -Path $ZPrefs.FullName -Value $t -NoNewline
}

function Ensure-Python {
  foreach ($c in @(@('py', '-3'), @('python', $null))) {
    if (Have $c[0]) {
      try {
        if ($c[1]) { & $c[0] $c[1] -c 'import lxml, docx, requests' 2>$null } else { & $c[0] -c 'import lxml, docx, requests' 2>$null }
        if ($LASTEXITCODE -eq 0) { Pass 'Python libraries already present'; return }
      } catch {}
    }
  }
  if (-not (Have py) -and -not (Have python)) { Winget 'Python.Python.3.12' }
  $venv = "$HOME\ScholarWeft\venv"
  Step 'Creating a private Python environment...'
  New-Item -ItemType Directory -Force -Path "$HOME\ScholarWeft" | Out-Null
  try { if (Have py) { py -m venv $venv } else { python -m venv $venv } } catch { Fail 'Create Python env' 'venv creation failed'; return }
  & "$venv\Scripts\pip" install --quiet --upgrade pip
  & "$venv\Scripts\pip" install --quiet lxml python-docx requests
  Pass "Created a private Python environment ($venv\Scripts\python.exe)"
}

function Winget($id) { Step "Installing $id..."; winget install --id $id -e --accept-source-agreements --accept-package-agreements }

# ═════════════════════════════════════════════════════════════════════════════
Say 'ScholarWeft setup'
Write-Host "  I'll ask before each step — y to install/configure, n to skip, esc to quit."
Write-Host "  Safe to re-run; nothing is changed without a yes."
if (-not (Have winget)) { Write-Host "  [!] 'winget' is missing; app/package installs will be skipped (install 'App Installer')." -ForegroundColor Yellow }

if (-not (Have obsidian) -or -not (Have zotero)) {
  if (Ask 'Install the Obsidian and Zotero apps with winget?' 'Install apps') {
    if (Have winget) {
      if (-not (Have obsidian)) { Winget 'Obsidian.Obsidian' }
      if (-not (Have zotero))   { Winget 'Zotero.Zotero' }
    } else { Fail 'Install apps' 'winget is not available' }
  }
}

if (Ask 'Install the ScholarWeft plugin into Obsidian?' 'Install ScholarWeft plugin') { if (Pick-Vault) { Install-ObsidianPlugin 'nebedaay/ScholarWeft' 'scholar-weft' $script:Vault } }
if (Ask 'Install the ZotLit plugin into Obsidian?' 'Install ZotLit plugin')         { if (Pick-Vault) { Install-ObsidianPlugin 'PKM-er/obsidian-zotlit' 'zotlit' $script:Vault } }

if (Ask 'Install the Better BibTeX and ZotLit extensions into Zotero? (Close Zotero first.)' 'Install Zotero extensions') {
  if (Zotero-Running) { Fail 'Install Zotero extensions' 'Zotero was running — quit Zotero and re-run' }
  elseif (-not $ZDir) { Fail 'Install Zotero extensions' 'Zotero profile not found — open Zotero once, then re-run' }
  else {
    if (Test-Path (Join-Path $ZDir 'extensions\better-bibtex@iris-advies.com.xpi')) { Pass 'Better BibTeX already installed' }
    elseif (Install-ZoteroAddon 'retorquere/zotero-better-bibtex' 'better-bibtex@iris-advies.com') { Pass 'Installed Better BibTeX' }
    if (Test-Path (Join-Path $ZDir 'extensions\zotlit@aidenlx.site.xpi')) { Pass 'ZotLit Zotero add-on already installed' }
    elseif (Install-ZoteroAddon 'zotlit' 'zotlit@aidenlx.site') { Pass 'Installed the ZotLit Zotero add-on' }
  }
}

if (Ask 'Set Zotero to allow other applications (like Obsidian) to connect? (Close Zotero first.)' 'Enable Zotero local connection') {
  if (Zotero-Running) { Fail 'Enable Zotero local connection' 'Zotero was running — quit Zotero and re-run' }
  elseif (-not $ZPrefs) { Fail 'Enable Zotero local connection' 'Zotero profile not found — open Zotero once, then re-run' }
  elseif ((Get-Content $ZPrefs.FullName -Raw) -match 'extensions\.zotero\.httpServer\.localAPI\.enabled",\s*true') { Pass 'Zotero local connection already enabled' }
  else {
    Step "Editing Zotero's preferences (a backup is saved)..."
    Copy-Item $ZPrefs.FullName "$($ZPrefs.FullName).scholarweft.bak" -Force
    Set-Pref 'extensions.zotero.httpServer.enabled' 'true'
    Set-Pref 'extensions.zotero.httpServer.localAPI.enabled' 'true'
    if ((Get-Content $ZPrefs.FullName -Raw) -match 'better-bibtex\.citekeyFormat"') {
      Set-Pref 'extensions.zotero.translators.better-bibtex.citekeyFormat' '"auth(15).lower.alphanum.nopunct + shorttitle(2,2).nopunct.alphanum + year.alphanum.nopunct"'
      Set-Pref 'extensions.zotero.translators.better-bibtex.citekeyFormatEditing' '"auth(15).lower.alphanum.nopunct + shorttitle(2,2).nopunct.alphanum + year.alphanum.nopunct"'
    }
    Pass "Enabled Zotero's local connection (start Zotero again to apply)"
  }
}

if (Ask 'Install Python, its packages, and Pandoc (required for document import/export)?' 'Install Python + Pandoc') {
  Ensure-Python
  if (Have pandoc) { Pass 'Pandoc already installed' } elseif (Have winget) { Winget 'JohnMacFarlane.Pandoc'; Pass 'Installed Pandoc' }
}

if (Ask 'Install LibreOffice (required for PDF export using DOCX/ODT templates)?' 'Install LibreOffice') {
  if (Have soffice) { Pass 'LibreOffice already installed' } elseif (Have winget) { Winget 'TheDocumentFoundation.LibreOffice'; Pass 'Installed LibreOffice' }
}

if (Ask 'Install MiKTeX (required for PDF export using .tex templates; may be several GB)?' 'Install LaTeX') {
  if (Have lualatex) { Pass 'LaTeX already installed' } elseif (Have winget) { Winget 'MiKTeX.MiKTeX'; Pass 'Installed LaTeX' }
}

if (Have lualatex) {
  if (Ask 'Install the fonts the LaTeX templates expect (Noto; Scheherazade for Arabic)?' 'Install fonts') {
    Write-Host @"
  Download and install (double-click → Install):
    • Noto Serif, Noto Sans, Noto Emoji (MONOCHROME)  →  https://fonts.google.com
    • Scheherazade New (Arabic)                       →  https://software.sil.org/scheherazade/
"@
    Pass 'Showed font download links'
  }
}

# ── Summary ──────────────────────────────────────────────────────────────────
Say 'Summary'
if ($script:Done.Count)    { Write-Host '  Succeeded:'; $script:Done    | ForEach-Object { Write-Host "    [ok] $_" -ForegroundColor Green } }
if ($script:Failed.Count)  { Write-Host "`n  Failed:";  $script:Failed  | ForEach-Object { Write-Host "    [x] $_"  -ForegroundColor Red } }
if ($script:Skipped.Count) { Write-Host "`n  Skipped:"; $script:Skipped | ForEach-Object { Write-Host "    - $_" } }
if ($script:Failed.Count) {
  Write-Host "`n  Fix the reason shown next to each failure (e.g. quit Zotero and re-run for"
  Write-Host '  the Zotero steps) and run this script again. If a failure has no clear reason,'
  Write-Host '  follow the matching step in the manual instructions: docs/setup.md.'
}
Write-Host "`n  Then: start Zotero if it was closed; restart Obsidian and enable any plugins"
Write-Host "  in Settings → Community plugins; click Retry in ScholarWeft's settings if it"
Write-Host '  says "Cannot connect to Zotero".'
