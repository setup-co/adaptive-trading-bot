$ErrorActionPreference = 'Stop'
$externalRoot = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-231-recovery-run'
$checkout = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\work\issue-231-failed-breakout-evidence'
$python = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-216-ci-env\Scripts\python.exe'
$exportId = '48f3d327-86b3-4210-98a3-4d22052200d8'
$exporterSha = 'f2518739f47add85355598260f0d0f72bc42c13d'
$sourceRunId = '5708360d-0b81-437d-a888-6956bda16ed6'
$authorizedCeilingSeconds = 60
$terminationThresholdSeconds = 55
$workingSetLimitBytes = 1GB
$logs = Join-Path $externalRoot 'logs'
$output = Join-Path $externalRoot 'output'

if (Test-Path -LiteralPath $logs) { throw 'report-export logs already exist' }
if (Test-Path -LiteralPath $output) { throw 'report-export output already exists' }
New-Item -ItemType Directory -Path $logs | Out-Null
$ownerPath = Join-Path $logs 'owner.json'
$exitPath = Join-Path $logs 'export-exit.json'
$startPath = Join-Path $logs 'export-start.json'
$processPath = Join-Path $logs 'process.json'
$resourcePath = Join-Path $logs 'resource.jsonl'
$stdoutPath = Join-Path $logs 'stdout.log'
$stderrPath = Join-Path $logs 'stderr.log'

$owner = [ordered]@{
    schema = 'issue-231-report-export-owner-v1'
    export_id = $exportId
    wrapper_pid = $PID
    wrapper = $PSCommandPath
    claimed_at_utc = [DateTime]::UtcNow.ToString('o')
    exporter_sha = $exporterSha
    source_run_id = $sourceRunId
}
$ownerBytes = [Text.UTF8Encoding]::new($false).GetBytes(($owner | ConvertTo-Json -Compress))
$ownerStream = [IO.File]::Open($ownerPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
try { $ownerStream.Write($ownerBytes, 0, $ownerBytes.Length) } finally { $ownerStream.Dispose() }

$arguments = @(
    '-m', 'scripts.research.failed_breakout_reversion_saved_export',
    '--repo', $checkout,
    '--output', $output,
    '--exporter-sha', $exporterSha
)
$started = [DateTime]::UtcNow
$commandRecord = [ordered]@{
    schema = 'issue-231-report-export-start-v1'
    export_id = $exportId
    started_at_utc = $started.ToString('o')
    authorized_total_ceiling_seconds = $authorizedCeilingSeconds
    termination_threshold_seconds = $terminationThresholdSeconds
    working_set_limit_bytes = $workingSetLimitBytes
    executable = $python
    arguments = $arguments
    working_directory = $checkout
    pythonpath = (Join-Path $checkout 'src')
    exporter_sha = $exporterSha
    source_run_id = $sourceRunId
    source_run_exit = 1
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
if (-not $process.Start()) { throw 'failed to launch the saved-row exporter' }
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

$peakMain = 0L
$peakTree = 0L
$timedOut = $false
$resourceExceeded = $false
while (-not $process.HasExited) {
    $elapsed = ([DateTime]::UtcNow - $started).TotalSeconds
    $process.Refresh()
    $mainWorkingSet = $process.WorkingSet64
    if ($mainWorkingSet -gt $peakMain) { $peakMain = $mainWorkingSet }
    $children = @(Get-OwnedDescendants $process.Id)
    $treeWorkingSet = $mainWorkingSet
    foreach ($child in $children) {
        $childProcess = Get-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
        if ($null -ne $childProcess) { $treeWorkingSet += $childProcess.WorkingSet64 }
    }
    if ($treeWorkingSet -gt $peakTree) { $peakTree = $treeWorkingSet }
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
    Start-Sleep -Milliseconds 100
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
    peak_main_working_set_bytes = $peakMain
    peak_tree_working_set_bytes = $peakTree
}
[IO.File]::WriteAllText($exitPath, ($exitRecord | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
exit $wrapperExit
