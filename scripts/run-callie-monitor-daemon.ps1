[CmdletBinding()]
param(
    [string]$Wrapper = 'C:\ProgramData\Callie\monitor\current\run-callie-monitor-reporter.ps1',
    [string]$StateFile = 'C:\ProgramData\Callie\state\monitor-local-daemon.status.json'
)

$ErrorActionPreference = 'Stop'
$mutex = [Threading.Mutex]::new($false, 'Global\CallieMonitorLocalDaemonV1')
$owned = $false

function Write-State($value) {
    $temporary = $StateFile + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    [IO.File]::WriteAllText($temporary, ($value | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
    if ([IO.File]::Exists($StateFile)) {
        $backup = $StateFile + '.replace-backup'
        [IO.File]::Replace($temporary, $StateFile, $backup)
        if ([IO.File]::Exists($backup)) { [IO.File]::Delete($backup) }
    } else {
        [IO.File]::Move($temporary, $StateFile)
    }
}

try {
    try { $owned = $mutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { $owned = $true }
    if (-not $owned) { exit 0 }
    if (-not (Test-Path -LiteralPath $Wrapper -PathType Leaf)) { throw 'MONITOR_WRAPPER_MISSING' }
    $now = Get-Date
    $currentBucket = $now.ToString('yyyy-MM-ddTHH')
    $state = [ordered]@{
        version=1;status='starting';pid=$PID;started_at=$now.ToString('o');heartbeat_at=$now.ToString('o')
        last_workbench_bucket=$currentBucket;last_order_bucket=$currentBucket
        last_workbench_attempt_at=$null;last_order_attempt_at=$null
        last_workbench_result='not_due';last_order_result='not_due'
    }
    if (Test-Path -LiteralPath $StateFile -PathType Leaf) {
        try {
            $previous = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
            foreach ($name in @('last_workbench_bucket','last_order_bucket','last_workbench_attempt_at','last_order_attempt_at','last_workbench_result','last_order_result')) {
                if ($null -ne $previous.$name) { $state[$name] = $previous.$name }
            }
        } catch {}
    }
    while ($true) {
        $now = Get-Date
        $bucket = $now.ToString('yyyy-MM-ddTHH')
        foreach ($item in @(@{Component='workbench';Minute=2},@{Component='order';Minute=4})) {
            $bucketKey='last_'+$item.Component+'_bucket'; $attemptKey='last_'+$item.Component+'_attempt_at'; $resultKey='last_'+$item.Component+'_result'
            $lastAttempt=if($state[$attemptKey]){[DateTimeOffset]::Parse([string]$state[$attemptKey])}else{$null}
            $retryReady=$null -eq $lastAttempt -or ([DateTimeOffset]::Now-$lastAttempt).TotalMinutes -ge 5
            if($now.Minute -ge $item.Minute -and $state[$bucketKey] -ne $bucket -and $retryReady){
                $state[$attemptKey]=$now.ToString('o')
                & $Wrapper -Component $item.Component
                if($LASTEXITCODE -eq 0){$state[$bucketKey]=$bucket;$state[$resultKey]='ok'}else{$state[$resultKey]='error'}
            }
        }
        $state.status='running';$state.pid=$PID;$state.heartbeat_at=(Get-Date).ToString('o')
        Write-State $state
        Start-Sleep -Seconds 60
    }
} finally {
    if ($owned) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
