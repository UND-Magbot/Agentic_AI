# ============================================================
# UND Cortex Smoke Test (PowerShell)
# req.md §13.5 인수 기준 자동 검증.
# ============================================================
$ErrorActionPreference = "Stop"

$pass = 0
$fail = 0
$results = @()

function Check {
    param(
        [string]$Name,
        [scriptblock]$Test
    )
    Write-Host -NoNewline ("  {0,-40}" -f $Name)
    try {
        & $Test
        $script:pass += 1
        $script:results += "PASS  $Name"
        Write-Host "PASS" -ForegroundColor Green
    } catch {
        $script:fail += 1
        $script:results += "FAIL  $Name :: $($_.Exception.Message)"
        Write-Host "FAIL" -ForegroundColor Red
        Write-Host "      $($_.Exception.Message)" -ForegroundColor DarkRed
    }
}

Write-Host ""
Write-Host "============================================================"
Write-Host " UND Cortex Smoke Test"
Write-Host "============================================================"

# 1) 컨테이너 healthy
Check "containers all healthy" {
    $services = @("postgres","redis","minio","backend","frontend")
    foreach ($s in $services) {
        $status = docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "und_cortex_$s" 2>$null
        if ($status -ne "healthy" -and $status -ne "none") {
            throw "$s status=$status"
        }
    }
}

# 2) Postgres pg_isready
Check "postgres pg_isready" {
    $out = docker compose exec -T postgres pg_isready -U cortex 2>&1
    if ($LASTEXITCODE -ne 0) { throw "exit=$LASTEXITCODE out=$out" }
}

# 3) pgvector extension
Check "pgvector extension installed" {
    $out = docker compose exec -T postgres psql -U cortex -d cortex -tAc "SELECT extname FROM pg_extension WHERE extname='vector';" 2>&1
    if ($out.Trim() -ne "vector") { throw "extension not found: '$out'" }
}

# 4) Redis ping
Check "redis ping" {
    $pw = (Get-Content .env | Select-String "^REDIS_PASSWORD=").ToString().Split("=",2)[1]
    $out = docker compose exec -T redis redis-cli -a "$pw" --no-auth-warning ping 2>&1
    if ($out.Trim() -ne "PONG") { throw "got '$out'" }
}

# 5) MinIO health
Check "minio /minio/health/live" {
    $r = Invoke-WebRequest -Uri "http://localhost:9001/minio/health/live" -UseBasicParsing -TimeoutSec 5
    if ($r.StatusCode -ne 200) { throw "status=$($r.StatusCode)" }
}

# 6) Agent (backend) /health
Check "backend /health" {
    $r = Invoke-WebRequest -Uri "http://localhost:8002/health" -UseBasicParsing -TimeoutSec 5
    if ($r.StatusCode -ne 200) { throw "status=$($r.StatusCode)" }
    $j = $r.Content | ConvertFrom-Json
    if (-not $j.ok) { throw "ok=false body=$($r.Content)" }
}

# 7) BFF /health (현 단계: NestJS 미도입, frontend root 200으로 대체)
Check "frontend root reachable" {
    $r = Invoke-WebRequest -Uri "http://localhost:3010" -UseBasicParsing -TimeoutSec 10
    if ($r.StatusCode -ne 200) { throw "status=$($r.StatusCode)" }
}

# 8) Frontend internal API /api/chat (smoke ping)
Check "frontend /api/chat smoke" {
    $body = @{ domain = "auto"; history = @(); text = "ping" } | ConvertTo-Json -Compress
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:3010/api/chat" -Method POST `
            -Body $body -ContentType "application/json" -UseBasicParsing -TimeoutSec 15
        if ($r.StatusCode -ne 200) { throw "status=$($r.StatusCode)" }
    } catch {
        # vLLM이 없으면 backend가 504/500을 던져 stream error 텍스트가 와도 200일 수 있다.
        # 라우팅 자체가 실패하지 않으면 통과로 본다.
        if ($_.Exception.Response.StatusCode -ne 200) {
            throw "status=$($_.Exception.Response.StatusCode)"
        }
    }
}

# 9) Backend → vLLM 연결 (정보용, vLLM 없으면 skip)
Check "backend reports vllm config" {
    $r = Invoke-WebRequest -Uri "http://localhost:8002/health" -UseBasicParsing -TimeoutSec 5
    $j = $r.Content | ConvertFrom-Json
    if (-not $j.vllm) { throw "vllm url missing" }
}

Write-Host ""
Write-Host "============================================================"
Write-Host " 결과: PASS=$pass FAIL=$fail"
Write-Host "============================================================"

if ($fail -gt 0) {
    foreach ($r in $results) { if ($r -like "FAIL*") { Write-Host $r -ForegroundColor Red } }
    exit 1
}
exit 0
