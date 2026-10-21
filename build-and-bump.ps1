# build-and-bump.ps1 - release the OMG InvenTree plugin
#
# Run from PowerShell inside the plugin repo root (the folder with setup.py), e.g.
#   cd "C:\Users\tyler\PycharmProjects\OMG Harness\inventree omg plugin"
#   .\build-and-bump.ps1                  # 0.2.0 -> 0.2.1 (patch)
#   .\build-and-bump.ps1 -Bump minor      # 0.2.1 -> 0.3.0
#   .\build-and-bump.ps1 -Bump major      # 0.3.0 -> 1.0.0
#   .\build-and-bump.ps1 -Version 0.2.0   # set an exact version
#   .\build-and-bump.ps1 -SkipBuild       # no panel changes - skip the npm builds
#   .\build-and-bump.ps1 -Yes             # don't ask (e.g. empty changelog)
#
# What it does, in order (it stops at the first problem, before committing anything):
#   1. Checks: git (and npm unless -SkipBuild), version.py and CHANGELOG.md exist,
#      the new tag doesn't already exist here or on GitHub.
#   2. Builds all five panels and merges their Vite manifests (see the note at
#      $mergedManifest for why).
#   3. Moves setup.py's mouser-lookup pin to mouser_shared's newest release tag.
#   4. Checks every .py file compiles (needs Python 3 on PATH or your venv active).
#   5. Bumps omg_import_plugin/version.py (setup.py reads it from there) and turns
#      "## Unreleased" in CHANGELOG.md into "## X.Y.Z - date".
#   6. Commits ALL current changes as "Release vX.Y.Z" and tags vX.Y.Z.
# It does NOT push. Push the commit AND the tag (PyCharm: tick "Push tags").
#
# Why a tag: the server's plugins.txt pins the plugin to a release tag
# (...inventree_omg_plugin.git@vX.Y.Z). A new tag is a new install link, so pip
# really reinstalls - the bare repo URL let pip skip the update whenever it thought
# that URL was "already installed" (README "Known issue #3"). And only tags deploy:
# CI publishes the GitHub release and updates the server when a tag is pushed, so a
# push to master or a branch never reaches InvenTree on its own.
#
# Works in Windows PowerShell 5.1 (PyCharm's terminal) and PowerShell 7.
# Keep this file plain ASCII: 5.1 reads BOM-less scripts as ANSI.

param(
    [ValidateSet('patch', 'minor', 'major')]
    [string]$Bump = 'patch',
    [string]$Version,
    [switch]$SkipBuild,
    [switch]$Yes
)

# Native commands (git, npm, python) report through exit codes; 'Stop' would turn
# their stderr chatter into exceptions in Windows PowerShell 5.1.
$ErrorActionPreference = 'Continue'
$repoUrl = 'https://github.com/SXSLYDA/inventree_omg_plugin.git'
$mouserRepo = 'https://github.com/SXSLYDA/mouser_shared.git'
$utf8 = New-Object System.Text.UTF8Encoding $false
$emDash = [string][char]0x2014

function Fail([string]$message) {
    Write-Host ""
    Write-Host "STOPPED: $message" -ForegroundColor Red
    Write-Host "Nothing was committed or tagged." -ForegroundColor Red
    Pop-Location
    exit 1
}

function Step([string]$title) {
    Write-Host ""
    Write-Host "=== $title ===" -ForegroundColor Cyan
}

function Read-Text([string]$path) { return [System.IO.File]::ReadAllText($path, $utf8) }
function Write-Text([string]$path, [string]$text) { [System.IO.File]::WriteAllText($path, $text, $utf8) }

function Find-Python {
    foreach ($candidate in @('python', 'python3', 'py')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) {
            & $cmd.Source -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) { return $cmd.Source }
        }
    }
    return $null
}

$root = $PSScriptRoot
Push-Location $root

# ------------------------------------------------------------------ 1. checks
Step "Checking"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "git isn't on PATH in this window." }
if (-not $SkipBuild -and -not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Fail "npm isn't on PATH. If you just installed Node.js, open a brand new window (PATH is read when a shell starts), or use -SkipBuild if no panel changed."
}
git rev-parse --is-inside-work-tree 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "this folder isn't a git repository." }

$versionPath = Join-Path $root 'omg_import_plugin\version.py'
$changelogPath = Join-Path $root 'CHANGELOG.md'
$setupPath = Join-Path $root 'setup.py'
if (-not (Test-Path $versionPath)) { Fail "omg_import_plugin\version.py not found - run this from the plugin repo root (next to setup.py)." }
if (-not (Test-Path $changelogPath)) { Fail "CHANGELOG.md not found." }

$versionText = Read-Text $versionPath
$match = [regex]::Match($versionText, 'OMG_IMPORT_PLUGIN_VERSION\s*=\s*[''"](\d+)\.(\d+)\.(\d+)[''"]')
if (-not $match.Success) { Fail "couldn't find OMG_IMPORT_PLUGIN_VERSION = `"X.Y.Z`" in version.py." }
$major = [int]$match.Groups[1].Value
$minor = [int]$match.Groups[2].Value
$patch = [int]$match.Groups[3].Value
$oldVersion = "$major.$minor.$patch"

if ($Version) {
    if ($Version -notmatch '^\d+\.\d+\.\d+$') { Fail "-Version must look like 1.2.3" }
    $newVersion = $Version
} elseif ($Bump -eq 'major') {
    $newVersion = "$($major + 1).0.0"
} elseif ($Bump -eq 'minor') {
    $newVersion = "$major.$($minor + 1).0"
} else {
    $newVersion = "$major.$minor.$($patch + 1)"
}
$newParts = $newVersion.Split('.') | ForEach-Object { [int]$_ }
$isNewer = ($newParts[0] -gt $major) -or ($newParts[0] -eq $major -and $newParts[1] -gt $minor) -or
           ($newParts[0] -eq $major -and $newParts[1] -eq $minor -and $newParts[2] -gt $patch)
if (-not $isNewer) { Fail "$newVersion isn't newer than the current $oldVersion." }
$tag = "v$newVersion"

$branch = (git rev-parse --abbrev-ref HEAD 2>$null)
Write-Host "Branch: $branch    Version: $oldVersion -> $newVersion    Tag: $tag"
if ($branch -ne 'master' -and $branch -ne 'main') {
    Write-Host "Not on master - the release commit and tag will be on '$branch'. Merge it into master afterwards." -ForegroundColor Yellow
}

if (git tag --list $tag) { Fail "tag $tag already exists here. Pick another version with -Version." }
$remoteTag = git ls-remote --tags origin "refs/tags/$tag" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Couldn't reach GitHub to check tags (offline?) - carrying on." -ForegroundColor Yellow
} elseif ($remoteTag) {
    Fail "tag $tag already exists on GitHub. Pull first, or pick another version with -Version."
}

# ------------------------------------------------------------------ 2. build panels
$staticPath = Join-Path $root "omg_import_plugin\static"
if ($SkipBuild) {
    Step "Building panels"
    Write-Host "Skipped (-SkipBuild) - the committed static/ files are released as they are." -ForegroundColor Yellow
} else {
    # panel folder name -> the prefix its own compiled output files use
    $panels = [ordered]@{
        "sync_panel_source"           = "Panel"
        "so_export_panel_source"      = "SOExportPanel"
        "import_harness_panel_source" = "ImportHarnessPanel"
        "part_setup_panel_source"     = "PartSetupPanel"
        "pending_parts_panel_source"  = "PendingPartsPanel"
    }
    $manifestPath = Join-Path $staticPath ".vite\manifest.json"
    # Every panel's Vite build writes the SAME static/.vite/manifest.json, each
    # one overwriting the last - so only the last panel built kept InvenTree's
    # cache-busting (it serves the hashed file only when the manifest lists it,
    # inventree/InvenTree#11565, 1.3.0+) and the others could stay cached in
    # browsers across an update. Each panel's entry is captured right after its
    # build and all are written back together at the end.
    $mergedManifest = [ordered]@{}

    foreach ($panel in $panels.Keys) {
        $prefix = $panels[$panel]
        $panelPath = Join-Path $root "omg_import_plugin\$panel"
        if (-not (Test-Path $panelPath)) { Fail "expected folder not found: $panelPath" }

        # Remove this panel's own old hashed output (not --emptyOutDir, which
        # would wipe the other panels' files in the same folder).
        if (Test-Path $staticPath) {
            Get-ChildItem $staticPath -File -Filter "$prefix-*.js*" | Remove-Item -Force
        }

        Step "Building $panel"
        Push-Location $panelPath
        npm install
        $installExit = $LASTEXITCODE
        if ($installExit -eq 0) { npm run build }
        $buildExit = $LASTEXITCODE
        Pop-Location
        if ($installExit -ne 0) { Fail "npm install failed for $panel (above)." }
        if ($buildExit -ne 0) { Fail "npm run build failed for $panel (above)." }

        if (Test-Path $manifestPath) {
            $panelManifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
            foreach ($entryKey in $panelManifest.PSObject.Properties.Name) {
                $mergedManifest[$entryKey] = $panelManifest.$entryKey
            }
        } else {
            Write-Warning "No manifest.json after building $panel - its cache-busting entry will be missing."
        }
    }
    $mergedManifest | ConvertTo-Json -Depth 10 | Set-Content -Path $manifestPath -NoNewline
    Write-Host ""
    Write-Host "Merged manifest.json covers $($mergedManifest.Count) panel(s): $($mergedManifest.Keys -join ', ')"
}

# ------------------------------------------------------------------ 3. mouser_shared pin
# setup.py pins mouser-lookup to mouser_shared's NEWEST release tag - the pin
# (...mouser_shared.git@vX.Y.Z) is what makes pip reinstall it. Never downgrades;
# if GitHub can't be reached it warns and leaves the pin alone.
Step "Checking mouser_shared for a newer release"
$mouserPinChange = $null
$remoteRefs = git ls-remote --tags --refs $mouserRepo 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Couldn't list mouser_shared's tags (offline, or no access) - setup.py's mouser-lookup pin left as is."
} else {
    $releases = @()
    foreach ($line in $remoteRefs) {
        if ($line -match 'refs/tags/v(\d+)\.(\d+)\.(\d+)$') {
            $releases += [pscustomobject]@{
                Tag     = "v$($matches[1]).$($matches[2]).$($matches[3])"
                Version = [version]"$($matches[1]).$($matches[2]).$($matches[3])"
            }
        }
    }
    $setupContent = Read-Text $setupPath
    $pinRegex = [regex]'(mouser-lookup\s*@\s*git\+https://github\.com/SXSLYDA/mouser_shared\.git)(@v(\d+\.\d+\.\d+))?'
    $pinMatch = $pinRegex.Match($setupContent)
    if ($releases.Count -eq 0) {
        Write-Warning "No vX.Y.Z tags found on mouser_shared - pin left as is."
    } elseif (-not $pinMatch.Success) {
        Write-Warning "No mouser-lookup @ ...mouser_shared.git line found in setup.py - pin not checked."
    } else {
        $latest = ($releases | Sort-Object Version -Descending)[0]
        if ($pinMatch.Groups[3].Success -and ([version]$pinMatch.Groups[3].Value -ge $latest.Version)) {
            Write-Host "mouser-lookup is already pinned to the newest release ($($pinMatch.Groups[2].Value.TrimStart('@')))."
        } else {
            $currentPin = if ($pinMatch.Groups[2].Success) { $pinMatch.Groups[2].Value.TrimStart('@') } else { "(unpinned)" }
            Write-Text $setupPath ($pinRegex.Replace($setupContent, "`${1}@$($latest.Tag)", 1))
            $mouserPinChange = "$currentPin -> $($latest.Tag)"
            Write-Host "mouser-lookup pin updated in setup.py: $mouserPinChange" -ForegroundColor Green
        }
    }
}

# ------------------------------------------------------------------ 4. checks
Step "Checking the Python code"
$python = Find-Python
if (-not $python) {
    Write-Host "No Python 3 found - skipping the compile check (CI runs it on the tag)." -ForegroundColor Yellow
} else {
    & $python -m compileall -q omg_import_plugin setup.py tools
    if ($LASTEXITCODE -ne 0) { Fail "a Python file doesn't compile (above)." }
    Write-Host "All Python files compile." -ForegroundColor Green
}

# ------------------------------------------------------------------ 5. bump
Step "Bumping"
$changelog = Read-Text $changelogPath
$nl = if ($changelog.Contains("`r`n")) { "`r`n" } else { "`n" }
$heading = [regex]::Match($changelog, '(?m)^## Unreleased[ \t]*\r?$')
if (-not $heading.Success) {
    Fail "CHANGELOG.md has no '## Unreleased' heading. Add one at the top with this release's notes under it."
}
$bodyStart = $heading.Index + $heading.Length
$next = [regex]::Match($changelog.Substring($bodyStart), '(?m)^## ')
$bodyEnd = if ($next.Success) { $bodyStart + $next.Index } else { $changelog.Length }
$body = $changelog.Substring($bodyStart, $bodyEnd - $bodyStart).Trim()
if ($mouserPinChange) {
    $body = ($body + "$nl- mouser-lookup (mouser_shared) $mouserPinChange.").Trim()
}
if (-not $body) {
    Write-Host "There's nothing under '## Unreleased' in CHANGELOG.md." -ForegroundColor Yellow
    if (-not $Yes) {
        $answer = Read-Host "Release $tag without changelog notes? (y/N)"
        if ($answer -notmatch '^[Yy]') { Fail "add this release's notes under '## Unreleased' in CHANGELOG.md, then run again." }
    }
    $body = "- No notes."
}
$date = Get-Date -Format 'yyyy-MM-dd'
$released = "## Unreleased$nl$nl## $newVersion $emDash $date$nl$nl$body$nl$nl"
Write-Text $changelogPath ($changelog.Substring(0, $heading.Index) + $released + $changelog.Substring($bodyEnd))

$versionText = [regex]::Replace($versionText, 'OMG_IMPORT_PLUGIN_VERSION\s*=\s*[''"]\d+\.\d+\.\d+[''"]', "OMG_IMPORT_PLUGIN_VERSION = `"$newVersion`"")
Write-Text $versionPath $versionText
Write-Host "Version bumped: $oldVersion -> $newVersion; CHANGELOG.md stamped '$newVersion $emDash $date'."

# ------------------------------------------------------------------ 6. commit and tag
Step "Committing"
git add -A
git status --short | Out-Host
git commit -q -m "Release $tag"
if ($LASTEXITCODE -ne 0) { Fail "git commit failed (above). Is your git user.name / user.email set?" }
git tag -a $tag -m "Release $tag"
if ($LASTEXITCODE -ne 0) { Fail "couldn't create tag $tag." }

Write-Host ""
Write-Host "Released $tag (committed and tagged, NOT pushed)." -ForegroundColor Green
Write-Host ""
Write-Host "1. Push the commit AND the tag:"
Write-Host "     PyCharm:  Git -> Push (Ctrl+Shift+K), tick 'Push tags' (bottom-left), then Push"
Write-Host "     Terminal: git push --follow-tags"
Write-Host "   Check the tag reached GitHub (prints a line ending in refs/tags/$tag):"
Write-Host "     git ls-remote --tags origin $tag"
Write-Host ""
Write-Host "2. GitHub Actions (CI) then checks the tag, publishes the release and updates"
Write-Host "   InvenTree to it. To do it by hand instead, on the server:"
Write-Host "     sudo inventree-omg-update $tag"
Write-Host "   (which moves plugins.txt to:  inventree-omg-harness-import @ git+$repoUrl@$tag )"
Write-Host ""
Write-Host "To undo before pushing:  git tag -d $tag; git reset --soft HEAD~1"
Pop-Location
