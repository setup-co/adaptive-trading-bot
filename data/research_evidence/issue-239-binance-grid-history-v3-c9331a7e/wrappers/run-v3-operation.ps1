param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('qualify-v3', 'verify-v3', 'report-v3')]
    [string]$Operation
)

$ErrorActionPreference = 'Stop'
$checkout = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\work\issue-239-grid-data'
$python = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-216-ci-env\Scripts\python.exe'
$dataset = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-239-run\dataset'
$records = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-239-run\v3-records'
$script = Join-Path $checkout 'scripts\research\binance_grid_history.py'
$stdout = Join-Path $records ($Operation + '.stdout.log')
$stderr = Join-Path $records ($Operation + '.stderr.log')
$startRecord = Join-Path $records ($Operation + '-start.json')
$exitRecord = Join-Path $records ($Operation + '-exit.json')

New-Item -ItemType Directory -Path $records -Force | Out-Null
if ((Test-Path -LiteralPath $startRecord) -or (Test-Path -LiteralPath $exitRecord)) {
    throw "operation record already exists: $Operation"
}

$started = [DateTimeOffset]::UtcNow
$command = @($python, $script, $Operation, '--repo', $checkout, '--root', $dataset)
$start = [ordered]@{
    schema = 'issue-239-v3-operation-start-v1'
    operation = $Operation
    started_at = $started.ToString('o')
    command = $command
    checkout = $checkout
    executable_sha = 'c9331a7e284a9ec876629d999a6100474d4b85ee'
    executable_tree = 'ec826cb266c727675237a2ba2ab31b0e155c8a11'
    plan_sha256 = 'ceeca32259aa172fef6c192da110e03aa7b0a1543a21b66872e7c0e8910e7ace'
    raw_inventory_sha256 = 'e5fd2b4fed801993308b39878ca7ab9373ee14bd84f0785e0b262209d54948b0'
}
$start | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $startRecord -Encoding utf8NoBOM

$priorPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = Join-Path $checkout 'src'
try {
    $process = Start-Process -FilePath $python -ArgumentList @(
        $script, $Operation, '--repo', $checkout, '--root', $dataset
    ) -WorkingDirectory $checkout -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
    $peakWorkingSet = [int64]0
    do {
        $all = @(Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId)
        $owned = [System.Collections.Generic.HashSet[int]]::new()
        [void]$owned.Add([int]$process.Id)
        $changed = $true
        while ($changed) {
            $changed = $false
            foreach ($row in $all) {
                if ($owned.Contains([int]$row.ParentProcessId) -and -not $owned.Contains([int]$row.ProcessId)) {
                    [void]$owned.Add([int]$row.ProcessId)
                    $changed = $true
                }
            }
        }
        $workingSet = [int64]0
        foreach ($pidValue in $owned) {
            $observed = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
            if ($null -ne $observed) {
                $workingSet += [int64]$observed.WorkingSet64
            }
        }
        if ($workingSet -gt $peakWorkingSet) { $peakWorkingSet = $workingSet }
        Start-Sleep -Milliseconds 250
        $process.Refresh()
    } while (-not $process.HasExited)
    $process.WaitForExit()
    $finished = [DateTimeOffset]::UtcNow
    $exit = [ordered]@{
        schema = 'issue-239-v3-operation-exit-v1'
        operation = $Operation
        started_at = $started.ToString('o')
        finished_at = $finished.ToString('o')
        elapsed_seconds = [math]::Round(($finished - $started).TotalSeconds, 3)
        exit_code = [int]$process.ExitCode
        peak_process_tree_working_set_bytes = $peakWorkingSet
        stdout_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $stdout).Hash.ToLowerInvariant()
        stderr_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $stderr).Hash.ToLowerInvariant()
    }
    $exit | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $exitRecord -Encoding utf8NoBOM
    exit $process.ExitCode
}
finally {
    $env:PYTHONPATH = $priorPythonPath
}
