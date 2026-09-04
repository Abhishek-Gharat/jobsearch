param([Parameter(Mandatory=$true)][string]$line)
Add-Content -LiteralPath '<PROJECT_ROOT>\autoapply\results\b20260823_155858_008_smartrecruiters.jsonl' -Value $line
$ts = Get-Date -Format o
"{0}|worker_w2084_8|result_logged" -f $ts | Add-Content -LiteralPath '<PROJECT_ROOT>\autoapply\heartbeat.log'
