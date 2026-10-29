$ErrorActionPreference = 'Stop'

$Checkout = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\work\issue-220-execution'
$Python = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-216-ci-env\Scripts\python.exe'
$Grant = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-220-run\grant.json'
$LedgerRoot = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-220-run\ledgers'
$Logs = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-220-run\logs'
$Uuid = 'ec10ca5d-01e6-436f-8153-fe9c5b5439b5'
$RunStart = Join-Path $Logs 'run-start.json'
$Invocation = Join-Path $Logs 'run-command-invoked.json'
$RunExit = Join-Path $Logs 'run-exit.json'
$Stdout = Join-Path $Logs 'run.stdout.log'
$Stderr = Join-Path $Logs 'run.stderr.log'

function Write-AtomicJson([string]$Path, [object]$Value) {
    $Temporary = "$Path.tmp"
    $Json = $Value | ConvertTo-Json -Compress -Depth 8
    [System.IO.File]::WriteAllText(
        $Temporary,
        $Json + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $Temporary -Destination $Path
}

if (
    (Test-Path -LiteralPath $RunStart) -or
    (Test-Path -LiteralPath $Invocation) -or
    (Test-Path -LiteralPath $RunExit) -or
    (Test-Path -LiteralPath (Join-Path $LedgerRoot $Uuid))
) {
    throw 'Issue #220 run-once guard refused an existing invocation or ledger.'
}

$Started = [DateTimeOffset]::UtcNow.ToString('o')
Write-AtomicJson $RunStart ([ordered]@{
    wrapper_pid = $PID
    started_at_utc = $Started
    frozen_executable_sha = 'd09e50f3eea87a275b640ffd38c27ad8c0f8cb3e'
    run_uuid = $Uuid
    python = $Python
    checkout = $Checkout
})

$env:PYTHONPATH = Join-Path $Checkout 'src'
$ExitCode = 255
Push-Location -LiteralPath $Checkout
try {
    Write-AtomicJson $Invocation ([ordered]@{
        invocation_count = 1
        invoked_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
        wrapper_pid = $PID
        command = @(
            $Python,
            '-m',
            'scripts.research.breakout_history_screened_comparison',
            '--repo',
            $Checkout,
            'run',
            '--grant',
            $Grant,
            '--ledger-root',
            $LedgerRoot
        )
    })
    & $Python -m scripts.research.breakout_history_screened_comparison `
        --repo $Checkout run --grant $Grant --ledger-root $LedgerRoot `
        1> $Stdout 2> $Stderr
    $ExitCode = $LASTEXITCODE
}
catch {
    $Failure = $_ | Out-String
    [System.IO.File]::AppendAllText(
        $Stderr,
        $Failure,
        [System.Text.UTF8Encoding]::new($false)
    )
    $ExitCode = 254
}
finally {
    Pop-Location
    Write-AtomicJson $RunExit ([ordered]@{
        started_at_utc = $Started
        ended_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
        exit_code = $ExitCode
        wrapper_pid = $PID
        run_uuid = $Uuid
    })
}

exit $ExitCode
