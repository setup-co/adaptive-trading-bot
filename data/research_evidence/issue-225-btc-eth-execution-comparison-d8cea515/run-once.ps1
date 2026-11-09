$ErrorActionPreference = 'Stop'

$RunRoot = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-225-run'
$Repo = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\work\issue-225-btc-eth-execution-comparison'
$Python = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-216-ci-env\Scripts\python.exe'
$Output = Join-Path $RunRoot 'output'
$RunRecord = Join-Path $RunRoot 'run-record.json'
$Stdout = Join-Path $RunRoot 'run.stdout.log'
$Stderr = Join-Path $RunRoot 'run.stderr.log'
$ExitRecord = Join-Path $RunRoot 'run-exit.json'
$StartRecord = Join-Path $RunRoot 'run-start.json'

if (Test-Path -LiteralPath $ExitRecord) { throw 'run exit already exists' }
if (Test-Path -LiteralPath $StartRecord) { throw 'run invocation already exists' }
if ((Test-Path -LiteralPath $Output) -and (Get-ChildItem -LiteralPath $Output -Force)) {
    throw 'output already exists'
}

$started = [DateTimeOffset]::UtcNow
@{
    command = "$Python scripts/research/btc_eth_retained_execution_comparison.py --repo $Repo --output $Output --run-record $RunRecord"
    started_at = $started.ToString('o')
    run_uuid = 'e3aac506-9704-4afe-8bec-d22795835f0c'
} | ConvertTo-Json -Compress | Set-Content -LiteralPath $StartRecord -Encoding utf8NoBOM

$env:PYTHONPATH = Join-Path $Repo 'src'
$arguments = @(
    (Join-Path $Repo 'scripts\research\btc_eth_retained_execution_comparison.py'),
    '--repo', $Repo,
    '--output', $Output,
    '--run-record', $RunRecord
)
$process = Start-Process -FilePath $Python -ArgumentList $arguments -WorkingDirectory $Repo `
    -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -WindowStyle Hidden -PassThru
$reason = 'NORMAL_EXIT'
while (-not $process.HasExited) {
    $process.Refresh()
    $elapsed = ([DateTimeOffset]::UtcNow - $started).TotalSeconds
    if ($elapsed -ge 3600) {
        $reason = 'INTERRUPTED_BUDGET_LIMIT'
        Stop-Process -Id $process.Id -Force
        break
    }
    if ($process.WorkingSet64 -gt 1GB) {
        $reason = 'INTERRUPTED_RESOURCE_LIMIT'
        Stop-Process -Id $process.Id -Force
        break
    }
    Start-Sleep -Seconds 2
}
$process.WaitForExit()
$ended = [DateTimeOffset]::UtcNow
@{
    ended_at = $ended.ToString('o')
    elapsed_seconds = [decimal]($ended - $started).TotalSeconds
    exit_code = $process.ExitCode
    reason = $reason
    peak_working_set_bytes = $process.PeakWorkingSet64
    run_uuid = 'e3aac506-9704-4afe-8bec-d22795835f0c'
} | ConvertTo-Json -Compress | Set-Content -LiteralPath $ExitRecord -Encoding utf8NoBOM
exit $process.ExitCode
