$ErrorActionPreference = 'Continue'
$PSNativeCommandUseErrorActionPreference = $false
$runRoot = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-214-run'
$checkout = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\work\issue-214-history-screened-196d-corpus'
$python = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-202-run\venv\Scripts\python.exe'
$stdout = Join-Path $runRoot 'partial-inventory-audit.stdout.log'
$stderr = Join-Path $runRoot 'partial-inventory-audit.stderr.log'
$exitFile = Join-Path $runRoot 'partial-inventory-audit-exit.json'
if (Test-Path -LiteralPath $exitFile) { throw 'partial inventory audit already executed' }
if ((git -C $checkout rev-parse HEAD).Trim() -ne '3cc2f475cddc0466d28edd5f11587b7e8321e17a') { throw 'frozen HEAD changed' }
$startedAt = [DateTime]::UtcNow.ToString('o')
& $python (Join-Path $runRoot 'partial-inventory-audit.py') 1> $stdout 2> $stderr
$actualExit = $LASTEXITCODE
$record = [ordered]@{schema='issue-214-partial-inventory-audit-exit-v1';started_at_utc=$startedAt;ended_at_utc=[DateTime]::UtcNow.ToString('o');exit_code=$actualExit}
[IO.File]::WriteAllText($exitFile, ($record | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
exit $actualExit
