# install.ps1
#
# Fetches the upstream packages this project builds on, each pinned to the
# exact commit the modifications in overlay/ were written against, then copies
# those modifications into place.
#
# The upstream packages are the work of Jared Rhodenizer and are not
# redistributed here. This script clones them from his repositories. See
# README.md, Credits.
#
# Usage, from the repository root:
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# Re-running is safe. A package already present is left alone and only the
# overlay is reapplied. Pass -Force to delete and re-clone everything.

[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

# Upstream repositories, pinned. These hashes are the commits the files in
# overlay/ were derived from. Moving a pin forward without re-deriving the
# overlay is how you get a silently broken merge.
$Packages = [ordered]@{
    'backtalk'        = @{ Url = 'https://github.com/jaredrhod/backtalk';        Commit = '17353c66b21ef31b5e7d990153e81c717d80771d' }
    'ai-visualizer'   = @{ Url = 'https://github.com/jaredrhod/ai-visualizer';   Commit = 'dd109df74fda8a14ee93828fc2313b9045a9f1b4' }
    'barehands'       = @{ Url = 'https://github.com/jaredrhod/barehands';       Commit = 'bdd8df505b290287dc5483844eb43d61fa1b74af' }
    'fullstack-agent' = @{ Url = 'https://github.com/jaredrhod/fullstack-agent'; Commit = 'd3c7374c89680628f38e1e63aa9d64a0a7170f7b' }
    'ai-memory-vault' = @{ Url = 'https://github.com/jaredrhod/ai-memory-vault'; Commit = 'd16ba5a6c705456c0f53692f3be0819c2cfe98b3' }
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'git is not on PATH. Install Git for Windows, then run this again.'
}

foreach ($name in $Packages.Keys) {
    $url    = $Packages[$name].Url
    $commit = $Packages[$name].Commit

    if ($Force -and (Test-Path -LiteralPath $name)) {
        Write-Host "  $name : removing existing checkout (-Force)"
        Remove-Item -LiteralPath $name -Recurse -Force
    }

    if (Test-Path -LiteralPath $name) {
        Write-Host "  $name : already present, leaving it alone"
    }
    else {
        Write-Host "  $name : cloning"
        git clone --quiet $url $name
        if ($LASTEXITCODE -ne 0) { throw "clone failed for $name" }

        Write-Host "  $name : pinning to $($commit.Substring(0,10))"
        git -C $name -c advice.detachedHead=false checkout --quiet $commit
        if ($LASTEXITCODE -ne 0) { throw "checkout failed for $name" }
    }
}

# Apply the modified files. Each one carries Jared Rhodenizer's copyright
# notice and the AGPL header from the file it was derived from.
Write-Host ''
Write-Host 'Applying overlay:'

$overlayRoot = Join-Path $PSScriptRoot 'overlay'
if (-not (Test-Path -LiteralPath $overlayRoot)) {
    throw 'overlay/ is missing. Run this from the repository root.'
}

Get-ChildItem -LiteralPath $overlayRoot -Recurse -File | ForEach-Object {
    $relative = $_.FullName.Substring($overlayRoot.Length + 1)
    $target   = Join-Path $PSScriptRoot $relative
    $parent   = Split-Path -Parent $target

    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }

    Copy-Item -LiteralPath $_.FullName -Destination $target -Force
    Write-Host "  $relative"
}

Write-Host ''
Write-Host 'Upstream fetched and overlay applied.'
Write-Host ''
Write-Host 'Next:'
Write-Host '  1. cd backtalk; uv sync'
Write-Host '  2. cd face-widget; python -m venv .venv; .venv\Scripts\pip install -r requirements.txt'
Write-Host '  3. Copy each file in config-examples\ to the component that reads it and edit it.'
Write-Host '  4. Edit CLAUDE.md and replace the {OWNER_*} placeholders.'
Write-Host ''
Write-Host 'See README.md for the full walkthrough.'
