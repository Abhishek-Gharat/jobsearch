param(
  [Parameter(Mandatory=$true)][string]$step,
  [string]$jid = "",
  [string]$url = ""
)
$ts = Get-Date -Format o
"{0}|worker_w2084_8|{1}" -f $ts, $step | Add-Content -LiteralPath 'D:\newjobs\autoapply\heartbeat.log'
$batchId = 'b20260823_155858_008_smartrecruiters'
$obj = [ordered]@{
  batch_id  = $batchId
  worker_id = 'w2084_8'
  job       = [ordered]@{ id = $jid; url = $url; step = $step }
  ts        = $ts
}
$json = $obj | ConvertTo-Json -Compress
Set-Content -LiteralPath ('D:\newjobs\autoapply\results\' + $batchId + '.progress.json') -Value $json
