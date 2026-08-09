param(
    [Parameter(Mandatory=$true)]
    [string]$ControllerUrl,

    [Parameter(Mandatory=$true)]
    [string]$EnrollmentToken,

    [ValidateSet("none","cpu","gpu")]
    [string]$Onnx = "none",

    [ValidateSet("none","12","13")]
    [string]$Cuda = "none",

    [string]$InstallDir = "$env:LOCALAPPDATA\ComputeSwarm"
)

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/CaMaLabs/Android-compute-cluster.git"
$Branch = "agent/universal-compute-swarm"

function Ensure-Command($Name, $WingetId) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            throw "$Name is required and winget is not available. Install $Name, then rerun this script."
        }
        Write-Host "Installing $Name..."
        winget install --id $WingetId --exact --accept-package-agreements --accept-source-agreements
        $env:PATH = [Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [Environment]::GetEnvironmentVariable("PATH","User")
    }
}

Ensure-Command "git" "Git.Git"
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Ensure-Command "python" "Python.Python.3.12"
}

$RepoDir = Join-Path $InstallDir "repo"
$VenvDir = Join-Path $InstallDir "venv"
$ConfigDir = Join-Path $InstallDir "config"
New-Item -ItemType Directory -Force -Path $InstallDir, $ConfigDir | Out-Null

if (Test-Path (Join-Path $RepoDir ".git")) {
    git -C $RepoDir fetch origin $Branch
    git -C $RepoDir checkout $Branch
    git -C $RepoDir reset --hard "origin/$Branch"
} else {
    git clone --branch $Branch --single-branch $RepoUrl $RepoDir
}

python -m venv $VenvDir
$Python = Join-Path $VenvDir "Scripts\python.exe"
& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $RepoDir "worker\requirements.txt")

if ($Onnx -eq "cpu") {
    & $Python -m pip install -r (Join-Path $RepoDir "worker\requirements-onnx.txt")
} elseif ($Onnx -eq "gpu") {
    & $Python -m pip install -r (Join-Path $RepoDir "worker\requirements-onnx-gpu.txt")
}

if ($Cuda -eq "12") {
    & $Python -m pip install -r (Join-Path $RepoDir "worker\requirements-cuda12.txt")
} elseif ($Cuda -eq "13") {
    & $Python -m pip install -r (Join-Path $RepoDir "worker\requirements-cuda13.txt")
}

$AllowInsecure = 0
if ($ControllerUrl -match '^http://(localhost|127\.0\.0\.1|10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)') {
    $AllowInsecure = 1
}

$EnvFile = Join-Path $ConfigDir "worker.env.ps1"
@"
`$env:SWARM_CONTROLLER_URL = '$ControllerUrl'
`$env:SWARM_ENROLLMENT_TOKEN = '$EnrollmentToken'
`$env:SWARM_ALLOW_INSECURE_REMOTE = '$AllowInsecure'
"@ | Set-Content -Encoding UTF8 $EnvFile

$Runner = Join-Path $InstallDir "run-worker.ps1"
@"
`$ErrorActionPreference = 'Stop'
. '$EnvFile'
& '$Python' '$RepoDir\worker\worker.py'
"@ | Set-Content -Encoding UTF8 $Runner

$TaskName = "ComputeSwarmWorker"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -RestartCount 20 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 3650)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "Compute Swarm worker installed."
Write-Host "Install directory: $InstallDir"
Write-Host "Startup task: $TaskName"
Write-Host "Controller: $ControllerUrl"
Write-Host ""
Write-Host "Stop:    Stop-ScheduledTask -TaskName '$TaskName'"
Write-Host "Start:   Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Remove:  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
