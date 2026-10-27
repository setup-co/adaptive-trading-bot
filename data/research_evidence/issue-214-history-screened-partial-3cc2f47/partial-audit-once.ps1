$ErrorActionPreference = 'Stop'
$runRoot = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-214-run'
$checkout = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\work\issue-214-history-screened-196d-corpus'
$python = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-202-run\venv\Scripts\python.exe'
$stdout = Join-Path $runRoot 'partial-audit.stdout.log'
$stderr = Join-Path $runRoot 'partial-audit.stderr.log'
$exitFile = Join-Path $runRoot 'partial-audit-exit.json'
if (Test-Path -LiteralPath $exitFile) { throw 'partial audit already executed' }
if ((git -C $checkout rev-parse HEAD).Trim() -ne '3cc2f475cddc0466d28edd5f11587b7e8321e17a') {
  throw 'frozen execution HEAD changed'
}
$startedAt = [DateTime]::UtcNow.ToString('o')
$process = Start-Process -FilePath $python -ArgumentList @((Join-Path $runRoot 'partial-audit.py')) -WorkingDirectory $checkout -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
$process.WaitForExit()
$process.Refresh()
$record = [ordered]@{
  schema = 'issue-214-partial-audit-exit-v1'
  started_at_utc = $startedAt
  ended_at_utc = [DateTime]::UtcNow.ToString('o')
  pid = $process.Id
  exit_code = $process.ExitCode
}
[IO.File]::WriteAllText($exitFile, ($record | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
exit $process.ExitCode
