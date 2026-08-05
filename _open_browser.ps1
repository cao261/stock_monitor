# ====================== A-Stock Sentiment Monitor · 浏览器自动启动器 ======================
# 单独写一个 .ps1 文件的原因：
#   - 写在 start.bat 里的 `start /min powershell -Command "..."` 多层引号 + 续行
#     在中文 Windows 上经常被 cmd 转义吃掉，窗口根本起不来
#   - 用 -File 调用 .ps1 没有任何字符串解析歧义，最稳
#   - 日志写到 _browser_launcher.log，双击出错也能看到原因
#
# 用法（start.bat 里）：
#   start "browser-launcher" /min powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_open_browser.ps1"
# =====================================================================================
$ErrorActionPreference = 'Stop'

# 路径：用 $PSScriptRoot（脚本所在目录）而不是 PWD——start /min 启动时 PWD 可能不是这里
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$LogFile   = Join-Path $ScriptDir '_browser_launcher.log'

function Write-Log {
    param([string]$Msg)
    $ts = Get-Date -Format 'HH:mm:ss.fff'
    $line = "[$ts] $Msg"
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    # 同时打到当前窗口（虽然被 /min 隐藏，但万一用户手动跑能看到）
    Write-Host $line
}

# 清掉旧日志（每次新启动一次）
if (Test-Path $LogFile) { Remove-Item $LogFile -Force -ErrorAction SilentlyContinue }

$Port = 8000
$Host_ = '127.0.0.1'
$Url  = "http://${Host_}:${Port}/"

try {
    Write-Log "browser launcher started, waiting for ${Url}"

    # ===== 快速 TCP 端口探测（每次 1.5s 超时，绝不被 .NET 默认 21-120s 拖死） =====
    # 强制 IPv4：默认 TcpClient 解析 '127.0.0.1' 时偶发会先尝试 IPv6 ::1 然后回退 IPv4，
    # 那个回退延迟在 Windows 上能拖到 1-2s，跟我们的 1s timeout 几乎一样，导致偶发探测失败
    function Test-PortReachable {
        param([string]$HostName, [int]$Port, [int]$TimeoutMs = 1500)
        try {
            $ip = [Net.Dns]::GetHostAddresses($HostName) |
                  Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
                  Select-Object -First 1
            if (-not $ip) { return $false }
            $client = New-Object Net.Sockets.TcpClient
            $task = $client.ConnectAsync($ip, $Port)
            $ok = $task.Wait($TimeoutMs) -and $client.Connected
            $client.Close()
            return $ok
        } catch {
            return $false
        }
    }

    $Ready = $false
    $Attempts = 0
    $MaxAttempts = 20         # 20 × ~1.7s ≈ 34s 上限
    $StartedAt = Get-Date
    while ($Attempts -lt $MaxAttempts) {
        if (Test-PortReachable -HostName $Host_ -Port $Port -TimeoutMs 1500) {
            $Ready = $true
            $ms = [int]((Get-Date) - $StartedAt).TotalMilliseconds
            Write-Log "port $Port is open after ${ms}ms ($Attempts attempts)"
            break
        }
        $Attempts++
        Start-Sleep -Milliseconds 200
    }

    if (-not $Ready) {
        $elapsed = [int]((Get-Date) - $StartedAt).TotalSeconds
        Write-Log "ERROR: port $Port did not open within ${elapsed}s. Aborting (you can open ${Url} manually)."
        exit 1
    }

    # 端口通了再等 800ms——uvicorn 端口绑定成功 ≠ HTTP server 完整就绪
    Start-Sleep -Milliseconds 800

    Write-Log "launching default browser: $Url"
    Start-Process $Url -ErrorAction Stop
    Write-Log "browser launch command sent. Done."
    exit 0
} catch {
    Write-Log "FATAL: $($_.Exception.GetType().Name): $($_.Exception.Message)"
    exit 2
}
