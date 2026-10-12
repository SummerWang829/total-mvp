# One-time: turn this project into a Git repo and make the first commit.
# Safe to re-run. Aborts if secrets/databases would be committed.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "Git is not installed. Install from https://git-scm.com/download/win and re-run." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path ".git")) { git init -b main } else { Write-Host "Already a git repo." }

if (-not (git config user.name))  { git config user.name  "Total Dev" }
if (-not (git config user.email)) { git config user.email "dev@localhost.invalid" }

git add -A
$staged = git diff --cached --name-only
$bad = $staged | Where-Object { $_ -match '(^|/)\.env$' -or $_ -match '\.db($|-|\.bak)' -or $_ -match '^(media|bin)/' -or $_ -match '\.(pem|key)$' }
if ($bad) {
    Write-Host "ABORT: these files must not be committed:" -ForegroundColor Red
    $bad | ForEach-Object { Write-Host "  $_" }
    git reset | Out-Null
    exit 1
}

$count = ($staged | Measure-Object).Count
Write-Host "Files to commit: $count"
$staged | Select-Object -First 40 | ForEach-Object { Write-Host "  $_" }

git commit -m "Initial commit: TOTAL deep-interview MVP"
Write-Host ""
Write-Host "Done. Next: create a PRIVATE remote repo, then run:" -ForegroundColor Green
Write-Host "  git remote add origin <repo-url>"
Write-Host "  git push -u origin main"
