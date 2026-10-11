# Smoke tests for the Windows launchers (PowerShell side).
# A stub .cmd plays the role of claude, so no real session is ever started.
# Run:  powershell -NoProfile -ExecutionPolicy Bypass -File tests\test_launchers.ps1
$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $here '..')).Path
$launcher = Join-Path $root 'launchers\launch-claude.ps1'
$bat = Join-Path $root 'launchers\launch-claude.bat'
$fails = 0

function Check($label, $needle, $haystack) {
    if ("$haystack" -like "*$needle*") { Write-Host "ok   $label" }
    else { Write-Host "FAIL $label`n     expected to contain: $needle`n     got: $($haystack -replace "`r?`n", ' | ')"; $script:fails++ }
}
function CheckNot($label, $needle, $haystack) {
    if ("$haystack" -like "*$needle*") { Write-Host "FAIL $label`n     should not contain: $needle`n     got: $($haystack -replace "`r?`n", ' | ')"; $script:fails++ }
    else { Write-Host "ok   $label" }
}

$work = Join-Path $env:TEMP ('ovpull-win-test-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $work | Out-Null
$stubDir = Join-Path $work 'stub'
$fakeHome = Join-Path $work 'fakehome'
New-Item -ItemType Directory -Path $stubDir, $fakeHome | Out-Null

$stub = Join-Path $stubDir 'claude.cmd'
@"
@echo off
echo ARGV:%*
echo CWD:%CD%
"@ | Set-Content -Path $stub -Encoding ASCII

$slug = $root -replace '[^A-Za-z0-9]', '-'
$sessDir = Join-Path $fakeHome ".claude\projects\$slug"

function Run-Launcher {
    param([string[]]$Extra)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'powershell.exe'
    $psi.Arguments = @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$launcher`"") + $Extra | ForEach-Object { "`"$_`"" }
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.EnvironmentVariables['CLAUDE_BIN'] = $env:CLAUDE_BIN
    $psi.EnvironmentVariables['USERPROFILE'] = $env:USERPROFILE
    $psi.EnvironmentVariables['CLAUDE_LAUNCHER_HOLD'] = '0'
    $p = [System.Diagnostics.Process]::Start($psi)
    $out = $p.StandardOutput.ReadToEnd() + $p.StandardError.ReadToEnd()
    $p.WaitForExit()
    $out
}

Write-Host '== scenario: no conversation yet =='
$env:CLAUDE_BIN = $stub
$env:USERPROFILE = $fakeHome
if (Test-Path $sessDir) { Remove-Item -Recurse -Force $sessDir }
$out = Run-Launcher @('--model', 'opus')
Check 'starts a fresh session' 'ARGV:--model opus' $out
Check 'runs in the project root' "CWD:$root" $out

Write-Host ''
Write-Host '== scenario: this folder already has a transcript =='
New-Item -ItemType Directory -Path $sessDir -Force | Out-Null
Set-Content -Path (Join-Path $sessDir 'aaaa-bbbb.jsonl') -Value '' -Encoding ASCII
$out = Run-Launcher @()
Check 'reuses the existing conversation' 'ARGV:--continue' $out
$out = Run-Launcher @('-New')
CheckNot '-New forces a fresh one' '--continue' $out

Write-Host ''
Write-Host '== scenario: -DryRun changes nothing =='
$out = Run-Launcher @('-DryRun')
Check 'reports the reused session' 'reuse:    yes (--continue)' $out
Check 'prints the command' "--continue" $out
CheckNot 'does not run claude' 'ARGV:' $out

Write-Host ''
Write-Host '== scenario: double-click entry point (launch-claude.bat) =='
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = 'cmd.exe'
$psi.Arguments = "/c `"`"$bat`" -DryRun`""
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.EnvironmentVariables['CLAUDE_BIN'] = $stub
$psi.EnvironmentVariables['USERPROFILE'] = $fakeHome
$psi.EnvironmentVariables['CLAUDE_LAUNCHER_HOLD'] = '0'
$p = [System.Diagnostics.Process]::Start($psi)
$out = $p.StandardOutput.ReadToEnd() + $p.StandardError.ReadToEnd()
$p.WaitForExit()
Check 'the .bat reaches the same script and reports' 'reuse:    yes (--continue)' $out

Write-Host ''
Write-Host '== scenario: claude missing =='
$env:CLAUDE_BIN = ''
$env:USERPROFILE = Join-Path $work 'emptyhome'
New-Item -ItemType Directory -Path $env:USERPROFILE -Force | Out-Null
$out = Run-Launcher @()
Check 'fails with an install hint' 'npm install -g @anthropic-ai/claude-code' $out

Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
Write-Host ''
if ($fails -eq 0) { Write-Host 'ALL PASS'; exit 0 } else { Write-Host "$fails FAILED"; exit 1 }
