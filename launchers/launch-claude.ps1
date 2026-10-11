# launch-claude.ps1 — open a Claude Code session for this project and keep that
# same conversation every time you come back to it.
#
#   .\launch-claude.ps1                    # continue this folder's last session (or start one)
#   .\launch-claude.ps1 -New               # start a fresh conversation
#   .\launch-claude.ps1 -DryRun            # show what would run, launch nothing
#   .\launch-claude.ps1 -ClaudeArgs '--model','opus'   # extra args passed to claude
#
# Double-click: launch-claude.bat (same script, cmd window stays open at the end).
# Env: CLAUDE_BIN (path to the CLI), CLAUDE_LAUNCHER_HOLD=0 to skip the pause.
[CmdletBinding()]
param(
    [switch]$New,
    [switch]$DryRun,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ClaudeArgs
)

$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $here '..')).Path      # project root = parent of .\launchers
Set-Location $root

# ---- find the CLI ---------------------------------------------------------
$bin = $env:CLAUDE_BIN
if (-not $bin) {
    $found = Get-Command claude -ErrorAction SilentlyContinue
    if ($found) { $bin = $found.Source }
}
if (-not $bin) {
    $candidates = @(
        (Join-Path $env:USERPROFILE '.local\bin\claude.exe'),
        (Join-Path $env:APPDATA 'npm\claude.cmd'),
        (Join-Path $env:USERPROFILE 'AppData\Roaming\npm\claude.cmd'),
        (Join-Path $env:USERPROFILE '.local\bin\claude')
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) { $bin = $candidate; break }
    }
}
if (-not $bin) {
    Write-Host 'claude not found.' -ForegroundColor Red
    Write-Host '  install:  npm install -g @anthropic-ai/claude-code'
    Write-Host '  or set CLAUDE_BIN to the binary.'
    if ($env:CLAUDE_LAUNCHER_HOLD -ne '0') { Read-Host 'Press Enter to close' | Out-Null }
    exit 1
}

# ---- does this folder already have a conversation? -----------------------
# Claude Code keeps transcripts in %USERPROFILE%\.claude\projects\<cwd, non-alphanumerics -> '-'>
$slug = $root -replace '[^A-Za-z0-9]', '-'
$sessDir = Join-Path $env:USERPROFILE ".claude\projects\$slug"
$sessions = 0
if (Test-Path $sessDir) {
    $sessions = @(Get-ChildItem -Path $sessDir -Filter '*.jsonl' -File -ErrorAction SilentlyContinue).Count
}

$cliArgs = @()
$reuse = $false
if (-not $New -and $sessions -gt 0) { $cliArgs += '--continue'; $reuse = $true }
if ($ClaudeArgs) { $cliArgs += $ClaudeArgs }

if ($DryRun) {
    Write-Host ("project:  {0}" -f $root)
    Write-Host ("claude:   {0}" -f $bin)
    Write-Host ("sessions: {0} transcript(s) in {1}" -f $sessions, $sessDir)
    Write-Host ("reuse:    {0}" -f $(if ($reuse) { 'yes (--continue)' } else { 'no (fresh session)' }))
    Write-Host ("command:  {0} {1}" -f $bin, ($cliArgs -join ' '))
    exit 0
}

Write-Host ("-> {0} {1}" -f $bin, ($cliArgs -join ' ')) -ForegroundColor Cyan
& $bin @cliArgs

# keep the console open only when Windows launched us by double-click
if ($env:CLAUDE_LAUNCHER_HOLD -ne '0') {
    $parentName = $null
    try {
        $parentId = (Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction Stop).ParentProcessId
        $parentName = (Get-CimInstance Win32_Process -Filter "ProcessId=$parentId" -ErrorAction Stop).Name
    } catch { $parentName = $null }
    if ($parentName -eq 'explorer.exe') {
        Read-Host 'Claude session closed. Press Enter to close this window' | Out-Null
    }
}
