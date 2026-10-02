param([Parameter(Mandatory=$true)][string]$Line)
$Line | Add-Content -LiteralPath 'D:\newjobs\autoapply\results\b20260823_115200_004_smartrecruiters.jsonl'
