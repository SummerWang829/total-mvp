# Windows 一键启动：先加载新装的 Python / Git / ffmpeg 路径，再启动本地转写服务（whisper.cpp），最后运行 scripts/dev.sh
# 洞察/翻译用的 Ollama 装好后常驻在托盘里，这里不用管。
$env:Path = 'C:\Program Files\Git\bin;C:\Program Files\Git\usr\bin;' + [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
$env:NO_PROXY = 'localhost,127.0.0.1,::1'  # 系统开了代理时，访问本机网站/视频服务器/转写服务不要走代理
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location $PSScriptRoot

$whisper = Join-Path $PSScriptRoot 'bin\whisper\Release\whisper-server.exe'
$model = Join-Path $PSScriptRoot 'bin\whisper\ggml-large-v3-turbo-q5_0.bin'
$ws = $null
if ((Test-Path $whisper) -and (Test-Path $model)) {
    $ws = Start-Process $whisper -WindowStyle Hidden -PassThru -ArgumentList @(
        '-m', "`"$model`"", '--host', '127.0.0.1', '--port', '8178',
        '--inference-path', '/v1/audio/transcriptions', '--convert', '-l', 'auto', '-t', '8')
    for ($i = 0; $i -lt 60; $i++) {  # wait until the model is loaded, so the environment check sees it online
        try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8178/ -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep 1 }
    }
    Write-Host '本地转写服务已启动（端口 8178）'
}
try {
    & 'C:\Program Files\Git\bin\bash.exe' scripts/dev.sh
} finally {
    if ($ws) { Stop-Process -Id $ws.Id -Force -ErrorAction SilentlyContinue }
}
