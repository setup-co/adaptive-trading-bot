$ErrorActionPreference = 'Stop'

$IssueRoot = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-210-run'
$Repository = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\work\issue-208-btw-daily-probe'
$Python = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-202-run\venv\Scripts\python.exe'
$Probe = Join-Path $IssueRoot 'probe.py'
$ProcessStart = Join-Path $IssueRoot 'process-start.json'
$ProcessExit = Join-Path $IssueRoot 'process-exit.json'

if ((Test-Path -LiteralPath $ProcessStart) -or (Test-Path -LiteralPath $ProcessExit)) {
    throw 'Issue #210 foreground wrapper has already been used or is ambiguous.'
}

$startRecord = [ordered]@{
    at = [DateTimeOffset]::UtcNow.ToString('o')
    pid = $PID
    python_executable = $Python
    launcher = $Probe
    launcher_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $Probe).Hash.ToLowerInvariant()
    plan_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $IssueRoot 'plan.json')).Hash.ToLowerInvariant()
}
$startPending = "$ProcessStart.pending"
[IO.File]::WriteAllText($startPending, ($startRecord | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath $startPending -Destination $ProcessStart

$startedAt = [DateTimeOffset]::UtcNow
& $Python $Probe run --repo $Repository --root $IssueRoot 1> (Join-Path $IssueRoot 'run.stdout.log') 2> (Join-Path $IssueRoot 'run.stderr.log')
$runExitCode = $LASTEXITCODE
$endedAt = [DateTimeOffset]::UtcNow

$exitRecord = [ordered]@{
    started_at = $startedAt.ToString('o')
    ended_at = $endedAt.ToString('o')
    elapsed_seconds = [Math]::Round(($endedAt - $startedAt).TotalSeconds, 6)
    exit_code = $runExitCode
}
$exitPending = "$ProcessExit.pending"
[IO.File]::WriteAllText($exitPending, ($exitRecord | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath $exitPending -Destination $ProcessExit
exit $runExitCode
