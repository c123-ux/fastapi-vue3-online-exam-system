# 启动本机原生 Redis（无 Docker：这台机器上根本没装）。
# 关键点：Redis 的 dump.rdb 落在**当前工作目录**（redis.windows.conf 里 dir ./），
# 所以脚本先 Set-Location 到 D:\Redis，避免把 RDB 写进含中文的项目目录。
# 用法：powershell -File scripts\start_redis.ps1
# 关停：D:\Redis\redis-cli.exe shutdown nosave

$RedisHome = "D:\Redis"
$Server = Join-Path $RedisHome "redis-server.exe"
$Conf = Join-Path $RedisHome "redis.windows.conf"

if (-not (Test-Path $Server)) {
    Write-Error "找不到 $Server —— 本机 Redis 不在 D:\Redis，请改本脚本的 `$RedisHome"
    exit 1
}

$listening = Get-NetTCPConnection -LocalPort 6379 -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Write-Host "Redis 已在监听 6379（PID $($listening[0].OwningProcess)），无需重复启动"
    exit 0
}

Set-Location $RedisHome
Write-Host "启动 redis-server 5.0.14.1（db 约定：项目三 dev=db0 / test=db1，项目四起用 db2）"
Write-Host "客户端必须走 RESP2：Redis 5.0 不认 HELLO 命令"
& $Server $Conf
