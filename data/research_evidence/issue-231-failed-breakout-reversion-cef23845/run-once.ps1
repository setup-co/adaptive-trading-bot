param([switch]$SelfTest)

$ErrorActionPreference = 'Stop'
$externalRoot = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-231-run'
$checkout = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\work\issue-231-execution'
$python = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-216-ci-env\Scripts\python.exe'
$runId = '5708360d-0b81-437d-a888-6956bda16ed6'
$executableSha = 'cef2384568e8c007ad20139bbd736382f32002b7'
$authorizedCeilingSeconds = if ($SelfTest) { 1 } else { 3593 }
$terminationThresholdSeconds = if ($SelfTest) { 1 } else { 3588 }
$workingSetLimitBytes = 1GB
$logs = if ($SelfTest) { Join-Path $externalRoot 'self-test' } else { Join-Path $externalRoot 'logs' }
$output = if ($SelfTest) { Join-Path $externalRoot 'self-test-output' } else { Join-Path $externalRoot 'output' }

New-Item -ItemType Directory -Path $logs -Force | Out-Null
$ownerPath = Join-Path $logs 'owner.json'
$exitPath = Join-Path $logs 'run-exit.json'
$startPath = Join-Path $logs 'run-start.json'
$processPath = Join-Path $logs 'process.json'
$resourcePath = Join-Path $logs 'resource.jsonl'
$stdoutPath = Join-Path $logs 'stdout.log'
$stderrPath = Join-Path $logs 'stderr.log'
foreach ($path in @($ownerPath, $exitPath, $startPath, $processPath, $resourcePath, $stdoutPath, $stderrPath)) {
    if (Test-Path -LiteralPath $path) { throw "existing invocation record: $path" }
}
if (Test-Path -LiteralPath $output) { throw "output already exists: $output" }

$owner = [ordered]@{
    schema = 'issue-231-exclusive-owner-v1'
    wrapper_pid = $PID
    wrapper = $PSCommandPath
    claimed_at_utc = [DateTime]::UtcNow.ToString('o')
    self_test = [bool]$SelfTest
    run_id = if ($SelfTest) { $null } else { $runId }
    executable_sha = if ($SelfTest) { $null } else { $executableSha }
}
$ownerBytes = [Text.UTF8Encoding]::new($false).GetBytes(($owner | ConvertTo-Json -Compress))
$ownerStream = [IO.File]::Open($ownerPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
try { $ownerStream.Write($ownerBytes, 0, $ownerBytes.Length) } finally { $ownerStream.Dispose() }

$arguments = if ($SelfTest) {
    $selfTestCode = "import pathlib,time; p=pathlib.Path(r'$output'); p.mkdir(); (p/'partial.txt').write_text('partial'); time.sleep(5)"
    @('-c', $selfTestCode)
} else {
    @(
        '-m', 'scripts.research.failed_breakout_reversion_screen',
        '--repo', $checkout,
        '--output', $output,
        '--run-id', $runId,
        '--executable-sha', $executableSha
    )
}

$started = [DateTime]::UtcNow
$commandRecord = [ordered]@{
    schema = 'issue-231-owned-deadline-wrapper-v1'
    self_test = [bool]$SelfTest
    started_at_utc = $started.ToString('o')
    authorized_total_ceiling_seconds = $authorizedCeilingSeconds
    termination_threshold_seconds = $terminationThresholdSeconds
    working_set_limit_bytes = $workingSetLimitBytes
    executable = $python
    arguments = $arguments
    working_directory = $checkout
    pythonpath = (Join-Path $checkout 'src')
    run_id = if ($SelfTest) { $null } else { $runId }
    executable_sha = if ($SelfTest) { $null } else { $executableSha }
    predecessor_run_id = if ($SelfTest) { $null } else { 'd2ed2be1-6c9b-47ea-a56e-eb7938a758aa' }
    predecessor_elapsed_seconds = if ($SelfTest) { $null } else { 6.474 }
    cumulative_historical_invocations_after_launch = if ($SelfTest) { $null } else { 2 }
}
[IO.File]::WriteAllText($startPath, ($commandRecord | ConvertTo-Json -Depth 8 -Compress), [Text.UTF8Encoding]::new($false))

$psi = [Diagnostics.ProcessStartInfo]::new()
$psi.FileName = $python
$psi.WorkingDirectory = $checkout
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.EnvironmentVariables['PYTHONPATH'] = Join-Path $checkout 'src'
$psi.EnvironmentVariables['PYTHONNOUSERSITE'] = '1'
foreach ($argument in $arguments) { [void]$psi.ArgumentList.Add($argument) }

$process = [Diagnostics.Process]::new()
$process.StartInfo = $psi
if (-not $process.Start()) { throw 'failed to launch the owned Python process' }
$stdoutTask = $process.StandardOutput.ReadToEndAsync()
$stderrTask = $process.StandardError.ReadToEndAsync()
$processRecord = [ordered]@{
    pid = $process.Id
    started_at_utc = $process.StartTime.ToUniversalTime().ToString('o')
    executable = $python
    parent_pid = $PID
    wrapper = $PSCommandPath
}
[IO.File]::WriteAllText($processPath, ($processRecord | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))

function Get-OwnedDescendants([int]$RootPid) {
    $all = @(Get-CimInstance Win32_Process)
    $owned = [Collections.Generic.List[object]]::new()
    $frontier = [Collections.Generic.Queue[int]]::new()
    $frontier.Enqueue($RootPid)
    while ($frontier.Count -gt 0) {
        $parent = $frontier.Dequeue()
        foreach ($child in @($all | Where-Object { $_.ParentProcessId -eq $parent })) {
            $owned.Add($child)
            $frontier.Enqueue([int]$child.ProcessId)
        }
    }
    return @($owned)
}

$peakWorkingSet = 0L
$peakTreeWorkingSet = 0L
$timedOut = $false
$resourceExceeded = $false
while (-not $process.HasExited) {
    $elapsed = ([DateTime]::UtcNow - $started).TotalSeconds
    $process.Refresh()
    $mainWorkingSet = $process.WorkingSet64
    if ($mainWorkingSet -gt $peakWorkingSet) { $peakWorkingSet = $mainWorkingSet }
    $children = @(Get-OwnedDescendants $process.Id)
    $treeWorkingSet = $mainWorkingSet
    foreach ($child in $children) {
        $childProcess = Get-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
        if ($null -ne $childProcess) { $treeWorkingSet += $childProcess.WorkingSet64 }
    }
    if ($treeWorkingSet -gt $peakTreeWorkingSet) { $peakTreeWorkingSet = $treeWorkingSet }
    $sample = [ordered]@{
        observed_at_utc = [DateTime]::UtcNow.ToString('o')
        elapsed_seconds = [Math]::Round($elapsed, 3)
        pid = $process.Id
        main_working_set_bytes = $mainWorkingSet
        tree_working_set_bytes = $treeWorkingSet
        child_pids = @($children | ForEach-Object { $_.ProcessId })
    }
    [IO.File]::AppendAllText($resourcePath, (($sample | ConvertTo-Json -Compress) + [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
    if ($treeWorkingSet -gt $workingSetLimitBytes) {
        $resourceExceeded = $true
    } elseif ($elapsed -ge $terminationThresholdSeconds) {
        $timedOut = $true
    }
    if ($timedOut -or $resourceExceeded) {
        foreach ($child in @($children | Sort-Object ProcessId -Descending)) {
            Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        break
    }
    Start-Sleep -Milliseconds 250
}

$process.WaitForExit()
[IO.File]::WriteAllText($stdoutPath, $stdoutTask.Result, [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText($stderrPath, $stderrTask.Result, [Text.UTF8Encoding]::new($false))
$childExit = $process.ExitCode
$wrapperExit = if ($timedOut) { 124 } elseif ($resourceExceeded) { 125 } else { $childExit }
$ended = [DateTime]::UtcNow
$exitRecord = [ordered]@{
    ended_at_utc = $ended.ToString('o')
    elapsed_seconds = [Math]::Round(($ended - $started).TotalSeconds, 3)
    child_exit_code = $childExit
    wrapper_exit_code = $wrapperExit
    termination_reason = if ($timedOut) { 'TIME_LIMIT' } elseif ($resourceExceeded) { 'MEMORY_LIMIT' } else { 'CHILD_EXIT' }
    timed_out = $timedOut
    resource_exceeded = $resourceExceeded
    pid = $process.Id
    peak_main_working_set_bytes = $peakWorkingSet
    peak_tree_working_set_bytes = $peakTreeWorkingSet
}
[IO.File]::WriteAllText($exitPath, ($exitRecord | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
exit $wrapperExit
