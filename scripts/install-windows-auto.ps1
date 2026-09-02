param(
    [string]$ControllerUrl = "https://45.50.0.74:8675",
    [string]$EnrollmentToken = $env:SWARM_ENROLLMENT_TOKEN,
    [string]$InstallDir = "$env:LOCALAPPDATA\ComputeSwarm",
    [int]$UpdateIntervalMinutes = 15,
    [switch]$AllowInsecurePublicController
)

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/CaMaLabs/Android-compute-cluster.git"
$Branch = "main"

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

function Escape-SwarmValue([string]$Value) {
    if ($null -eq $Value) { return "" }
    return ($Value -replace '[,=\r\n]', '_').Trim()
}

Write-Host "Compute Swarm automatic Windows worker installer"
Write-Host "Controller: $ControllerUrl"

Ensure-Command "git" "Git.Git"
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Ensure-Command "python" "Python.Python.3.12"
}

if ([string]::IsNullOrWhiteSpace($EnrollmentToken)) {
    $secure = Read-Host "Controller enrollment token" -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $EnrollmentToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}
if ([string]::IsNullOrWhiteSpace($EnrollmentToken)) {
    throw "An enrollment token is required for first-time enrollment. The controller generates each worker's device token automatically after this bootstrap step."
}

$uri = [Uri]$ControllerUrl
$AllowInsecure = 0
if ($uri.Scheme -eq 'http') {
    $privateHost = $uri.Host -match '^(localhost|127\.0\.0\.1|10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)'
    if ($privateHost -or $AllowInsecurePublicController) { $AllowInsecure = 1 }
    else { throw "Public HTTP controller refused. Use HTTPS, or explicitly pass -AllowInsecurePublicController if you accept plaintext credentials on the network." }
}

$GpuControllers = @()
try {
    $GpuControllers = @(Get-CimInstance Win32_VideoController | Where-Object { $_.Name } | Select-Object -ExpandProperty Name)
} catch {
    Write-Warning "GPU enumeration through CIM failed: $($_.Exception.Message)"
}
if (-not $GpuControllers) { $GpuControllers = @("No discrete GPU detected") }

Write-Host "Detected graphics adapters:"
$GpuControllers | ForEach-Object { Write-Host "  - $_" }

$HasNvidia = [bool]($GpuControllers | Where-Object { $_ -match 'NVIDIA' })
$HasAmd = [bool]($GpuControllers | Where-Object { $_ -match 'AMD|Radeon' })
$HasIntel = [bool]($GpuControllers | Where-Object { $_ -match 'Intel' })
$CudaMajor = $null
if ($HasNvidia -and (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    try {
        $smi = (& nvidia-smi 2>&1 | Out-String)
        if ($smi -match 'CUDA Version:\s*(\d+)\.') { $CudaMajor = [int]$Matches[1] }
    } catch {}
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
$UpdateRequirements = @("worker\requirements.txt")

# ONNX CPU is useful on every Windows worker. NVIDIA workers are upgraded to the CUDA provider.
if ($HasNvidia) {
    Write-Host "NVIDIA GPU detected; installing GPU inference backend..."
    & $Python -m pip install -r (Join-Path $RepoDir "worker\requirements-onnx-gpu.txt")
    $UpdateRequirements += "worker\requirements-onnx-gpu.txt"

    if ($CudaMajor -ge 13) {
        Write-Host "CUDA 13-compatible NVIDIA driver detected; installing CuPy CUDA 13 backend..."
        & $Python -m pip install -r (Join-Path $RepoDir "worker\requirements-cuda13.txt")
        $UpdateRequirements += "worker\requirements-cuda13.txt"
    } else {
        Write-Host "Installing CuPy CUDA 12 backend (compatible default for NVIDIA Windows workers)..."
        & $Python -m pip install -r (Join-Path $RepoDir "worker\requirements-cuda12.txt")
        $UpdateRequirements += "worker\requirements-cuda12.txt"
    }
} else {
    Write-Host "No NVIDIA CUDA adapter detected; installing CPU ONNX backend."
    & $Python -m pip install -r (Join-Path $RepoDir "worker\requirements-onnx.txt")
    $UpdateRequirements += "worker\requirements-onnx.txt"
}

$GpuVendor = if ($HasNvidia) { "nvidia" } elseif ($HasAmd) { "amd" } elseif ($HasIntel) { "intel" } else { "unknown" }
$GpuName = Escape-SwarmValue (($GpuControllers -join " + "))
$Labels = "gpu_vendor=$(Escape-SwarmValue $GpuVendor),gpu_name=$GpuName,installer=windows-auto"

$EnvFile = Join-Path $ConfigDir "worker.env.ps1"
$SafeController = $ControllerUrl.Replace("'", "''")
$SafeToken = $EnrollmentToken.Replace("'", "''")
$SafeLabels = $Labels.Replace("'", "''")
@"
`$env:SWARM_CONTROLLER_URL = '$SafeController'
`$env:SWARM_ENROLLMENT_TOKEN = '$SafeToken'
`$env:SWARM_ALLOW_INSECURE_REMOTE = '$AllowInsecure'
`$env:SWARM_LABELS = '$SafeLabels'
"@ | Set-Content -Encoding UTF8 $EnvFile

$Runner = Join-Path $InstallDir "run-worker.ps1"
@"
`$ErrorActionPreference = 'Stop'
. '$EnvFile'
& '$Python' '$RepoDir\worker\worker.py'
"@ | Set-Content -Encoding UTF8 $Runner

# Run once interactively so enrollment/accelerator problems fail during installation rather than at the next logon.
Write-Host "Testing controller connectivity and worker enrollment..."
. $EnvFile
$Probe = Start-Process -FilePath $Python -ArgumentList @((Join-Path $RepoDir "worker\worker.py")) -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 8
if ($Probe.HasExited -and $Probe.ExitCode -ne 0) {
    throw "Worker exited during enrollment/initialization with code $($Probe.ExitCode). Run $Runner manually to see the worker error."
}
if (-not $Probe.HasExited) { Stop-Process -Id $Probe.Id -Force }

$TaskName = "ComputeSwarmWorker"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -RestartCount 20 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 3650)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

$UpdateConfig = Join-Path $ConfigDir "update.env.ps1"
$ReqLiteral = ($UpdateRequirements | ForEach-Object { "'$_'" }) -join ', '
@"
`$RepoDir = '$RepoDir'
`$Branch = '$Branch'
`$Python = '$Python'
`$Requirements = @($ReqLiteral)
`$WorkerTask = '$TaskName'
`$StateFile = '$(Join-Path $ConfigDir "update-state.txt")'
"@ | Set-Content -Encoding UTF8 $UpdateConfig

$UpdateTaskName = "ComputeSwarmAutoUpdate"
$UpdateScript = Join-Path $RepoDir "scripts\auto-update-windows.ps1"
$UpdateAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$UpdateScript`" -ConfigFile `"$UpdateConfig`""
$UpdateStart = (Get-Date).AddMinutes(5)
$UpdateTrigger = New-ScheduledTaskTrigger -Once -At $UpdateStart -RepetitionInterval (New-TimeSpan -Minutes $UpdateIntervalMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
$UpdateSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
Register-ScheduledTask -TaskName $UpdateTaskName -Action $UpdateAction -Trigger $UpdateTrigger -Principal $Principal -Settings $UpdateSettings -Force | Out-Null

Write-Host ""
Write-Host "Compute Swarm worker installed and initialized."
Write-Host "Controller: $ControllerUrl"
Write-Host "GPU vendor: $GpuVendor"
Write-Host "GPU(s): $($GpuControllers -join '; ')"
Write-Host "Worker task: $TaskName"
Write-Host "Auto-update task: $UpdateTaskName"
Write-Host "Install directory: $InstallDir"
Write-Host ""
Write-Host "The controller-issued worker/device token is now stored in the worker identity file automatically."
