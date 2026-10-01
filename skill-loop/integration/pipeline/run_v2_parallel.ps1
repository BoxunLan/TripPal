# Run dataset v2 in parallel: split the task pack round-robin into N slices and
# drive N concurrent runner processes, so all N LM Studio inference slots are used.
#
# Why: the B-line runner issues requests sequentially, while the local model exposes
# 4 parallel slots (measured aggregate throughput ~2.75x). 218 cards take ~40 min
# sequentially and ~15 min with 4-way parallelism.
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 decodes .ps1 files as ANSI
# unless they carry a UTF-8 BOM, so non-ASCII text here gets corrupted (and can even
# break quoting).
#
# Usage (from the skill-loop directory):
#   powershell -NoProfile -File integration/pipeline/run_v2_parallel.ps1 `
#       -Pack modules/demand-task-factory/out-v2/task_pack.jsonl -Out $env:TEMP\v2-full -Jobs 4
#
# Output: <Out>\runs\<experiment>\<condition>\<task>\<n>\{verdict,trace,log}.json plus <Out>\summary.json

param(
  [string]$Pack = "modules/demand-task-factory/out-v2/task_pack.jsonl",
  [string]$Out = "$env:TEMP\v2-full",
  [int]$Jobs = 4,
  [int]$Runs = 1,
  [int]$MaxTokens = 4096,
  [int]$TimeoutSeconds = 120,
  [string]$ExperimentId = "v2full",
  [string]$Condition = "B-no-skill",
  [string]$Skill = ""
)

$ErrorActionPreference = "Stop"
$wd = (Get-Location).Path
$py = Join-Path $wd ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "venv python not found at $py" }

Remove-Item $Out -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Out | Out-Null

# Round-robin split keeps every family (B/C/D/E) present in every slice.
$splitter = Join-Path $wd "integration\pipeline\split_pack.mjs"
node $splitter $Pack $Out $Jobs
if ($LASTEXITCODE -ne 0) { throw "split_pack.mjs failed with exit code $LASTEXITCODE" }

$runRoot = Join-Path $Out "runs"
$jobList = @()
for ($i = 0; $i -lt $Jobs; $i++) {
  $slice = Join-Path $Out "pack$i.jsonl"
  $jobList += Start-Job -ScriptBlock {
    param($wd, $py, $slice, $runRoot, $Runs, $MaxTokens, $TimeoutSeconds, $ExperimentId, $Condition, $Skill)
    Set-Location $wd
    $env:LLM_API_KEY      = "lm-studio-local"
    $env:LLM_BASE_URL     = "http://127.0.0.1:1234/v1"
    $env:LLM_MODEL        = "minicpm5-2b"
    $env:LLM_MAX_TOKENS   = "$MaxTokens"
    $env:LLM_TIMEOUT_SECONDS = "$TimeoutSeconds"
    $env:PYTHONPATH       = ""
    $env:PYTHONIOENCODING = "utf-8"
    $extra = @()
    if ($Skill -ne "") { $extra = @("--skill", $Skill) }
    & $py -m execution_evaluation run --task-pack $slice --condition $Condition --runs $Runs `
        --experiment-id $ExperimentId --out $runRoot @extra 2>&1 | Select-Object -Last 2
  } -ArgumentList $wd, $py, $slice, $runRoot, $Runs, $MaxTokens, $TimeoutSeconds, $ExperimentId, $Condition, $Skill
}

$jobList | Wait-Job | Out-Null
$jobList | Receive-Job
$bad = $jobList | Where-Object { $_.State -ne "Completed" }
$jobList | Remove-Job

if ($bad) { throw "slice jobs did not complete: $($bad.State -join ',')" }

$env:PYTHONPATH = ""; $env:PYTHONIOENCODING = "utf-8"
& $py -m execution_evaluation summarize --experiment-id $ExperimentId --out (Join-Path $Out "report.md") --run-root $runRoot 2>&1 | Select-Object -Last 2
Write-Host "[run_v2_parallel] done: $Out"
