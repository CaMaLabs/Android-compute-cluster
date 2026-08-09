param(
    [Parameter(Mandatory=$true)]
    [string]$ConfigFile
)

$ErrorActionPreference = 'Stop'
. $ConfigFile

function Write-UpdateState {
    param([string]$Status, [string]$Current, [string]$Target, [string]$Message)
    @"
status=$Status
checked_at=$([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'))
current=$Current
target=$Target
message=$($Message -replace "`r?`n", ' ')
"@ | Set-Content -Encoding UTF8 $StateFile
}

function Git-Text {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)
    $out = & git -C $RepoDir @Args
    if ($LASTEXITCODE -ne 0) { throw "git $($Args -join ' ') failed" }
    return ($out | Out-String).Trim()
}

function Install-Requirements {
    foreach ($req in $Requirements) {
        if ([string]::IsNullOrWhiteSpace($req)) { continue }
        $path = Join-Path $RepoDir $req
        if (-not (Test-Path $path)) { throw "requirements file missing: $req" }
        & $Python -m pip install -r $path
        if ($LASTEXITCODE -ne 0) { throw "pip install failed for $req" }
    }
}

if (-not (Test-Path (Join-Path $RepoDir '.git'))) {
    Write-UpdateState 'error' 'unknown' 'unknown' "repository not found: $RepoDir"
    exit 1
}

# Refuse to overwrite tracked local modifications.
& git -C $RepoDir diff --quiet
$dirtyWork = $LASTEXITCODE -ne 0
& git -C $RepoDir diff --cached --quiet
$dirtyIndex = $LASTEXITCODE -ne 0
$current = Git-Text rev-parse HEAD
if ($dirtyWork -or $dirtyIndex) {
    Write-UpdateState 'blocked_dirty' $current $current 'tracked local changes detected'
    exit 2
}

Git-Text fetch --prune origin $Branch | Out-Null
$current = Git-Text rev-parse HEAD
$target = Git-Text rev-parse "origin/$Branch"
if ($current -eq $target) {
    Write-UpdateState 'up_to_date' $current $target 'no update available'
    exit 0
}

& git -C $RepoDir merge-base --is-ancestor $current $target
if ($LASTEXITCODE -ne 0) {
    Write-UpdateState 'blocked_diverged' $current $target "local history is not a fast-forward of origin/$Branch"
    exit 3
}

$updated = $false
try {
    Git-Text checkout $Branch | Out-Null
    Git-Text merge --ff-only "origin/$Branch" | Out-Null
    $updated = $true
    Install-Requirements

    if (Get-ScheduledTask -TaskName $WorkerTask -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $WorkerTask -ErrorAction SilentlyContinue
        Start-ScheduledTask -TaskName $WorkerTask
        Start-Sleep -Seconds 3
        $task = Get-ScheduledTask -TaskName $WorkerTask
        if ($task.State -eq 'Disabled') { throw "worker task is disabled after restart" }
    }

    $new = Git-Text rev-parse HEAD
    Write-UpdateState 'updated' $new $target "updated successfully from $current"
} catch {
    $reason = $_.Exception.Message
    if ($updated) {
        try {
            Git-Text reset --hard $current | Out-Null
            Install-Requirements
            if (Get-ScheduledTask -TaskName $WorkerTask -ErrorAction SilentlyContinue) {
                Stop-ScheduledTask -TaskName $WorkerTask -ErrorAction SilentlyContinue
                Start-ScheduledTask -TaskName $WorkerTask
            }
        } catch { }
        Write-UpdateState 'rolled_back' $current $target $reason
    } else {
        Write-UpdateState 'error' $current $target $reason
    }
    throw
}
