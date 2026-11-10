param([switch]$SelfTest)

$ErrorActionPreference = 'Stop'
$externalRoot = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-229-run'
$checkout = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\work\issue-229-failed-breakout-reversion'
$python = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-216-ci-env\Scripts\python.exe'
$runId = 'd2ed2be1-6c9b-47ea-a56e-eb7938a758aa'
$executableSha = 'b25a5dc9f69e45b58713fed7971273109d9520fe'
$limitSeconds = if ($SelfTest) { 1 } else { 3595 }
$logs = if ($SelfTest) { Join-Path $externalRoot 'self-test-03' } else { Join-Path $externalRoot 'logs' }
$output = if ($SelfTest) { Join-Path $externalRoot 'self-test-output-03' } else { Join-Path $externalRoot 'output' }

New-Item -ItemType Directory -Path $logs -Force | Out-Null
$exitPath = Join-Path $logs 'run-exit.json'
$startPath = Join-Path $logs 'run-start.json'
$processPath = Join-Path $logs 'process.json'
$resourcePath = Join-Path $logs 'resource.jsonl'
$stdoutPath = Join-Path $logs 'stdout.log'
$stderrPath = Join-Path $logs 'stderr.log'
foreach ($path in @($exitPath, $startPath, $processPath, $resourcePath, $stdoutPath, $stderrPath)) {
    if (Test-Path -LiteralPath $path) { throw "existing invocation record: $path" }
}
if (Test-Path -LiteralPath $output) { throw "output already exists: $output" }

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
    schema = 'issue-229-owned-deadline-wrapper-v1'
    self_test = [bool]$SelfTest
    started_at_utc = $started.ToString('o')
    authorized_total_ceiling_seconds = if ($SelfTest) { 1 } else { 3600 }
    termination_threshold_seconds = $limitSeconds
    executable = $python
    arguments = $arguments
    working_directory = $checkout
    pythonpath = (Join-Path $checkout 'src')
    run_id = if ($SelfTest) { $null } else { $runId }
    executable_sha = if ($SelfTest) { $null } else { $executableSha }
}
[IO.File]::WriteAllText($startPath, ($commandRecord | ConvertTo-Json -Depth 8 -Compress), [Text.UTF8Encoding]::new($false))

$psi = [Diagnostics.ProcessStartInfo]::new()
$psi.FileName = $python
$psi.WorkingDirectory = $checkout
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.RedirectStandardOutput = $false
$psi.RedirectStandardError = $false
$psi.EnvironmentVariables['PYTHONPATH'] = Join-Path $checkout 'src'
$psi.EnvironmentVariables['PYTHONNOUSERSITE'] = '1'
foreach ($argument in $arguments) { [void]$psi.ArgumentList.Add($argument) }
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
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

$peakWorkingSet = 0L
$peakTreeWorkingSet = 0L
$timedOut = $false
while (-not $process.HasExited) {
    $elapsed = ([DateTime]::UtcNow - $started).TotalSeconds
    $process.Refresh()
    $mainWorkingSet = $process.WorkingSet64
    if ($mainWorkingSet -gt $peakWorkingSet) { $peakWorkingSet = $mainWorkingSet }
    $children = @(Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $process.Id })
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
    if ($elapsed -ge $limitSeconds) {
        $timedOut = $true
        foreach ($child in $children) { Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue }
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        break
    }
    Start-Sleep -Milliseconds 250
}
$process.WaitForExit()
[IO.File]::WriteAllText($stdoutPath, $stdoutTask.Result, [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText($stderrPath, $stderrTask.Result, [Text.UTF8Encoding]::new($false))
$numericExit = if ($timedOut) { 124 } else { $process.ExitCode }
$ended = [DateTime]::UtcNow
$exitRecord = [ordered]@{
    ended_at_utc = $ended.ToString('o')
    elapsed_seconds = [Math]::Round(($ended - $started).TotalSeconds, 3)
    exit_code = $numericExit
    timed_out = $timedOut
    pid = $process.Id
    peak_main_working_set_bytes = $peakWorkingSet
    peak_tree_working_set_bytes = $peakTreeWorkingSet
}
[IO.File]::WriteAllText($exitPath, ($exitRecord | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
exit $numericExit
