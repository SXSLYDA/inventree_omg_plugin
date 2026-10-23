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
# Your release, start to finish:
#   1. .\build-and-bump.ps1   - builds, bumps, stamps the changelog
#   2. commit in PyCharm      - the release commit is TAGGED for you (git hook below)
#   3. Push with "Push tags" ticked (All)  - the tag is what releases
#
# Safe to run again: before you've committed it re-uses the version it already
# bumped (it rebuilds, it doesn't bump twice); after you've committed but not
# pushed, or with nothing changed since the last release, it stops and says so.
#
# What it does, in order (it stops at the first problem):
#   1. Checks: git (and npm unless -SkipBuild), version.py and CHANGELOG.md exist,
#      the new tag doesn't already exist here or on GitHub.
#   2. Builds all five panels and merges their Vite manifests (see the note at
#      $mergedManifest for why).
#   3. Checks every .py file compiles (needs Python 3 on PATH or your venv active).
#   4. Bumps omg_import_plugin/version.py (setup.py reads it from there) and turns
#      "## Unreleased" in CHANGELOG.md into "## X.Y.Z - date".
#   5. Installs the release-tag git hook (tools/git-hooks/post-commit): when you
#      commit a version.py change, that commit is tagged vX.Y.Z automatically.
#   6. Shows what changed and what to do next.
# It does NOT commit or push - review the changes in PyCharm first, commit
# (the hook tags it), then push the commit AND the tag.
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

$versionRegex = 'OMG_IMPORT_PLUGIN_VERSION\s*=\s*[''"](\d+)\.(\d+)\.(\d+)[''"]'
$versionText = Read-Text $versionPath
$match = [regex]::Match($versionText, $versionRegex)
if (-not $match.Success) { Fail "couldn't find OMG_IMPORT_PLUGIN_VERSION = `"X.Y.Z`" in version.py." }
$major = [int]$match.Groups[1].Value
$minor = [int]$match.Groups[2].Value
$patch = [int]$match.Groups[3].Value
$oldVersion = "$major.$minor.$patch"

# The version in your last commit - to tell "bumped, not committed yet" (re-use it)
# and "committed but not pushed" / "nothing new" (stop) from a fresh release.
$headText = (git show HEAD:omg_import_plugin/version.py 2>$null) -join "`n"
$headMatch = [regex]::Match($headText, $versionRegex)
$headVersion = if ($headMatch.Success) { "$($headMatch.Groups[1].Value).$($headMatch.Groups[2].Value).$($headMatch.Groups[3].Value)" } else { $null }
$alreadyBumped = $false
$changelogNow = Read-Text $changelogPath

if (-not $Version -and $headVersion -and $oldVersion -ne $headVersion) {
    # version.py already moved on since the last commit: this run's own bump, not committed yet
    if ($changelogNow -match "(?m)^## $([regex]::Escape($oldVersion)) ") {
        $alreadyBumped = $true
        Write-Host "Already bumped to $oldVersion (not committed yet) - rebuilding, not bumping again." -ForegroundColor Yellow
    } else {
        Fail "version.py says $oldVersion but your last commit says $headVersion, and CHANGELOG.md has no $oldVersion section. Put version.py back (git checkout -- omg_import_plugin/version.py) or run with -Version $oldVersion."
    }
} elseif (-not $Version -and $headVersion) {
    $headTag = "v$headVersion"
    $headTagOnGitHub = git ls-remote --tags origin "refs/tags/$headTag" 2>$null
    $gitHubReached = ($LASTEXITCODE -eq 0)
    if (-not (git tag --list $headTag) -and -not $headTagOnGitHub -and $gitHubReached) {
        # The release commit is in, but it was never tagged (committed without the
        # hook): tag the commit that set this version, rather than bumping again.
        $releaseCommit = (git log -n 1 --format=%H -- omg_import_plugin/version.py 2>$null)
        if ($releaseCommit) {
            git tag -a $headTag $releaseCommit -m "Release $headTag" | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Write-Host ""
                Write-Host "$headVersion is committed but was never tagged - tagged $headTag on $($releaseCommit.Substring(0, 7)) now." -ForegroundColor Green
                Write-Host "Push it: PyCharm Push with 'Push tags' ticked, or:  git push origin master $headTag"
                Write-Host "Run this script again after that for the next release."
                Pop-Location
                exit 0
            }
        }
    }
    if (git tag --list $headTag) {
        $headTagRemote = git ls-remote --tags origin "refs/tags/$headTag" 2>$null
        if ($LASTEXITCODE -eq 0 -and -not $headTagRemote) {
            Fail "$headTag is committed and tagged but not on GitHub yet - push it (PyCharm: Push with 'Push tags' ticked, or: git push origin master $headTag). Nothing to bump."
        }
        $sinceTag = git rev-list "$headTag..HEAD" 2>$null
        $uncommitted = git status --porcelain 2>$null
        if (-not $sinceTag -and -not $uncommitted) {
            Fail "nothing has changed since $headTag - no new release to make."
        }
    }
}

if ($alreadyBumped) {
    $newVersion = $oldVersion
} elseif ($Version) {
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
if (-not $isNewer -and -not $alreadyBumped) { Fail "$newVersion isn't newer than the current $oldVersion." }
$tag = "v$newVersion"

$branch = (git rev-parse --abbrev-ref HEAD 2>$null)
Write-Host "Branch: $branch    Version: $oldVersion -> $newVersion    Tag: $tag"
if ($branch -ne 'master' -and $branch -ne 'main') {
    Write-Host "Not on master - commit and tag on '$branch', then merge it into master." -ForegroundColor Yellow
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

# ------------------------------------------------------------------ 3. checks
Step "Checking the Python code"
$python = Find-Python
if (-not $python) {
    Write-Host "No Python 3 found - skipping the compile check (CI runs it on the tag)." -ForegroundColor Yellow
} else {
    & $python -m compileall -q omg_import_plugin setup.py tools
    if ($LASTEXITCODE -ne 0) { Fail "a Python file doesn't compile (above)." }
    Write-Host "All Python files compile." -ForegroundColor Green
}

# ------------------------------------------------------------------ 4. bump
Step "Bumping"
if ($alreadyBumped) {
    Write-Host "version.py and CHANGELOG.md already say $newVersion - left as they are."
} else {
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
}

# ------------------------------------------------------------------ 5. release-tag hook
# .git/hooks isn't part of the repo, so it's (re)installed on every run: the
# commit that changes version.py gets tagged vX.Y.Z (tools/git-hooks/post-commit).
Step "Release-tag git hook"
$hookSource = Join-Path $root 'tools\git-hooks\post-commit'
$hooksDir = (git rev-parse --git-path hooks 2>$null)
if (-not (Test-Path $hookSource) -or -not $hooksDir) {
    Write-Warning "Couldn't install the release-tag hook - tag the release commit yourself: git tag -a $tag -m `"Release $tag`""
} else {
    if (-not [System.IO.Path]::IsPathRooted($hooksDir)) { $hooksDir = Join-Path $root $hooksDir }
    New-Item -ItemType Directory -Force -Path $hooksDir | Out-Null
    $hookPath = Join-Path $hooksDir 'post-commit'
    $hookText = (Read-Text $hookSource) -replace "`r", ""     # sh needs LF line endings
    $existing = if (Test-Path $hookPath) { Read-Text $hookPath } else { $null }
    if ($existing -and -not $existing.Contains('OMG plugin release tag')) {
        Write-Warning "$hookPath is someone else's hook - left alone. Tag the release commit yourself: git tag -a $tag -m `"Release $tag`""
    } else {
        Write-Text $hookPath $hookText
        if ($IsLinux -or $IsMacOS) { chmod +x $hookPath }   # Git for Windows runs hooks without it
        Write-Host "Installed: committing this release tags it $tag."
    }
}

# ------------------------------------------------------------------ 6. what changed
Step "Ready to commit"
# Each build makes NEW hashed panel files (PartSetupPanel-<hash>.js) and removes
# the old ones. New files aren't tracked yet, so PyCharm's commit window leaves
# them out (under "Unversioned Files") - and a release without them breaks the
# panels (the manifest names a file that isn't there). Stage the whole static/
# folder, additions and removals, so they always go into the commit.
if (-not $SkipBuild) {
    git add -A -- omg_import_plugin/static
    Write-Host "Staged the built panel files (omg_import_plugin/static) - new hashed files included."
}
$manifestFile = Join-Path $staticPath ".vite\manifest.json"
if (Test-Path $manifestFile) {
    $listed = (Get-Content $manifestFile -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $_.Value.file }
    $absent = @($listed | Where-Object { -not (Test-Path (Join-Path $staticPath $_)) })
    if ($absent.Count) { Fail "manifest.json names panel files that aren't there: $($absent -join ', ') - run without -SkipBuild." }
}
git status --short | Out-Host

Write-Host ""
Write-Host "Ready for $tag - nothing committed or pushed." -ForegroundColor Green
Write-Host ""
Write-Host "1. Look over the changes above, then commit them (PyCharm: Commit, Alt+0 - or:"
Write-Host "     git add -A; git commit -m `"Release $tag`" )"
Write-Host "   The commit is tagged $tag for you (the release-tag hook prints 'tagged this commit $tag')."
Write-Host ""
Write-Host "2. Push the commit AND the tag - the tag is what releases:"
Write-Host "     PyCharm:  Git -> Push (Ctrl+Shift+K), tick 'Push tags' (All), then Push"
Write-Host "     Terminal: git push origin master $tag"
Write-Host "   (PyCharm's push window lists commits only - the tag is sent without being shown.)"
Write-Host "   Check it reached GitHub (prints a line ending in refs/tags/$tag):"
Write-Host "     git ls-remote --tags origin $tag"
Write-Host ""
Write-Host "3. On the server:  sudo inventree-omg-update"
Write-Host "   (installs the newest release tag - moves plugins.txt to ...inventree_omg_plugin.git@$tag )"
Write-Host ""
Write-Host "Running this again before you commit is fine - it keeps $newVersion and only rebuilds."
Write-Host "To start again from scratch before committing:  git checkout -- omg_import_plugin/version.py CHANGELOG.md"
Pop-Location
