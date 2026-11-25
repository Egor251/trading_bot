# Register the observe-only online-learning daemon at logon.
# Run once from an elevated PowerShell prompt. No trading execution is wired.
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $python)) { $python = 'pythonw.exe' }
$arguments = '"' + (Join-Path $repo 'tools\online_daemon.py') + '" --mode live --interval 300'
$action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'trading-bot-online-learning' `
  -Action $action -Trigger $trigger -Settings $settings -Force
Write-Host 'Registered observe-only online-learning daemon at logon.'
