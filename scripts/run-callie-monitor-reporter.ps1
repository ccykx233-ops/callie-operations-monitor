[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('workbench','order')]
    [string]$Component,
    [string]$InstallRoot = 'C:\ProgramData\Callie\monitor\current',
    [string]$EnvFile = 'C:\ProgramData\Callie\secrets\monitor-local-reporter.env',
    [string]$StateRoot = 'C:\ProgramData\Callie\state',
    [string]$Python = 'C:\Callie\candidate\Callie-Workbench-stability-20261105-v2\apps\workbench\backend\.venv\Scripts\python.exe'
)

$ErrorActionPreference = 'Stop'
$mutex = [Threading.Mutex]::new($false, "Global\CallieMonitorReporter-$Component")
$owned = $false
$started = Get-Date
$statusPath = Join-Path $StateRoot "monitor-$Component-reporter.status.json"

function Write-AtomicStatus($payload) {
    [IO.Directory]::CreateDirectory($StateRoot) | Out-Null
    $temporary = $statusPath + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    [IO.File]::WriteAllText($temporary, ($payload | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
    if ([IO.File]::Exists($statusPath)) {
        $backup = $statusPath + '.replace-backup'
        [IO.File]::Replace($temporary, $statusPath, $backup)
        if ([IO.File]::Exists($backup)) { [IO.File]::Delete($backup) }
    } else {
        [IO.File]::Move($temporary, $statusPath)
    }
}

try {
    try { $owned = $mutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { $owned = $true }
    if (-not $owned) { exit 0 }
    if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) { throw 'MONITOR_REPORTER_ENV_MISSING' }
    foreach ($line in [IO.File]::ReadAllLines($EnvFile)) {
        $value = $line.Trim()
        if (-not $value -or $value.StartsWith('#')) { continue }
        $index = $value.IndexOf('=')
        if ($index -lt 1) { continue }
        [Environment]::SetEnvironmentVariable($value.Substring(0, $index).Trim(), $value.Substring($index + 1).Trim(), 'Process')
    }
    $script = Join-Path $InstallRoot 'callie_monitor_local_reporter.py'
    if (-not (Test-Path -LiteralPath $script -PathType Leaf)) { throw 'MONITOR_REPORTER_SCRIPT_MISSING' }
    & $Python -X utf8 $script $Component | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'MONITOR_REPORTER_FAILED' }
    Write-AtomicStatus ([ordered]@{component=$Component;status='ok';started_at=$started.ToString('o');finished_at=(Get-Date).ToString('o')})
    exit 0
} catch {
    Write-AtomicStatus ([ordered]@{
        component=$Component;status='error';started_at=$started.ToString('o');finished_at=(Get-Date).ToString('o')
        error_type=$_.Exception.GetType().Name
        error_code=if ([string]$_.Exception.Message -match '^[A-Z][A-Z0-9_]{0,79}$') {[string]$_.Exception.Message}else{'MONITOR_REPORTER_UNEXPECTED'}
    })
    exit 1
} finally {
    if ($owned) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
