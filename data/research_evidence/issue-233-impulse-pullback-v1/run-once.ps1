$ErrorActionPreference = "Stop"

$runRoot = "C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-233-run"
$checkout = "C:\Users\elect\Documents\Codex\2026-09-09\x20\work\issue-233-impulse-pullback"
$python = "C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-216-ci-env\Scripts\python.exe"
$runId = "9d4a3e9b-81f3-471c-b12e-c84e4b70600b"
$executable = "892a75922aef8ba62dca7c9cd3e902bbb7b98669"
$output = Join-Path $runRoot "output"
$logs = Join-Path $runRoot "logs"
$ownerPath = Join-Path $runRoot "owner.json"
$exitPath = Join-Path $runRoot "run-exit.json"
$heartbeatPath = Join-Path $logs "heartbeat.jsonl"
$stdoutPath = Join-Path $logs "run.stdout.log"
$stderrPath = Join-Path $logs "run.stderr.log"
$deadlineSeconds = 590
$memoryLimitBytes = 1GB

if (
    (Test-Path -LiteralPath $output) -or
    (Test-Path -LiteralPath $ownerPath) -or
    (Test-Path -LiteralPath $exitPath)
) {
    throw "Issue #233 run ownership or output already exists"
}

$ownerStream = [System.IO.File]::Open(
    $ownerPath,
    [System.IO.FileMode]::CreateNew,
    [System.IO.FileAccess]::Write,
    [System.IO.FileShare]::Read
)
$startedAt = [DateTimeOffset]::UtcNow
$owner = [ordered]@{
    run_id = $runId
    executable_sha = $executable
    checkout = $checkout
    interpreter = $python
    output = $output
    wrapper_pid = $PID
    started_at = $startedAt.ToString("o")
    deadline_seconds = $deadlineSeconds
    memory_limit_bytes = $memoryLimitBytes
}
$ownerBytes = [Text.Encoding]::UTF8.GetBytes(($owner | ConvertTo-Json -Compress))
$ownerStream.Write($ownerBytes, 0, $ownerBytes.Length)
$ownerStream.Flush($true)
$ownerStream.Dispose()

$env:PYTHONPATH = Join-Path $checkout "src"
$arguments = @(
    "-m",
    "scripts.research.trend_impulse_pullback_screen",
    "--repo", $checkout,
    "--output", $output,
    "--run-id", $runId,
    "--executable-sha", $executable
)
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $checkout `
    -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -NoNewWindow -PassThru

$peakWorkingSet = [int64]0
$lastHeartbeat = [DateTimeOffset]::MinValue
$status = "RUNNING"
$timedOut = $false
$resourceStopped = $false

while (-not $process.HasExited) {
    $now = [DateTimeOffset]::UtcNow
    $elapsed = ($now - $startedAt).TotalSeconds
    $process.Refresh()
    $workingSet = [int64]$process.WorkingSet64
    if ($workingSet -gt $peakWorkingSet) { $peakWorkingSet = $workingSet }
    if (($now - $lastHeartbeat).TotalSeconds -ge 30) {
        $heartbeat = [ordered]@{
            observed_at = $now.ToString("o")
            child_pid = $process.Id
            elapsed_seconds = [Math]::Round($elapsed, 3)
            working_set_bytes = $workingSet
            peak_working_set_bytes = $peakWorkingSet
            output_exists = Test-Path -LiteralPath $output
        }
        Add-Content -LiteralPath $heartbeatPath -Value ($heartbeat | ConvertTo-Json -Compress) -Encoding utf8
        $lastHeartbeat = $now
    }
    if ($workingSet -gt $memoryLimitBytes) {
        $status = "RESOURCE_LIMIT"
        $resourceStopped = $true
        Stop-Process -Id $process.Id -Force
        break
    }
    if ($elapsed -ge $deadlineSeconds) {
        $status = "TIMEOUT"
        $timedOut = $true
        Stop-Process -Id $process.Id -Force
        break
    }
    Start-Sleep -Seconds 2
}

$process.WaitForExit()
$endedAt = [DateTimeOffset]::UtcNow
$exitCode = $process.ExitCode
if (-not $timedOut -and -not $resourceStopped) {
    $status = if ($exitCode -eq 0) { "EXITED_ZERO" } else { "EXITED_NONZERO" }
}
$exit = [ordered]@{
    run_id = $runId
    executable_sha = $executable
    status = $status
    child_exit_code = $exitCode
    timed_out = $timedOut
    resource_stopped = $resourceStopped
    started_at = $startedAt.ToString("o")
    ended_at = $endedAt.ToString("o")
    elapsed_seconds = [Math]::Round(($endedAt - $startedAt).TotalSeconds, 3)
    peak_working_set_bytes = $peakWorkingSet
    output = $output
}
$pending = "$exitPath.pending"
[System.IO.File]::WriteAllText($pending, ($exit | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath $pending -Destination $exitPath
exit $(if ($status -eq "EXITED_ZERO") { 0 } else { 1 })
