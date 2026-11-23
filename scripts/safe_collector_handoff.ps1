$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$log = Join-Path $root 'logs\collector-safe-handoff.log'
$base = 'http://127.0.0.1:8000'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $log) | Out-Null
function Write-Log([string]$Message) { Add-Content -LiteralPath $log -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message" -Encoding UTF8 }
function Get-Status { Invoke-RestMethod -Uri "$base/api/status" -TimeoutSec 10 }
function Get-LatestRun { (Invoke-RestMethod -Uri "$base/api/collector/runs?limit=1" -TimeoutSec 10).runs | Select-Object -First 1 }
$initial = Get-LatestRun
if (-not $initial -or $null -eq $initial.id) { Write-Log 'STOP: không xác định được run hiện tại.'; exit 2 }
$runId = [int]$initial.id
$workerPid = [int]$initial.process_id
Write-Log "Theo dõi run=$runId pid=$workerPid; không dừng trước khi queue và ghi DB ổn định."
$stable = 0
$lastReport = [DateTime]::MinValue
while ($stable -lt 3) {
    try {
        $s = Get-Status
        $run = Get-LatestRun
        if ($run -and [int]$run.id -ne $runId -and $null -eq $run.ended_at) { Write-Log "STOP: phát hiện run mới $($run.id), không tác động."; exit 3 }
        $q = [int]$s.queue_depth; $t = [int]$s.trade_queue_depth
        $qs = [string]$s.quote_persistence_status; $ts = [string]$s.trade_persistence_status
        $safe = ($q -eq 0 -and $t -eq 0 -and $qs -in @('saved','saved_invalid_only','duplicate_only','idle') -and $ts -in @('saved','saved_invalid_only','duplicate_only','idle') -and -not [bool]$s.status_db_busy -and [int]$s.queue_dropped -eq 0 -and [int]$s.trade_messages_dropped -eq 0)
        if ($safe) { $stable++ } else { $stable = 0 }
        if (((Get-Date)-$lastReport).TotalSeconds -ge 60 -or $safe) {
            Write-Log "run=$runId quote_queue=$q trade_queue=$t quote_state=$qs trade_state=$ts stable=$stable/3 db_busy=$($s.status_db_busy) persisted=$($s.last_persisted_at)"
            $lastReport = Get-Date
        }
    } catch { $stable = 0; if (((Get-Date)-$lastReport).TotalSeconds -ge 60) { Write-Log "status read error: $($_.Exception.Message)"; $lastReport=Get-Date } }
    if ($stable -lt 3) { Start-Sleep -Seconds 10 }
}
Write-Log 'Queue đã rỗng và writer ổn định 3 lần; gửi yêu cầu dừng graceful.'
$stopped = $false
for ($i=0; $i -lt 6 -and -not $stopped; $i++) {
    try { $r=Invoke-RestMethod -Method Post -Uri "$base/api/collector/stop" -TimeoutSec 20; $stopped=[bool]$r.ok; Write-Log "stop request ok=$stopped $($r.message)" } catch { Write-Log "stop request retry: $($_.Exception.Message)"; Start-Sleep -Seconds 5 }
}
if (-not $stopped) { Write-Log 'STOP: không gửi được lệnh dừng; giữ collector, không khởi chạy bản mới.'; exit 4 }
$deadline=(Get-Date).AddMinutes(10)
while ((Get-Date) -lt $deadline) {
    try {
        $run=Get-LatestRun
        $alive=$false
        try { Get-Process -Id $workerPid -ErrorAction Stop | Out-Null; $alive=$true } catch { }
        if ($run -and [int]$run.id -eq $runId -and $run.ended_at -and -not $alive) { break }
    } catch { }
    Start-Sleep -Seconds 3
}
if (-not $run -or [int]$run.id -ne $runId -or -not $run.ended_at -or $alive) { Write-Log 'STOP: worker cũ chưa xác nhận kết thúc trong 10 phút; không khởi chạy worker trùng.'; exit 5 }
Write-Log "Run $runId đã kết thúc và PID $workerPid đã thoát; khởi chạy mã collector hiện tại."
try { $start=Invoke-RestMethod -Method Post -Uri "$base/api/collector/start" -TimeoutSec 30; Write-Log "start ok=$($start.ok) pid=$($start.worker_process_id) message=$($start.message)"; if (-not $start.ok) { exit 6 } } catch { Write-Log "START ERROR: $($_.Exception.Message)"; exit 6 }
$newPid=[int]$start.worker_process_id
$verifyUntil=(Get-Date).AddMinutes(3)
$confirmed=$false
while ((Get-Date) -lt $verifyUntil) {
    try {
        $s=Get-Status; $run=Get-LatestRun
        if ($run -and [int]$run.process_id -eq $newPid -and $null -eq $run.ended_at -and $s.subscription_status -eq 'confirmed' -and $s.worker_process_alive) { $confirmed=$true; Write-Log "VERIFIED pid=$newPid run=$($run.id) subscription=$($s.subscription_status) socket=$($s.connection_status) feed=$($s.feed_state); realtime event/database write must be confirmed during next market session."; break }
    } catch { }
    Start-Sleep -Seconds 5
}
if (-not $confirmed) { Write-Log "NOT VERIFIED pid=${newPid}: worker may be running, but subscription/heartbeat not confirmed; inspect logs and status."; exit 7 }