$ErrorActionPreference = 'Stop'
$checkout = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\work\issue-239-grid-data'
$python = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-216-ci-env\Scripts\python.exe'
$dataset = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-239-run\dataset'
$records = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-239-run\v3-records'
$verification = Join-Path $records 'verify-v3-corrected.stdout.json'
$stdout = Join-Path $records 'report-v3.stdout.log'
$stderr = Join-Path $records 'report-v3.stderr.log'
$exitRecord = Join-Path $records 'report-v3-exit.json'
if ((Test-Path $stdout) -or (Test-Path $stderr) -or (Test-Path $exitRecord)) {
    throw 'v3 report was already launched'
}
$started = [DateTimeOffset]::UtcNow
$priorPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = Join-Path $checkout 'src'
try {
    $process = Start-Process -FilePath $python -ArgumentList @(
        (Join-Path $checkout 'scripts\research\binance_grid_history.py'),
        'report-v3', '--repo', $checkout, '--root', $dataset,
        '--verification-json', $verification
    ) -WorkingDirectory $checkout -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru -Wait
    $finished = [DateTimeOffset]::UtcNow
    [ordered]@{
        schema = 'issue-239-v3-report-exit-v1'
        executable_sha = '038361d5afda5e11c5bfa182252c46371555e0d7'
        started_at = $started.ToString('o')
        finished_at = $finished.ToString('o')
        elapsed_seconds = [math]::Round(($finished - $started).TotalSeconds, 3)
        exit_code = [int]$process.ExitCode
        stdout_sha256 = (Get-FileHash -Algorithm SHA256 $stdout).Hash.ToLowerInvariant()
        stderr_sha256 = (Get-FileHash -Algorithm SHA256 $stderr).Hash.ToLowerInvariant()
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $exitRecord -Encoding utf8NoBOM
    exit $process.ExitCode
}
finally {
    $env:PYTHONPATH = $priorPythonPath
}
