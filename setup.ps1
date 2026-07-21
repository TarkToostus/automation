# Tark Automation - one-shot onboarding installer (Windows PowerShell).
#
# Installs Claude Code (native, no WSL/Node), downloads tark_cli, verifies Python,
# and configures your PAT. Run it with:
#
#   irm https://raw.githubusercontent.com/TarkToostus/automation/main/setup.ps1 | iex
#
# Safe to re-run.

$ErrorActionPreference = 'Continue'
$Raw  = 'https://raw.githubusercontent.com/TarkToostus/automation/main'
$Dest = if ($env:TARK_HOME) { $env:TARK_HOME } else { Join-Path $HOME 'tark-automation' }

function Say($m)  { Write-Host "`n> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[WARNING] $m" -ForegroundColor Yellow }

# 1. Claude Code (native Windows installer - no WSL, no Node)
Say 'Checking Claude Code'
if (Get-Command claude -ErrorAction SilentlyContinue) {
  Ok 'Claude Code already installed'
} else {
  Say 'Installing Claude Code (native Windows - no WSL, no Node)'
  try { irm https://claude.ai/install.ps1 | iex; Ok 'Claude Code installed' }
  catch { Warn 'Install failed. Use https://claude.ai/code in a browser instead (Path A).' }
}

# 2. Download automation files (Invoke-WebRequest, no git)
Say "Downloading tark_cli into $Dest"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Dest '.claude\commands') | Out-Null
foreach ($f in @('tark_cli.py','sales_followup.py','README.md','ONBOARDING.md')) {
  try { iwr "$Raw/$f" -OutFile (Join-Path $Dest $f) -UseBasicParsing; Ok "downloaded $f" }
  catch { Warn "could not download $f" }
}
try { iwr "$Raw/.claude/commands/research-customer.md" -OutFile (Join-Path $Dest '.claude\commands\research-customer.md') -UseBasicParsing; Ok 'bundled /research-customer skill' } catch { Warn 'skill not bundled' }
try { iwr "$Raw/.claude/CLAUDE.md" -OutFile (Join-Path $Dest 'CLAUDE.md') -UseBasicParsing } catch {}

# 3. Python 3 (stdlib only - no pip install)
Say 'Checking Python 3'
if (Get-Command python -ErrorAction SilentlyContinue) {
  Ok ("Python present (" + (python --version 2>&1) + ")")
} else {
  Warn 'Python 3 not found. Install it (double-click, no terminal):'
  Write-Host '    winget install Python.Python.3.12'
  Write-Host '  or  https://www.python.org/downloads/windows/'
  Write-Host '  Then re-run this script. (Or use Path A - claude.ai/code - which has Python built in.)'
}

# 4. Configure PAT + URL
Say 'Configuring your Tark connection'
if (Get-Command python -ErrorAction SilentlyContinue) {
  $Url = Read-Host 'Tark deployment URL (e.g. https://your-deployment.example.com)'
  # Mask the PAT as it's typed so it never shows on screen / in scrollback.
  $PatSecure = Read-Host 'Your PAT (tark_pat_..., hidden as you type)' -AsSecureString
  $Pat = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($PatSecure))
  if ($Url -and $Pat) {
    python (Join-Path $Dest 'tark_cli.py') config set url $Url | Out-Null
    python (Join-Path $Dest 'tark_cli.py') config set pat $Pat | Out-Null
    Ok 'saved config'
    Say 'Verifying connection'
    python (Join-Path $Dest 'tark_cli.py') tasks *> $null
    if ($LASTEXITCODE -eq 0) { Ok 'Connected - tark_cli can reach your deployment.' }
    else { Warn 'Could not reach the API. Check URL/PAT and re-run: python tark_cli.py config' }
  } else { Warn 'Skipped (blank input). Configure later with tark_cli config set ...' }
}

Say 'Done'
Write-Host @"

Next:
  1. cd $Dest
  2. claude                 # open Claude Code here
  3. In Claude, try:
       /research-customer https://www.hekotek.com
       Create a Tark lead for that company.

Full guide: $Dest\ONBOARDING.md
"@
