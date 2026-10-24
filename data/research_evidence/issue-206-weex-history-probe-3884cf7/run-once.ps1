$ErrorActionPreference = 'Stop'

$python = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-202-run\venv\Scripts\python.exe'
$script = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-206-run\probe.py'
$repo = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\work\issue-206-public-probe'
$root = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-206-run'
$stdout = Join-Path $root 'run.stdout.log'
$stderr = Join-Path $root 'run.stderr.log'
$processExit = Join-Path $root 'process-exit.json'

if ((Test-Path -LiteralPath $stdout) -or
    (Test-Path -LiteralPath $stderr) -or
    (Test-Path -LiteralPath $processExit)) {
    throw 'Prior process log/exit record exists; refusing another launch.'
}

$started = [DateTimeOffset]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffffffZ')
$arguments = @($script, 'run', '--repo', $repo, '--root', $root)
& $python @arguments 1> $stdout 2> $stderr
$actualExitCode = $LASTEXITCODE
$ended = [DateTimeOffset]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffffffZ')
$record = [ordered]@{
    command = @($python) + $arguments
    ended_at = $ended
    exit_code = $actualExitCode
    started_at = $started
}
$json = $record | ConvertTo-Json -Compress
$pending = "$processExit.pending"
[IO.File]::WriteAllText($pending, $json, [Text.UTF8Encoding]::new($false))
$stream = [IO.File]::Open($pending, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
try { $stream.Flush($true) } finally { $stream.Dispose() }
[IO.File]::Move($pending, $processExit)
exit $actualExitCode
