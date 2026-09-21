# build-and-bump.ps1
#
# Run this from PowerShell, from inside the plugin repo root (the same
# folder as setup.py) - e.g.:
#   cd "C:\Users\tyler\PycharmProjects\OMG Harness\inventree omg plugin"
#   .\build-and-bump.ps1
#
# Builds all three panels (sync, SO export, import harness) and bumps
# the plugin's version afterward (in omg_import_plugin/version.py,
# which setup.py imports from). The version bump matters for a real
# reason, not just bookkeeping: pip tracks installed packages by
# name+version, not by content - if the version string never changes,
# a later `inventree run invoke update` can silently skip reinstalling
# even a genuinely different commit (confirmed the hard way earlier -
# see README.md's "Known issues" #3). Bumping it here means you never
# have to remember --force-reinstall by hand again.

$ErrorActionPreference = "Stop"

# Fail fast with a clear message rather than a cryptic error partway
# through, if this is run from a fresh PowerShell window that hasn't
# picked up a Node.js install on PATH yet.
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Error "npm isn't on PATH in this PowerShell window. If you just installed Node.js, close this window and open a brand new one - PATH is only re-read when a shell starts."
    exit 1
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Error "git isn't on PATH in this PowerShell window."
    exit 1
}

$root = $PSScriptRoot
# panel folder name -> the prefix its own compiled output files use
# (confirmed from real build output: Panel.js/Panel-<hash>.js for
# sync_panel_source, etc). Used below to clean up only THIS panel's
# own old hashed files before rebuilding it, without touching the
# other two panels' output in the same shared static/ folder.
$panels = [ordered]@{
    "sync_panel_source"           = "Panel"
    "so_export_panel_source"      = "SOExportPanel"
    "import_harness_panel_source" = "ImportHarnessPanel"
}
$staticPath = Join-Path $root "omg_import_plugin\static"

foreach ($panel in $panels.Keys) {
    $prefix = $panels[$panel]
    $panelPath = Join-Path $root "omg_import_plugin\$panel"
    if (-not (Test-Path $panelPath)) {
        Write-Error "Expected folder not found: $panelPath - is this script sitting in the repo root, next to setup.py?"
        exit 1
    }

    # Clean up this panel's own old hashed output (Panel-<oldhash>.js
    # and its .map) before rebuilding - not --emptyOutDir, which would
    # wipe the other two panels' files sharing this same folder. The
    # exact non-hashed name (Panel.js) gets overwritten either way, so
    # it's excluded here; only the hash-suffixed cache-busting copies
    # accumulate otherwise.
    if (Test-Path $staticPath) {
        Get-ChildItem $staticPath -File -Filter "$prefix-*.js*" | Remove-Item -Force
    }

    Write-Host ""
    Write-Host "=== Building $panel ===" -ForegroundColor Cyan
    Push-Location $panelPath
    try {
        npm install
        if ($LASTEXITCODE -ne 0) { throw "npm install failed for $panel" }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed for $panel" }
    }
    finally {
        Pop-Location
    }
}

# Bump the version. setup.py doesn't hold a literal version string
# itself - it imports OMG_IMPORT_PLUGIN_VERSION from
# omg_import_plugin/version.py, so that's the actual file to bump.
$versionPath = Join-Path $root "omg_import_plugin\version.py"
if (-not (Test-Path $versionPath)) {
    Write-Warning "version.py not found at $versionPath - builds finished, but the version was NOT bumped. Bump it yourself before pushing."
}
else {
    $content = Get-Content $versionPath -Raw
    if ($content -match 'OMG_IMPORT_PLUGIN_VERSION\s*=\s*[''"](\d+)\.(\d+)\.(\d+)[''"]') {
        $major = [int]$matches[1]
        $minor = [int]$matches[2]
        $patch = [int]$matches[3] + 1
        $oldVersion = "$($matches[1]).$($matches[2]).$($matches[3])"
        $newVersion = "$major.$minor.$patch"

        $newContent = $content -replace 'OMG_IMPORT_PLUGIN_VERSION\s*=\s*[''"]\d+\.\d+\.\d+[''"]', "OMG_IMPORT_PLUGIN_VERSION = `"$newVersion`""
        Set-Content -Path $versionPath -Value $newContent -NoNewline

        Write-Host ""
        Write-Host "Version bumped: $oldVersion -> $newVersion" -ForegroundColor Green
    }
    else {
        Write-Warning "Could not find an OMG_IMPORT_PLUGIN_VERSION = `"X.Y.Z`" line in version.py - builds finished, but the version was NOT bumped. Bump it yourself before pushing."
    }
}

# Quick visual sanity check - all six current files should be present
# together in the shared static folder regardless of build order, with
# no leftover old-hash files from previous runs now that each panel
# cleans up its own before rebuilding.
Write-Host ""
Write-Host "=== static/ contents ===" -ForegroundColor Cyan
Get-ChildItem $staticPath -File | Select-Object Name, Length | Format-Table | Out-String | Write-Host

# Stage everything (new/changed static output, the old stale hashes
# now removed, and the version bump) - left as `git add .` rather than
# also auto-committing, so there's still a deliberate review step
# before anything actually goes to GitHub.
Write-Host ""
Write-Host "=== Staging changes ===" -ForegroundColor Cyan
Push-Location $root
try {
    git add .
    Write-Host ""
    git status
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "Done. Review the staged changes above (especially version.py's version bump), then:" -ForegroundColor Green
Write-Host "  git commit -m `"Rebuild panels, bump version`""
Write-Host "  git push"
