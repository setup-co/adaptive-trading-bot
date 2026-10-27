$ErrorActionPreference = 'Stop'
$runRoot = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-214-run'
$checkout = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\work\issue-214-history-screened-196d-corpus'
$python = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-202-run\venv\Scripts\python.exe'
$expectedHead = '3cc2f475cddc0466d28edd5f11587b7e8321e17a'
$stdout = Join-Path $runRoot 'capture.stdout.log'
$stderr = Join-Path $runRoot 'capture.stderr.log'
$exitFile = Join-Path $runRoot 'capture-exit.json'
$ownerFile = Join-Path $runRoot 'capture-owner.json'
if (Test-Path -LiteralPath $exitFile) { throw 'capture exit record already exists' }
if (Test-Path -LiteralPath $ownerFile) { throw 'capture owner already exists' }
if ((git -C $checkout rev-parse HEAD).Trim() -ne $expectedHead) { throw 'execution HEAD changed' }
if ((git -C $checkout status --porcelain).Count -ne 0) { throw 'execution checkout is not clean' }
$env:PYTHONPATH = Join-Path $checkout 'src'
$startedAt = [DateTime]::UtcNow.ToString('o')
$owner = [ordered]@{
  schema = 'issue-214-capture-owner-v1'
  started_at_utc = $startedAt
  launcher_pid = $PID
  checkout = $checkout
  executable_sha = $expectedHead
  interpreter = $python
  plan_sha256 = '0a9f48d8da7bb3af6054daa3cc565b93641c5e57a3a0e62dd2c954de9c101713'
}
[IO.File]::WriteAllText($ownerFile, ($owner | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
$arguments = @('-m','scripts.research.history_screened_196d_corpus','capture','--repo',$checkout)
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $checkout -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
$processStart = [ordered]@{
  schema = 'issue-214-capture-process-v1'
  started_at_utc = $startedAt
  pid = $process.Id
  interpreter = $python
  arguments = $arguments
}
[IO.File]::WriteAllText((Join-Path $runRoot 'capture-process.json'), ($processStart | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
$process.WaitForExit()
$endedAt = [DateTime]::UtcNow.ToString('o')
$exit = [ordered]@{
  schema = 'issue-214-capture-exit-v1'
  started_at_utc = $startedAt
  ended_at_utc = $endedAt
  pid = $process.Id
  exit_code = $process.ExitCode
}
[IO.File]::WriteAllText($exitFile, ($exit | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
exit $process.ExitCode
