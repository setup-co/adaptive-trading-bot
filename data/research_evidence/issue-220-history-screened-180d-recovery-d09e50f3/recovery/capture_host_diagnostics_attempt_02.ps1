$ErrorActionPreference = 'Stop'
$Root = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-220-run\recovery-01'
$Ledger = 'C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-220-run\ledgers\ec10ca5d-01e6-436f-8153-fe9c5b5439b5'
$Destination = Join-Path $Root 'host-diagnostics-attempt-02.json'
if (Test-Path -LiteralPath $Destination) { throw 'corrected host diagnostics already exist' }
$Start = [datetime]'2026-10-29T20:02:00Z'
$End = [datetime]'2026-10-29T20:22:00Z'
$Events = @()
foreach ($Log in @('System', 'Application')) {
    try {
        $Events += Get-WinEvent -FilterHashtable @{
            LogName = $Log
            StartTime = $Start.ToLocalTime()
            EndTime = $End.ToLocalTime()
        } -ErrorAction Stop | Where-Object {
            $_.ProviderName -match 'Resource-Exhaustion|Kernel-Power|Application Error|Windows Error Reporting|Application Hang|WER-SystemErrorReporting'
        } | Select-Object TimeCreated, LogName, ProviderName, Id, LevelDisplayName, Message
    } catch {}
}
$Os = Get-CimInstance Win32_OperatingSystem
$Memory = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
$Disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
$Gzip = @()
Get-ChildItem -LiteralPath $Ledger -File | Where-Object {
    $_.Name -match '^\d{2}\.(population\.json\.gz|results\.jsonl\.gz)$'
} | Sort-Object Name | ForEach-Object {
    $Bytes = [System.IO.File]::ReadAllBytes($_.FullName)
    $Gzip += [ordered]@{
        name = $_.Name
        compressed_bytes = $_.Length
        gzip_uncompressed_isize = [BitConverter]::ToUInt32($Bytes, $Bytes.Length - 4)
    }
}
$ResultRows = @($Gzip | Where-Object { $_.name -like '*.results.jsonl.gz' })
$Record = [ordered]@{
    observed_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
    supersedes_measurement = 'host-diagnostics.json: gzip selector matched zero files'
    termination_window_utc = @($Start.ToString('o'), $End.ToString('o'))
    matching_termination_events = $Events
    termination_cause = if ($Events.Count) { 'EVENTS_RETAINED_FOR_REVIEW' } else { 'UNAVAILABLE_NO_MATCHING_EVENT' }
    memory = [ordered]@{
        total_physical_bytes = [int64]$Os.TotalVisibleMemorySize * 1024
        free_physical_bytes = [int64]$Os.FreePhysicalMemory * 1024
        total_virtual_bytes = [int64]$Os.TotalVirtualMemorySize * 1024
        free_virtual_bytes = [int64]$Os.FreeVirtualMemory * 1024
        committed_bytes = [int64]$Memory.CommittedBytes
        commit_limit_bytes = [int64]$Memory.CommitLimit
        available_bytes = [int64]$Memory.AvailableBytes
    }
    disk = [ordered]@{ size_bytes = [int64]$Disk.Size; free_bytes = [int64]$Disk.FreeSpace }
    gzip_inputs = $Gzip
    gzip_file_count = $Gzip.Count
    gzip_compressed_total = [int64](($Gzip | Measure-Object compressed_bytes -Sum).Sum)
    gzip_uncompressed_isize_total = [int64](($Gzip | Measure-Object gzip_uncompressed_isize -Sum).Sum)
    results_uncompressed_isize_total = [int64](($ResultRows | Measure-Object gzip_uncompressed_isize -Sum).Sum)
}
$Payload = $Record | ConvertTo-Json -Compress -Depth 10
[System.IO.File]::WriteAllText($Destination, $Payload, [System.Text.UTF8Encoding]::new($false))
