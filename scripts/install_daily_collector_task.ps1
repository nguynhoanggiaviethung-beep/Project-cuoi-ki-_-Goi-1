$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $PSScriptRoot "run_ssi_worker.py"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$EnvFile = Join-Path $ProjectRoot ".env"
$TaskName = "Stock Liquidity Intelligence - SSI Collector"
$PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$Account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if (-not (Test-Path -LiteralPath $Python)) { throw "Không tìm thấy .venv; tạo môi trường trước: $Python" }
if (-not (Test-Path -LiteralPath $EnvFile)) { throw "Không tìm thấy .env; cần cấu hình SSI_API_KEY và SSI_API_SECRET." }
$EnvText = Get-Content -LiteralPath $EnvFile -Raw
foreach ($Variable in @("SSI_API_KEY", "SSI_API_SECRET")) {
    if ($EnvText -notmatch "(?m)^\s*$Variable\s*=\s*\S+") { throw "Thiếu $Variable trong .env. Giá trị không được in ra màn hình." }
}

$Action = New-ScheduledTaskAction -Execute $Python `
    -Argument ("-u `"{0}`"" -f $Runner) `
    -WorkingDirectory $ProjectRoot
$Triggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User $Account),
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "8:45AM")
)
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew
$Principal = New-ScheduledTaskPrincipal -UserId $Account -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Triggers `
    -Settings $Settings -Principal $Principal -Description `
    "Thu thập SSI FastConnect vào SQLite trong phiên; chạy khi đăng nhập và lúc 08:45 các ngày trong tuần." -Force | Out-Null
Write-Output "Đã đăng ký tác vụ '$TaskName' cho tài khoản hiện tại."
Write-Output "Tác vụ chạy khi đăng nhập và 08:45 thứ Hai–thứ Sáu; log tại logs\ssi_worker.log."
Write-Output "Tác vụ không lưu giá trị khóa trong Task Scheduler; worker đọc .env tại project."
