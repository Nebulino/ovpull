# Whoever clones this repo on Windows: run this once and `ovpull` works from
# anywhere (a .cmd shim in %USERPROFILE%\.local\bin).
#
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
#   .\install.ps1 --bin-dir C:\Tools\bin
#   .\install.ps1 --uninstall
#
# Double-click route: install.bat.
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here 'ovpull.py'

# find an interpreter that really answers --version before trusting it
$runner = $null
$prefix = @()
foreach ($entry in @(@('py', '-3'), @('python', ''), @('python3', ''))) {
    $found = Get-Command $entry[0] -ErrorAction SilentlyContinue
    if (-not $found) { continue }
    $pre = @($entry[1]) | Where-Object { $_ }
    try {
        $probe = & $found.Source @pre '--version' 2>&1
        if ("$probe" -match 'Python') { $runner = $found.Source; $prefix = $pre; break }
    } catch { }
}

if (-not $runner) {
    Write-Host 'No working Python found.' -ForegroundColor Red
    Write-Host '  install:  winget install --id Python.Python.3.12 -e'
    Write-Host '  or uv:    winget install --id astral-sh.uv -e'
    exit 1
}

Write-Host ("installing with {0}" -f (@($runner) + $prefix -join ' '))
& $runner @prefix $script --install @Rest
exit $LASTEXITCODE
