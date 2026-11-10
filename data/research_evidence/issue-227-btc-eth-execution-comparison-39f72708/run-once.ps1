$ErrorActionPreference = 'Stop'

$RunRoot = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-227-run'
$Repo = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\work\issue-227-btc-eth-execution'
$Python = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-216-ci-env\Scripts\python.exe'
$Output = Join-Path $RunRoot 'output'
$RunRecord = Join-Path $RunRoot 'run-record.json'
$Stdout = Join-Path $RunRoot 'run.stdout.log'
$Stderr = Join-Path $RunRoot 'run.stderr.log'
$ExitRecord = Join-Path $RunRoot 'run-exit.json'
$StartRecord = Join-Path $RunRoot 'run-start.json'
$OwnerRecord = Join-Path $RunRoot 'owner.json'
$RunUuid = 'd9cbd3a7-ccfd-4f6a-9606-1aecee1b3cf3'
$ExecutableSha = '39f72708b6d022f5f3a18e3ee2d993a3a6873cad'
$ExecutableTree = 'a4274099f0fef61b3cc82a7f667c503bf258c949'
$DriverSha256 = '9b34afaffd2e5a435c55f8465b7b9d18a37486a0b8c22b9264596c8b0c4019ae'
$BudgetSeconds = 3597
$WorkingSetLimit = 1GB
$OutputLimit = 20MB

if (Test-Path -LiteralPath $ExitRecord) { throw 'run exit already exists' }
if (Test-Path -LiteralPath $StartRecord) { throw 'run invocation already exists' }
if (Test-Path -LiteralPath $OwnerRecord) { throw 'run owner already exists' }
if ((Test-Path -LiteralPath $Output) -and (Get-ChildItem -LiteralPath $Output -Force)) {
    throw 'output already exists'
}
if ((git -C $Repo rev-parse HEAD) -ne $ExecutableSha) { throw 'checkout SHA changed' }
if ((git -C $Repo rev-parse 'HEAD^{tree}') -ne $ExecutableTree) { throw 'checkout tree changed' }
if (git -C $Repo status --porcelain) { throw 'checkout is not clean' }
if ((Get-FileHash (Join-Path $Repo 'scripts\research\btc_eth_retained_execution_comparison.py') -Algorithm SHA256).Hash.ToLower() -ne $DriverSha256) {
    throw 'driver bytes changed'
}

$record = Get-Content -LiteralPath $RunRecord -Raw | ConvertFrom-Json
$wrapperSha = (Get-FileHash $PSCommandPath -Algorithm SHA256).Hash.ToLower()
if (
    $record.protocol -ne 'BTC_ETH_RETAINED_DECISION_EXECUTION_COMPARISON_V1' -or
    $record.model_sha -ne '90101c22d9465ba2fb41eedb3164ac0285c8b9e3' -or
    $record.executable_sha -ne $ExecutableSha -or
    $record.driver_commit -ne $ExecutableSha -or
    $record.run_uuid -ne $RunUuid -or
    $record.wrapper_sha256 -ne $wrapperSha
) { throw 'run record identity mismatch' }

$command = "$Python scripts/research/btc_eth_retained_execution_comparison.py --repo $Repo --output $Output --run-record $RunRecord"
$started = [DateTimeOffset]::UtcNow
$clock = [System.Diagnostics.Stopwatch]::StartNew()
@{
    command = $command
    started_at = $started.ToString('o')
    run_uuid = $RunUuid
    executable_sha = $ExecutableSha
    external_budget_seconds = $BudgetSeconds
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
@{
    wrapper_pid = $PID
    child_pid = $process.Id
    child_executable = $Python
    command = $command
    created_at = $started.ToString('o')
    run_uuid = $RunUuid
} | ConvertTo-Json -Compress | Set-Content -LiteralPath $OwnerRecord -Encoding utf8NoBOM

$reason = 'NORMAL_EXIT'
$peakWorkingSet = [int64]0
$lastOutputBytes = [int64]0
while (-not $process.HasExited) {
    $process.Refresh()
    $peakWorkingSet = [Math]::Max($peakWorkingSet, [int64]$process.WorkingSet64)
    if (Test-Path -LiteralPath $Output) {
        $lastOutputBytes = [int64]((Get-ChildItem -LiteralPath $Output -File -Recurse |
            Measure-Object -Property Length -Sum).Sum)
    }
    if ($clock.Elapsed.TotalSeconds -ge $BudgetSeconds) {
        $reason = 'INTERRUPTED_BUDGET_LIMIT'
        Stop-Process -Id $process.Id -Force
        break
    }
    if ($process.WorkingSet64 -gt $WorkingSetLimit) {
        $reason = 'INTERRUPTED_RESOURCE_LIMIT'
        Stop-Process -Id $process.Id -Force
        break
    }
    if ($lastOutputBytes -gt $OutputLimit) {
        $reason = 'INTERRUPTED_OUTPUT_LIMIT'
        Stop-Process -Id $process.Id -Force
        break
    }
    Start-Sleep -Seconds 1
}
$process.WaitForExit()
$clock.Stop()
$process.Refresh()
$peakWorkingSet = [Math]::Max($peakWorkingSet, [int64]$process.PeakWorkingSet64)
if (Test-Path -LiteralPath $Output) {
    $lastOutputBytes = [int64]((Get-ChildItem -LiteralPath $Output -File -Recurse |
        Measure-Object -Property Length -Sum).Sum)
}
$ended = [DateTimeOffset]::UtcNow
@{
    ended_at = $ended.ToString('o')
    elapsed_seconds = [decimal]$clock.Elapsed.TotalSeconds
    exit_code = $process.ExitCode
    reason = $reason
    peak_working_set_bytes = $peakWorkingSet
    output_bytes = $lastOutputBytes
    run_uuid = $RunUuid
    executable_sha = $ExecutableSha
} | ConvertTo-Json -Compress | Set-Content -LiteralPath $ExitRecord -Encoding utf8NoBOM
exit $process.ExitCode
