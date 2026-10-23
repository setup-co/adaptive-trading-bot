$ErrorActionPreference = "Stop"

$python = "C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-202-run\venv\Scripts\python.exe"
$repository = "C:\Users\elect\Documents\Codex\2026-09-09\x20\work\issue-202-execution"
$grant = "C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-203-run\grant.json"
$ledgerRoot = "C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-203-run\ledgers"
$logRoot = "C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-203-run\logs"
$stdout = Join-Path $logRoot "run.stdout.log"
$stderr = Join-Path $logRoot "run.stderr.log"
$exitPath = Join-Path $logRoot "run-exit.json"
$exitTemp = Join-Path $logRoot "run-exit.json.tmp"
$startedAt = [DateTimeOffset]::UtcNow

if (Test-Path -LiteralPath $exitPath) {
    throw "Run exit record already exists; refusing duplicate launch."
}

Push-Location -LiteralPath $repository
try {
    & $python -m scripts.research.breakout_zone_comparison --repo $repository run --grant $grant --ledger-root $ledgerRoot 1> $stdout 2> $stderr
    $processExitCode = $LASTEXITCODE
}
catch {
    $processExitCode = 254
    $_ | Out-String | Add-Content -LiteralPath $stderr -Encoding utf8
}
finally {
    Pop-Location
}

$record = [ordered]@{
    schema_version = "issue-203-run-exit-v1"
    started_at = $startedAt.ToString("o")
    ended_at = [DateTimeOffset]::UtcNow.ToString("o")
    exit_code = $processExitCode
    executable_sha = "b332eb83f392819365f3b667496fb6b01a3810ef"
    run_uuid = "1bce148f-e5d0-4706-b440-85ee25d3ced2"
}
$record | ConvertTo-Json -Compress | Set-Content -LiteralPath $exitTemp -Encoding utf8NoBOM
Move-Item -LiteralPath $exitTemp -Destination $exitPath
exit $processExitCode
