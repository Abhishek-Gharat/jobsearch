param(
  [Parameter(Mandatory=$true)][string]$Step,
  [string]$JobId,
  [string]$Url
)
$ts = Get-Date -Format o
"{0}|worker_w20756_4_r1|{1}" -f $ts, $Step | Add-Content -LiteralPath '<PROJECT_ROOT>\autoapply\heartbeat.log'
if ($JobId) {
  $progress = [ordered]@{
    batch_id = 'b20260823_115200_004_smartrecruiters'
    worker_id = 'w20756_4_r1'
    job = [ordered]@{ id = $JobId; url = $Url; step = $Step }
    ts = $ts
  }
  ($progress | ConvertTo-Json -Compress -Depth 5) | Set-Content -LiteralPath '<PROJECT_ROOT>\autoapply\results\b20260823_115200_004_smartrecruiters.progress.json'
}
