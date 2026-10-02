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
# To rerun only tasks that failed in an earlier experiment, add:
#       -FailedFrom $env:TEMP\v2-previous\runs\<experiment>\<condition>
#
# Output: <Out>\runs\<experiment>\<condition>\<task>\<n>\{verdict,trace,log}.json plus <Out>\summary.json

param(
  [string]$Pack = "modules/demand-task-factory/out-v2/task_pack.jsonl",
  [string]$Out = "$env:TEMP\v2-full",
  [int]$Jobs = 4,
  [int]$Runs = 1,
  [int]$MaxTokens = 12288,
  [int]$TimeoutSeconds = 180,
  [string]$ExperimentId = "v2full",
  [string]$Condition = "B-no-skill",
  [string]$Skill = "",
  [string]$PythonExe = "",
  [string]$FailedFrom = ""
)

$ErrorActionPreference = "Stop"
$wd = (Get-Location).Path
$py = $PythonExe
if ([string]::IsNullOrWhiteSpace($py)) {
  $venvPython = Join-Path $wd ".venv\Scripts\python.exe"
  if (Test-Path $venvPython) {
    $py = $venvPython
  } else {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $pythonCommand) { $pythonCommand = Get-Command python -ErrorAction SilentlyContinue }
    if (-not $pythonCommand) { throw "Python was not found; pass -PythonExe with its full path" }
    $py = $pythonCommand.Source
  }
}
if (-not (Test-Path $py)) { throw "Python executable not found at $py" }

# Accept either the direct LLM_* process environment or the TRAVEL_* names in .env.
# Keep the API settings available to each Start-Job worker. Fall back to LM Studio
# only when no live API key has been configured.
$dotenv = @{}
$dotenvPath = Join-Path $wd ".env"
if (Test-Path $dotenvPath) {
  foreach ($line in Get-Content $dotenvPath) {
    if ($line -match '^\s*([^#=\s]+)\s*=\s*(.*)\s*$') {
      $dotenv[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
    }
  }
}
if ([string]::IsNullOrWhiteSpace($env:LLM_API_KEY)) { $env:LLM_API_KEY = $dotenv["TRAVEL_LLM_API_KEY"] }
if ([string]::IsNullOrWhiteSpace($env:LLM_BASE_URL)) { $env:LLM_BASE_URL = $dotenv["TRAVEL_LLM_BASE_URL"] }
if ([string]::IsNullOrWhiteSpace($env:LLM_MODEL)) {
  $env:LLM_MODEL = $dotenv["TRAVEL_GENERATOR_MODEL"]
  if ([string]::IsNullOrWhiteSpace($env:LLM_MODEL)) { $env:LLM_MODEL = $dotenv["TRAVEL_CLASSIFIER_MODEL"] }
}
# Match run_live.py: bypass host proxy settings for direct model endpoint requests.
foreach ($proxyName in @("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy")) {
  Remove-Item "Env:$proxyName" -ErrorAction SilentlyContinue
}
$evaluationModule = Join-Path $wd "modules\execution-evaluation"
$env:PYTHONPATH = $evaluationModule
if ($env:LLM_API_KEY) {
  Write-Host "[run_v2_parallel] model mode=API endpoint=$($env:LLM_BASE_URL) model=$($env:LLM_MODEL)"
} else {
  Write-Host "[run_v2_parallel] model mode=LM Studio local default"
}

$failedRoot = ""
if (-not [string]::IsNullOrWhiteSpace($FailedFrom)) {
  if (-not (Test-Path $FailedFrom)) { throw "Previous verdict root not found at $FailedFrom" }
  $failedRoot = (Resolve-Path $FailedFrom).Path
  $outFull = [IO.Path]::GetFullPath($Out).TrimEnd([IO.Path]::DirectorySeparatorChar)
  if ($failedRoot.Equals($outFull, [StringComparison]::OrdinalIgnoreCase) -or
      $failedRoot.StartsWith($outFull + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "FailedFrom must be outside Out because Out is recreated before every run"
  }
}

Remove-Item $Out -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Out | Out-Null

$effectivePack = $Pack
if ($failedRoot) {
  $selector = Join-Path $wd "integration\pipeline\select_failed_tasks.py"
  $effectivePack = Join-Path $Out "failed-task-pack.jsonl"
  $selectionManifest = Join-Path $Out "failed-task-pack.manifest.json"
  & $py $selector --task-pack $Pack --verdict-root $failedRoot --out $effectivePack --manifest $selectionManifest
  if ($LASTEXITCODE -ne 0) { throw "select_failed_tasks.py failed with exit code $LASTEXITCODE" }
}

# Round-robin split keeps every family (B/C/D/E) present in every slice.
$splitter = Join-Path $wd "integration\pipeline\split_pack.mjs"
node $splitter $effectivePack $Out $Jobs
if ($LASTEXITCODE -ne 0) { throw "split_pack.mjs failed with exit code $LASTEXITCODE" }

$runRoot = Join-Path $Out "runs"
$jobList = @()
for ($i = 0; $i -lt $Jobs; $i++) {
  $slice = Join-Path $Out "pack$i.jsonl"
  # Empty slices are skipped: when the pack has fewer cards than Jobs, split_pack.mjs
  # leaves empty packN.jsonl files, the runner then dies with FileNotFoundError, and the
  # job still reports State=Completed -- i.e. the whole batch fails silently.
  if (-not (Test-Path $slice)) { continue }
  if ((Get-Item $slice).Length -eq 0) { continue }
  $jobList += Start-Job -ScriptBlock {
    param($wd, $py, $slice, $runRoot, $Runs, $MaxTokens, $TimeoutSeconds, $ExperimentId, $Condition, $Skill)
    Set-Location $wd
    foreach ($proxyName in @("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy")) {
      Remove-Item "Env:$proxyName" -ErrorAction SilentlyContinue
    }
    if (-not $env:LLM_API_KEY) {
      $env:LLM_API_KEY  = "lm-studio-local"
      $env:LLM_BASE_URL = "http://127.0.0.1:1234/v1"
      $env:LLM_MODEL    = "minicpm5-2b"
    }
    $env:LLM_MAX_TOKENS   = "$MaxTokens"
    $env:LLM_TIMEOUT_SECONDS = "$TimeoutSeconds"
    $env:PYTHONPATH       = Join-Path $wd "modules\execution-evaluation"
    $env:PYTHONIOENCODING = "utf-8"
    $extra = @()
    if ($Skill -ne "") { $extra = @("--skill", $Skill) }
    & $py -m execution_evaluation run --task-pack $slice --condition $Condition --runs $Runs `
        --experiment-id $ExperimentId --out $runRoot @extra 2>&1 | Select-Object -Last 2
    # Surface failures: otherwise a crashed runner looks like a successful job.
    if ($LASTEXITCODE -ne 0) { throw "runner exited $LASTEXITCODE for $slice" }
  } -ArgumentList $wd, $py, $slice, $runRoot, $Runs, $MaxTokens, $TimeoutSeconds, $ExperimentId, $Condition, $Skill
}

$jobList | Wait-Job | Out-Null
$jobList | Receive-Job
$bad = $jobList | Where-Object { $_.State -ne "Completed" }
$jobList | Remove-Job

if ($bad) { throw "slice jobs did not complete: $($bad.State -join ',')" }

$env:PYTHONPATH = $evaluationModule; $env:PYTHONIOENCODING = "utf-8"
& $py -m execution_evaluation summarize --experiment-id $ExperimentId --out (Join-Path $Out "report.md") --run-root $runRoot 2>&1 | Select-Object -Last 2
Write-Host "[run_v2_parallel] done: $Out"
