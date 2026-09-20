﻿# 手工端到端验证（DoD 要求：curl 实录 + 性能观察值）
# 用法（PowerShell 5，先起 Redis 与 uvicorn）：
#   powershell -File scripts\verify_api.ps1
# 输出写入 docs\手工验证实录.txt（D4 引用性能数字时必须附这份原文）

$ErrorActionPreference = "Stop"
# PowerShell 5 默认用 GBK 解码子进程 stdout，curl 返回的 UTF-8 中文会变成乱码。
# 不设这一行的话，实录里全是"楠岃瘉"这种 mojibake（不是服务端的错，是控制台的错）。
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$base = "http://127.0.0.1:8000"
$root = Split-Path -Parent $PSScriptRoot
$out  = Join-Path (Split-Path -Parent $root) "docs\手工验证实录.txt"
$tmp  = Join-Path $env:TEMP ("examverify" + (Get-Random))
New-Item -ItemType Directory -Path $tmp | Out-Null

$code = ""
foreach ($line in Get-Content (Join-Path $root ".env")) {
    if ($line -like "TEACHER_REGISTER_CODE=*") { $code = $line.Split("=", 2)[1] }
}
if (-not $code) { throw ".env 里没有 TEACHER_REGISTER_CODE" }

function Invoke-Api {
    param([string]$Method, [string]$Path, [hashtable]$Headers, [string]$BodyJson, [switch]$ShowTime)
    $args = @("-s", "-X", $Method)
    if ($Headers) { foreach ($k in $Headers.Keys) { $args += @("-H", "$($k): $($Headers[$k])") } }
    if ($BodyJson) {
        $f = Join-Path $tmp ("body" + (Get-Random) + ".json")
        [IO.File]::WriteAllText($f, $BodyJson, (New-Object Text.UTF8Encoding $false))
        $args += @("-H", "Content-Type: application/json", "--data", "@$f")
    }
    if ($ShowTime) { $args += @("-w", "`ntime_total=%{time_total}s") }
    $args += "$base$Path"
    # curl 的多行输出（body + -w 那行）统一并成一个字符串，方便后续 split
    @(& curl.exe @args) -join "`n"
}

$log = New-Object Collections.Generic.List[string]
function Need {
    param($Value, [string]$What)
    if (-not $Value) { throw "$What 解析失败（后续步骤会全线连锁 404），原始响应见实录" }
    return $Value
}
function Step {
    param([string]$Title, [string]$Cmd, [string]$Result)
    $log.Add("### $Title")
    $log.Add("``````")
    $log.Add("PS> $Cmd")
    $log.Add($Result)
    $log.Add("``````")
    Write-Host "== $Title"
}

$suffix = Get-Random -Maximum 99999
$tUser = "vt_teacher$suffix"
$sUser = "vt_stu$suffix"
$pwd64 = "Passw0rd!"

$log.Add("# 在线考试系统 · 手工端到端实录")
$log.Add("")
$log.Add("- 时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
$log.Add("- 服务：$base（uvicorn，单实例）")
$log.Add("- 依赖：MySQL 8.0.46 @3307 / 本机原生 Redis 5.0.14.1 (db0)")
$log.Add("- 账号：$tUser（teacher）、$sUser（student），注册码来自 .env")
$log.Add("")

Step "0. 健康检查" "curl.exe -s $base/health" (Invoke-Api GET "/health")

$reg = Invoke-Api POST "/api/auth/register" $null (`
    '{"username":"' + $tUser + '","password":"' + $pwd64 + '","role":"teacher","full_name":"验证老师","teacher_code":"' + $code + '"}')
Step "1. 教师注册（带注册码）" "curl.exe -s -X POST $base/api/auth/register --data @body.json" $reg

$noCode = Invoke-Api POST "/api/auth/register" $null (`
    '{"username":"vt_noode' + $suffix + '","password":"' + $pwd64 + '","role":"teacher"}')
Step "2. 无注册码注册教师被拒（EC-26）" "curl.exe -s -X POST $base/api/auth/register --data @body_no_code.json" $noCode

$tok = Invoke-Api POST "/api/auth/token" $null ('{"username":"' + $tUser + '","password":"' + $pwd64 + '"}')
$tToken = ($tok | ConvertFrom-Json).access_token
Step "3. 教师登录拿 JWT" "curl.exe -s -X POST $base/api/auth/token --data @body.json" ($tok.Substring(0, 60) + " ...(token 截断)")

$TH = @{ Authorization = "Bearer $tToken" }
$cat = Invoke-Api POST "/api/categories" $TH '{"name":"验证分类"}'
# 注意：这里必须直接 ConvertFrom-Json。先前写成 `$cat | ConvertTo-Json -Compress | ConvertFrom-Json`，
# 对"已经是字符串"的响应再 ConvertTo-Json 会得到一个 JSON 字符串字面量，取 .id 得 $null，
# 于是请求体里出现 "category_id":, → 全线 422 并把后续步骤带崩（实测踩到）。
$catId = ($cat | ConvertFrom-Json).id
if (-not $catId) { throw "拿不到 category_id：$cat" }
Step "4. 建分类" "curl.exe -s -X POST $base/api/categories -H `"Authorization: Bearer <t>`" --data @body.json" $cat

$q1 = Invoke-Api POST "/api/questions" $TH ('{"type":"single","content":"验证题1 单选","options":["A. 对","B. 错","C. 也许","D. 不知道"],"correct_answer":"A","category_id":' + $catId + ',"difficulty":"easy"}')
$q1Id = Need ($q1 | ConvertFrom-Json).id "题目1 id"
$q2 = Invoke-Api POST "/api/questions" $TH ('{"type":"multiple","content":"验证题2 多选","options":["A. 甲","B. 乙","C. 丙","D. 丁"],"correct_answer":"A,C","category_id":' + $catId + ',"difficulty":"medium"}')
$q2Id = ($q2 | ConvertFrom-Json).id
$q3 = Invoke-Api POST "/api/questions" $TH ('{"type":"judge","content":"验证题3 判断","correct_answer":"T","category_id":' + $catId + ',"difficulty":"easy"}')
$q3Id = ($q3 | ConvertFrom-Json).id
$q4 = Invoke-Api POST "/api/questions" $TH ('{"type":"short","content":"验证题4 简答","correct_answer":"参考答案要点","category_id":' + $catId + ',"difficulty":"hard"}')
$q4Id = ($q4 | ConvertFrom-Json).id
Step "5. 建四题型各 1 题（5/15/10/20 = 卷面 50）" "curl.exe -s -X POST $base/api/questions -H `"Authorization: Bearer <t>`" --data @body.json" ($q1 + "`n" + $q2 + "`n" + $q3 + "`n" + $q4)

$badQ = Invoke-Api POST "/api/questions" $TH ('{"type":"single","content":"答案越界","options":["A. x","B. y"],"correct_answer":"Z","category_id":' + $catId + ',"difficulty":"easy"}')
Step "6. 题目校验矩阵：答案字母越界 → 400（EC/FR-02）" "curl.exe -s -X POST $base/api/questions --data @body_bad_answer.json" $badQ

$now = Get-Date
$startAt = $now.AddMinutes(-5).ToString("yyyy-MM-ddTHH:mm:ss")
$endAt = $now.AddHours(2).ToString("yyyy-MM-ddTHH:mm:ss")
$paper = Invoke-Api POST "/api/papers" $TH ('{"title":"验证卷","duration_minutes":10,"start_at":"' + $startAt + '","end_at":"' + $endAt + '","questions":[' +
  '{"question_id":' + $q1Id + ',"score":5},{"question_id":' + $q2Id + ',"score":15},{"question_id":' + $q3Id + ',"score":10},{"question_id":' + $q4Id + ',"score":20}]}')
$paperId = Need ($paper | ConvertFrom-Json).id "试卷 id"
Step "7. 组卷（逐题分值，full_score 自动求和=50）" "curl.exe -s -X POST $base/api/papers --data @body_paper.json" $paper

$pub = Invoke-Api POST "/api/papers/$paperId/publish" $TH $null
Step "8. 发布试卷 draft→published" "curl.exe -s -X POST $base/api/papers/$paperId/publish -H `"Authorization: Bearer <t>`"" $pub

$detail = Invoke-Api GET "/api/papers/$paperId" $TH $null
Step "9. 卷面详情（创建者可见答案）" "curl.exe -s $base/api/papers/$paperId -H `"Authorization: Bearer <t>`"" $detail

$sreg = Invoke-Api POST "/api/auth/register" $null ('{"username":"' + $sUser + '","password":"' + $pwd64 + '","role":"student","full_name":"验证学生"}')
$stok = Invoke-Api POST "/api/auth/token" $null ('{"username":"' + $sUser + '","password":"' + $pwd64 + '"}')
$sToken = ($stok | ConvertFrom-Json).access_token
$SH = @{ Authorization = "Bearer $sToken" }
Step "10. 学生注册并登录（无注册码要求）" "curl.exe -s -X POST $base/api/auth/register --data @body_student.json" ($sreg + "`n" + ($stok.Substring(0, 40) + " ..."))

$forbidden = Invoke-Api POST "/api/questions" $SH '{"type":"judge","content":"学生建题","correct_answer":"T","difficulty":"easy"}'
Step "11. 学生调建题接口 → 403（RBAC）" "curl.exe -s -X POST $base/api/questions -H `"Authorization: Bearer <s>`" --data @body.json" $forbidden

$start = Invoke-Api POST "/api/exams/$paperId/start" $SH $null
$recordId = ($start | ConvertFrom-Json).record_id
Step "12. 学生开考（返回题面不含答案 + remaining 600）" "curl.exe -s -X POST $base/api/exams/$paperId/start -H `"Authorization: Bearer <s>`"" $start

$ttlRaw = & "D:\Redis\redis-cli.exe" -p 6379 -n 0 TTL "exam:session:$recordId"
Step "13. Redis 会话 TTL 实测（应≈630=600+宽限30）" "D:\Redis\redis-cli.exe -n 0 TTL exam:session:$recordId" "TTL = $ttlRaw"

$again = Invoke-Api POST "/api/exams/$paperId/start" $SH $null
$againDeadline = ($again | ConvertFrom-Json).deadline_at
Step "14. 重复 start 幂等且不续时（A7 回归：deadline 不变）" "curl.exe -s -X POST $base/api/exams/$paperId/start -H `"Authorization: Bearer <s>`"" $again

$ans1 = Invoke-Api POST "/api/exams/$recordId/answer" $SH ('{"question_id":' + $q1Id + ',"answer":"a"}')
$ans2 = Invoke-Api POST "/api/exams/$recordId/answer" $SH ('{"question_id":' + $q2Id + ',"answer":["C","A"]}')
$ans3 = Invoke-Api POST "/api/exams/$recordId/answer" $SH ('{"question_id":' + $q3Id + ',"answer":"对"}')
$ans4 = Invoke-Api POST "/api/exams/$recordId/answer" $SH ('{"question_id":' + $q4Id + ',"answer":"我写了简答要点"}')
Step "15. 逐题作答（小写/乱序数组/中文判断都自动归一）" "curl.exe -s -X POST $base/api/exams/$recordId/answer --data @body.json  ×4" ($ans1 + "`n" + $ans2 + "`n" + $ans3 + "`n" + $ans4)

$cheat = Invoke-Api POST "/api/exams/$recordId/cheat-report" $SH '{"reason":"visibilitychange"}'
Step "16. 切屏上报只写 DB（无 Redis 键）" "curl.exe -s -X POST $base/api/exams/$recordId/cheat-report --data @body.json" $cheat

$submit = Invoke-Api POST "/api/exams/$recordId/submit" $SH $null -ShowTime
Step "17. 交卷判分（客观 5+15+10=30，简答待批）+ 响应耗时" "curl.exe -s -X POST $base/api/exams/$recordId/submit -w `"time_total=%{time_total}s`"" $submit

$dup = Invoke-Api POST "/api/exams/$recordId/submit" $SH $null
Step "18. 重复提交 → 409 且携带首次成绩（FR-10）" "curl.exe -s -X POST $base/api/exams/$recordId/submit" $dup

$sview = Invoke-Api GET "/api/exams/$recordId/score" $SH $null
Step "19. 学生查成绩（无答案、无学生作答原文，含待批数）" "curl.exe -s $base/api/exams/$recordId/score -H `"Authorization: Bearer <s>`"" $sview

$answers = Invoke-Api GET "/api/exams/$recordId/answers" $TH $null
Step "20. 教师读作答（批改入口，A6 闭环）" "curl.exe -s $base/api/exams/$recordId/answers -H `"Authorization: Bearer <t>`"" $answers

$review = Invoke-Api POST "/api/exams/$recordId/review" $TH ('{"question_id":' + $q4Id + ',"score":18,"comment":"要点齐全"}')
Step "21. 批改简答 18 分 → earned 48/50，记录定稿" "curl.exe -s -X POST $base/api/exams/$recordId/review --data @body_review.json" $review

$overReview = Invoke-Api POST "/api/exams/$recordId/review" $TH ('{"question_id":' + $q4Id + ',"score":21}')
Step "22. 批改越界 → 400（EC-27）" "curl.exe -s -X POST $base/api/exams/$recordId/review --data @body_over.json" $overReview

$locked = Invoke-Api PUT "/api/questions/$q1Id" $TH ('{"type":"single","content":"改题干","options":["A. 对","B. 错","C. 也许","D. 不知道"],"correct_answer":"B","category_id":' + $catId + ',"difficulty":"easy"}')
Step "23. 改已发布题目的答案 → 409 QUESTION_LOCKED（EC-22 判分基准保护）" "curl.exe -s -X PUT $base/api/questions/$q1Id --data @body_change_answer.json" $locked

$stats = Invoke-Api GET "/api/papers/$paperId/stats" $TH $null -ShowTime
Step "24. 统计（两级均分含样本数）+ 响应耗时" "curl.exe -s $base/api/papers/$paperId/stats -w `"time_total=%{time_total}s`"" $stats

$results = Invoke-Api GET "/api/papers/$paperId/results" $TH $null
Step "25. 全班成绩列表" "curl.exe -s $base/api/papers/$paperId/results" $results

$other = Invoke-Api POST "/api/auth/register" $null ('{"username":"vt_other' + $suffix + '","password":"' + $pwd64 + '","role":"student"}')
$otok = Invoke-Api POST "/api/auth/token" $null ('{"username":"vt_other' + $suffix + '","password":"' + $pwd64 + '"}')
$oToken = ($otok | ConvertFrom-Json).access_token
$OH = @{ Authorization = "Bearer $oToken" }
$steal = Invoke-Api GET "/api/exams/$recordId/score" $OH $null
$ghost = Invoke-Api GET "/api/exams/987654/score" $OH $null
Step "26. 跨学生查他人成绩 → 404（与『不存在』完全同形）" "curl.exe -s $base/api/exams/$recordId/score -H `"$($OH.Keys[0]): <other token>`"" ($steal + "`n" + $ghost)

$noauth = Invoke-Api GET "/api/questions" $null $null
Step "27. 无 token → 401（判定顺序第一档）" "curl.exe -s $base/api/questions" $noauth

$list = Invoke-Api GET "/api/exams" $SH $null
Step "28. 我的考试列表（与 /api/exams/{id} 同时可达，N2 路由回归）" "curl.exe -s $base/api/exams -H `"Authorization: Bearer <s>`"" $list

$resume = Invoke-Api GET "/api/exams/$recordId" $SH $null
Step "29. 已结束记录访问续考响应" "curl.exe -s $base/api/exams/$recordId -H `"Authorization: Bearer <s>`"" $resume

$timings = @()
foreach ($i in 1..10) {
  $raw = Invoke-Api GET "/api/papers" $SH $null -ShowTime
  $line = ($raw -split "`n")[-1]
  if ($line -match "time_total=([0-9.]+)s") { $timings += [double]$Matches[1] }
}
$sorted = $timings | Sort-Object
$p95 = $sorted[[math]::Floor(($sorted.Count - 1) * 0.95)]
Step "30. 性能观察值（NFR-01 已降为观察项，不作为验收数字）" "循环 10 次 curl.exe -s $base/api/papers -w `"time_total=%{time_total}s`"" ("10 次耗时(秒)=" + ($timings -join ", ") + "`n近似 P95=$p95 s（样本只有 10 次，只作观察）")

Set-Content -Path $out -Value ($log -join "`n") -Encoding UTF8
Remove-Item -Recurse -Force $tmp
Write-Host "`n实录已写入 $out"
