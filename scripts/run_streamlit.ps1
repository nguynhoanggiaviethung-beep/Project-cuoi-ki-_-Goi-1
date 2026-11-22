$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$ApiUrl = "http://127.0.0.1:8000/api/status"
$UiUrl = "http://127.0.0.1:8501/"
$Logs = Join-Path $ProjectRoot "logs"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Không tìm thấy .venv. Tạo môi trường và cài requirements-streamlit.txt theo README trước."
}
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

$apiReady = $false
try {
    $null = Invoke-RestMethod -Uri $ApiUrl -TimeoutSec 8
    $apiReady = $true
} catch {
    $listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        throw "Cổng 8000 đã có tiến trình nhưng API không phản hồi. Không dừng tiến trình để tránh làm gián đoạn collector; kiểm tra backend trước rồi chạy lại."
    }
}

if (-not $apiReady) {
    $backend = Start-Process -FilePath $Python -ArgumentList @("app.py") -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $Logs "fastapi-streamlit.out.log") -RedirectStandardError (Join-Path $Logs "fastapi-streamlit.err.log") -PassThru
    $deadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 1
        try {
            $null = Invoke-RestMethod -Uri $ApiUrl -TimeoutSec 3
            $apiReady = $true
            break
        } catch {
            if ($backend.HasExited) {
                Get-Content (Join-Path $Logs "fastapi-streamlit.err.log") -Tail 30 -ErrorAction SilentlyContinue
                throw "FastAPI thoát ngay khi khởi động. Xem logs\fastapi-streamlit.err.log."
            }
        }
    }
    if (-not $apiReady) { throw "FastAPI chưa sẵn sàng sau 45 giây. Không khởi chạy thêm tiến trình nào." }
}

$uiListener = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue
if ($uiListener) {
    try {
        $null = Invoke-WebRequest -Uri "http://127.0.0.1:8501/_stcore/health" -TimeoutSec 4
        Start-Process $UiUrl
        Write-Host "Streamlit đã chạy; mở $UiUrl"
        return
    } catch {
        throw "Cổng 8501 đang bị chiếm bởi tiến trình không phải Streamlit khỏe mạnh. Không dừng tiến trình đó."
    }
}

Write-Host "Streamlit là giao diện chính tại $UiUrl. FastAPI backend đang chạy ở cổng 8000."
Write-Host "Dừng terminal này chỉ dừng giao diện Streamlit; backend/collector vẫn tiếp tục chạy."
& $Python -m streamlit run (Join-Path $ProjectRoot "streamlit_app.py") --server.address 127.0.0.1 --server.port 8501 --server.headless false


