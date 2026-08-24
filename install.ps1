<#
  install.ps1 — put the skills where the agent will find them, then prove the
  toolchain works. Idempotent: re-running overwrites the skills and nothing else.

    powershell -ExecutionPolicy Bypass -File install.ps1
    powershell -ExecutionPolicy Bypass -File install.ps1 -Target "$HOME\.agents\skills"
    powershell -ExecutionPolicy Bypass -File install.ps1 -SkipVerify
#>
param(
    [string]$Target = "$HOME\.claude\skills",
    [switch]$SkipVerify
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "telegram-stack -> $Target" -ForegroundColor Cyan

if (-not (Test-Path $Target)) { New-Item -ItemType Directory -Force -Path $Target | Out-Null }

$skills = Get-ChildItem -Directory (Join-Path $root 'skills')
foreach ($s in $skills) {
    $dest = Join-Path $Target $s.Name
    if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
    Copy-Item -Recurse $s.FullName $dest
    Write-Host "  installed $($s.Name)"
}

# The scripts are referenced from the skills by relative path, so they travel with
# the repo rather than into the skills directory. Record where they live.
$marker = Join-Path $Target 'telegram\STACK_ROOT'
Set-Content -Path $marker -Value $root -Encoding utf8
Write-Host "  scripts stay at $root (recorded in skills\telegram\STACK_ROOT)"

if ($SkipVerify) { Write-Host "`nskipped verification"; exit 0 }

$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { Write-Host "`npython not on PATH - skipping verification" -ForegroundColor Yellow; exit 0 }

Write-Host "`nverifying the toolchain (offline)" -ForegroundColor Cyan
$env:PYTHONIOENCODING = 'utf-8'
$tmp = Join-Path $env:TEMP ("tgstack-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$checks = @(
    @{ Name = 'initdata selftest'; Args = @("$root\scripts\validate_initdata.py", '--selftest') },
    @{ Name = 'text builders';     Args = @("$root\scripts\tg_text.py", '--demo') },
    @{ Name = 'contract validator';Args = @("$root\scripts\gen_navigation.py", "$root\templates\navigation.example.yaml", '--check') },
    @{ Name = 'glyph generator';   Args = @("$root\scripts\make_process_assets.py", '--out', $tmp) },
    @{ Name = 'asset validator';   Args = @("$root\scripts\validate_sticker_assets.py", $tmp, '--kind', 'custom_emoji') }
)
$failed = 0
foreach ($c in $checks) {
    $null = & python @($c.Args) 2>&1
    if ($LASTEXITCODE -eq 0) { Write-Host "  PASS $($c.Name)" -ForegroundColor Green }
    else { Write-Host "  FAIL $($c.Name)" -ForegroundColor Red; $failed++ }
}
if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }

if ($failed -gt 0) {
    Write-Host "`n$failed check(s) failed. 'pip install pyyaml' covers the usual cause." -ForegroundColor Red
    exit 1
}
Write-Host "`nready. Start with the 'telegram' skill; workflows\WORKFLOW.md is the process." -ForegroundColor Green
