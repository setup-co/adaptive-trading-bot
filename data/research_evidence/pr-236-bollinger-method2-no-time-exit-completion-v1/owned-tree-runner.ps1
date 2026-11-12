$ErrorActionPreference = "Stop"
$root = "C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-236-no-time-completion-run"
$checkout = "C:\Users\elect\Documents\Codex\2026-09-09\x20\work\issue-235-bb-method2"
$python = "C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-216-ci-env\Scripts\python.exe"
$executable = "c706baf6dc6cfb05cb46e0059bfae95adcc50ee2"
$runId = "79844d61-29fe-4f45-bb9c-236b2717892b"
$limitSeconds = 300
$memoryLimitBytes = [int64]1073741824
$stdout = Join-Path $root "run-stdout.log"
$stderr = Join-Path $root "run-stderr.log"
$record = Join-Path $root "run-exit.json"
$owner = Join-Path $root "run-owner.json"

foreach ($exclusivePath in @($record, $owner, $stdout, $stderr, (Join-Path $root "output"))) {
    if (Test-Path -LiteralPath $exclusivePath) {
        throw "exclusive selective-completion run path already exists: $exclusivePath"
    }
}

$psi = [System.Diagnostics.ProcessStartInfo]::new()
$psi.FileName = $python
$psi.WorkingDirectory = $checkout
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.Environment["PYTHONPATH"] = (Join-Path $checkout "src")
foreach ($argument in @(
    "-m", "scripts.research.bollinger_method2_no_time_exit_completion",
    "--repo", $checkout,
    "--output", (Join-Path $root "output"),
    "--run-id", $runId,
    "--executable-sha", $executable
)) {
    $psi.ArgumentList.Add($argument)
}

$startedAt = [DateTimeOffset]::UtcNow
$process = [System.Diagnostics.Process]::Start($psi)
[ordered]@{
    schema = "pr-236-bollinger-method2-no-time-completion-owner-v1"
    executable_sha = $executable
    run_id = $runId
    root_pid = $process.Id
    started_at = $startedAt.ToString("o")
    command = $psi.FileName + " " + (($psi.ArgumentList | ForEach-Object { '"' + $_ + '"' }) -join " ")
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $owner -Encoding utf8NoBOM
$stdoutTask = $process.StandardOutput.ReadToEndAsync()
$stderrTask = $process.StandardError.ReadToEndAsync()
$observed = [System.Collections.Generic.HashSet[int]]::new()
$peakWorkingSet = [int64]0
$timedOut = $false
$memoryExceeded = $false

while (-not $process.HasExited) {
    $all = @(Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId)
    $tree = [System.Collections.Generic.HashSet[int]]::new()
    [void]$tree.Add($process.Id)
    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($item in $all) {
            if ($tree.Contains([int]$item.ParentProcessId) -and -not $tree.Contains([int]$item.ProcessId)) {
                [void]$tree.Add([int]$item.ProcessId)
                $changed = $true
            }
        }
    }
    $working = [int64]0
    foreach ($pidValue in $tree) {
        [void]$observed.Add($pidValue)
        $owned = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
        if ($null -ne $owned) {
            $working += [int64]$owned.WorkingSet64
        }
    }
    if ($working -gt $peakWorkingSet) {
        $peakWorkingSet = $working
    }
    if ($working -gt $memoryLimitBytes) {
        $memoryExceeded = $true
    }
    if (([DateTimeOffset]::UtcNow - $startedAt).TotalSeconds -ge $limitSeconds) {
        $timedOut = $true
    }
    if ($timedOut -or $memoryExceeded) {
        foreach ($pidValue in @($tree | Sort-Object -Descending)) {
            Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
        }
        break
    }
    Start-Sleep -Milliseconds 100
}

$process.WaitForExit()
$stdoutTask.Result | Set-Content -LiteralPath $stdout -Encoding utf8NoBOM
$stderrTask.Result | Set-Content -LiteralPath $stderr -Encoding utf8NoBOM
$endedAt = [DateTimeOffset]::UtcNow
$exitCode = if ($timedOut) { 124 } elseif ($memoryExceeded) { 137 } else { $process.ExitCode }
[ordered]@{
    schema = "pr-236-bollinger-method2-no-time-completion-exit-v1"
    executable_sha = $executable
    run_id = $runId
    started_at = $startedAt.ToString("o")
    ended_at = $endedAt.ToString("o")
    elapsed_seconds = ($endedAt - $startedAt).TotalSeconds
    limit_seconds = $limitSeconds
    memory_limit_bytes = $memoryLimitBytes
    root_pid = $process.Id
    observed_process_ids = @($observed | Sort-Object)
    observed_process_count = $observed.Count
    peak_tree_working_set_bytes = $peakWorkingSet
    timed_out = $timedOut
    memory_exceeded = $memoryExceeded
    worker_exit_code = if ($process.HasExited) { $process.ExitCode } else { $null }
    exit_code = $exitCode
    stdout_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $stdout).Hash.ToLowerInvariant()
    stderr_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $stderr).Hash.ToLowerInvariant()
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $record -Encoding utf8NoBOM
exit $exitCode
