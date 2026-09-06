param(
    [string]$ControllerUrl = "http://45.50.0.74:8765",
    [string]$InstallDir = "$env:LOCALAPPDATA\ComputeSwarm",
    [int]$UpdateIntervalMinutes = 15,
    [int]$PairingTimeoutMinutes = 15,
    [switch]$AllowInsecurePublicController
)

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/CaMaLabs/Android-compute-cluster.git"
$Branch = "main"
$TrustedDefaultController = "http://45.50.0.74:8765"

function Ensure-Command($Name, $WingetId) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            throw "$Name is required and winget is not available. Install $Name, then rerun this script."
        }
        Write-Host "Installing $Name..."
        winget install --id $WingetId --exact --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { throw "winget failed while installing $Name with exit code $LASTEXITCODE." }
        $env:PATH = [Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [Environment]::GetEnvironmentVariable("PATH","User")
    }
}

function Test-Python312Candidate([string]$Command, [string[]]$PrefixArgs = @()) {
    if (-not $Command) { return $null }
    try {
        $probe = & $Command @PrefixArgs -c "import os,sys; print(os.path.abspath(sys.executable)); raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 12)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $probe) {
            $resolved = ([string]($probe | Select-Object -Last 1)).Trim()
            if ($resolved -and (Test-Path $resolved -PathType Leaf)) {
                return [System.IO.Path]::GetFullPath($resolved)
            }
        }
    } catch {}
    return $null
}

function Find-Python312 {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand -and $pythonCommand.Source) {
        $resolved = Test-Python312Candidate $pythonCommand.Source
        if ($resolved) { return $resolved }
    }

    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher -and $launcher.Source) {
        $resolved = Test-Python312Candidate $launcher.Source @("-3.12")
        if ($resolved) { return $resolved }
    }

    $patterns = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
        "$env:ProgramFiles\Python312\python.exe",
        "$env:ProgramFiles\Python3*\python.exe",
        "${env:ProgramFiles(x86)}\Python312\python.exe",
        "${env:ProgramFiles(x86)}\Python3*\python.exe"
    )
    foreach ($pattern in $patterns) {
        if (-not $pattern) { continue }
        $candidates = @(Get-ChildItem -Path $pattern -File -ErrorAction SilentlyContinue | Sort-Object FullName -Descending)
        foreach ($candidate in $candidates) {
            $resolved = Test-Python312Candidate $candidate.FullName
            if ($resolved) { return $resolved }
        }
    }
    return $null
}

function Ensure-Python312 {
    $resolved = Find-Python312
    if ($resolved) {
        Write-Host "Using Python 3.12: $resolved"
        return $resolved
    }

    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Python 3.12 is required and no runnable Python 3.12 interpreter was found. The Microsoft Store python app-execution alias does not count, and winget is not available to install Python automatically."
    }

    Write-Host "No runnable Python 3.12 interpreter found; installing Python 3.12..."
    $wingetOutput = @(winget install --id Python.Python.3.12 --exact --accept-package-agreements --accept-source-agreements 2>&1)
    $wingetExit = $LASTEXITCODE
    foreach ($line in $wingetOutput) { Write-Host $line }
    if ($wingetExit -ne 0) {
        throw "winget failed while installing Python 3.12 with exit code $wingetExit."
    }

    $env:PATH = [Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [Environment]::GetEnvironmentVariable("PATH","User")
    $resolved = Find-Python312
    if (-not $resolved) {
        throw "Python 3.12 installation completed, but a runnable interpreter still could not be located. The Windows Store app-execution alias may still be shadowing Python; the installer will not use that alias."
    }
    Write-Host "Using Python 3.12: $resolved"
    return $resolved
}

function Escape-SwarmValue([string]$Value) {
    if ($null -eq $Value) { return "" }
    return ($Value -replace '[,=\r\n]', '_').Trim()
}

function Find-CudaToolkitPath {
    if ($env:CUDA_PATH -and (Test-Path $env:CUDA_PATH)) { return $env:CUDA_PATH }

    $nvcc = Get-Command nvcc -ErrorAction SilentlyContinue
    if ($nvcc -and $nvcc.Source) {
        $candidate = Split-Path (Split-Path $nvcc.Source -Parent) -Parent
        if (Test-Path $candidate) { return $candidate }
    }

    $cudaRoot = Join-Path $env:ProgramFiles "NVIDIA GPU Computing Toolkit\CUDA"
    if (Test-Path $cudaRoot) {
        $candidate = Get-ChildItem $cudaRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^v\d+\.\d+$' } |
            Sort-Object { [version]($_.Name.TrimStart('v')) } -Descending |
            Select-Object -First 1
        if ($candidate) { return $candidate.FullName }
    }
    return $null
}

Write-Host "Compute Swarm automatic Windows worker installer"
Write-Host "Controller: $ControllerUrl"

Ensure-Command "git" "Git.Git"
$BasePython = Ensure-Python312

$uri = [Uri]$ControllerUrl
$AllowInsecure = 0
if ($uri.Scheme -eq 'http') {
    $privateHost = $uri.Host -match '^(localhost|127\.0\.0\.1|10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)'
    $trustedBuiltInController = $ControllerUrl.TrimEnd('/') -eq $TrustedDefaultController
    if ($privateHost -or $AllowInsecurePublicController -or $trustedBuiltInController) {
        $AllowInsecure = 1
        if ($trustedBuiltInController -and -not $privateHost) {
            Write-Warning "Using the configured Compute Swarm controller over plaintext HTTP: $TrustedDefaultController"
        }
    } else {
        throw "Public HTTP controller refused. Use HTTPS, or explicitly pass -AllowInsecurePublicController if you accept plaintext pairing credentials on the network."
    }
}

try {
    $health = Invoke-RestMethod -Method Get -Uri "$ControllerUrl/health" -TimeoutSec 15
    if (-not $health.ok) { throw "controller health response was not OK" }
} catch {
    throw "Cannot reach Compute Swarm controller at $ControllerUrl. $($_.Exception.Message)"
}

$CpuCores = 1
$MemoryMb = $null
try {
    $ComputerSystem = Get-CimInstance Win32_ComputerSystem
    if ($ComputerSystem.NumberOfLogicalProcessors -and [int]$ComputerSystem.NumberOfLogicalProcessors -gt 0) {
        $CpuCores = [int]$ComputerSystem.NumberOfLogicalProcessors
    }
    if ($ComputerSystem.TotalPhysicalMemory -and [uint64]$ComputerSystem.TotalPhysicalMemory -gt 0) {
        $MemoryMb = [int64][math]::Round([double]$ComputerSystem.TotalPhysicalMemory / 1MB)
    }
} catch {
    Write-Warning "CPU/RAM inventory through CIM failed: $($_.Exception.Message)"
    try {
        if ([int]$env:NUMBER_OF_PROCESSORS -gt 0) { $CpuCores = [int]$env:NUMBER_OF_PROCESSORS }
    } catch {}
}
Write-Host "Detected logical CPU processors: $CpuCores"
if ($MemoryMb) { Write-Host "Detected physical RAM: $MemoryMb MB" }

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

$CudaToolkitPath = $null
if ($HasNvidia) {
    $CudaToolkitPath = Find-CudaToolkitPath
    if ($CudaToolkitPath) {
        $env:CUDA_PATH = $CudaToolkitPath
        $cudaBin = Join-Path $CudaToolkitPath "bin"
        if (Test-Path $cudaBin) { $env:PATH = "$cudaBin;$env:PATH" }
        Write-Host "Detected system CUDA Toolkit: $CudaToolkitPath"
    } else {
        Write-Host "No system CUDA Toolkit detected; CuPy will use NVIDIA CUDA component wheels inside the worker environment."
    }
}

$RepoDir = Join-Path $InstallDir "repo"
$VenvDir = Join-Path $InstallDir "venv"
$ConfigDir = Join-Path $InstallDir "config"
$IdentityFile = Join-Path $ConfigDir "worker-identity.json"
New-Item -ItemType Directory -Force -Path $InstallDir, $ConfigDir | Out-Null

if (Test-Path (Join-Path $RepoDir ".git")) {
    git -C $RepoDir fetch origin $Branch
    if ($LASTEXITCODE -ne 0) { throw "git fetch failed." }
    git -C $RepoDir checkout $Branch
    if ($LASTEXITCODE -ne 0) { throw "git checkout failed." }
    git -C $RepoDir reset --hard "origin/$Branch"
    if ($LASTEXITCODE -ne 0) { throw "git reset failed." }
} else {
    git clone --branch $Branch --single-branch $RepoUrl $RepoDir
    if ($LASTEXITCODE -ne 0) { throw "git clone failed." }
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if ((Test-Path $VenvDir) -and -not (Test-Path $VenvPython -PathType Leaf)) {
    Write-Host "Removing incomplete Python virtual environment from the previous install attempt..."
    Remove-Item $VenvDir -Recurse -Force
}
& $BasePython -m venv $VenvDir
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPython -PathType Leaf)) {
    throw "Failed to create Python virtual environment using $BasePython."
}
$Python = $VenvPython
& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Failed to upgrade pip." }
& $Python -m pip install -r (Join-Path $RepoDir "worker\requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Failed to install base worker requirements." }
$UpdateRequirements = @("worker\requirements.txt")

if ($HasNvidia) {
    Write-Host "NVIDIA GPU detected; installing GPU inference backend..."
    & $Python -m pip install -r (Join-Path $RepoDir "worker\requirements-onnx-gpu.txt")
    if ($LASTEXITCODE -ne 0) { throw "Failed to install ONNX Runtime GPU backend." }
    $UpdateRequirements += "worker\requirements-onnx-gpu.txt"

    if ($CudaMajor -ge 13) {
        Write-Host "CUDA 13-compatible NVIDIA driver detected; installing CuPy CUDA 13 backend with CUDA component libraries..."
        & $Python -m pip install -r (Join-Path $RepoDir "worker\requirements-cuda13.txt")
        if ($LASTEXITCODE -ne 0) { throw "Failed to install CuPy CUDA 13 component toolkit." }
        $UpdateRequirements += "worker\requirements-cuda13.txt"
    } else {
        Write-Host "Installing CuPy CUDA 12 backend with CUDA component libraries (compatible default for NVIDIA Windows workers)..."
        & $Python -m pip install -r (Join-Path $RepoDir "worker\requirements-cuda12.txt")
        if ($LASTEXITCODE -ne 0) { throw "Failed to install CuPy CUDA 12 component toolkit." }
        $UpdateRequirements += "worker\requirements-cuda12.txt"
    }

    Write-Host "Testing CuPy CUDA execution..."
    $CuPyProbeCode = @'
import cupy as cp
count = cp.cuda.runtime.getDeviceCount()
if count < 1:
    raise RuntimeError("CuPy reported no CUDA devices")
x = cp.arange(8, dtype=cp.float32)
y = x * x
cp.cuda.Stream.null.synchronize()
value = float(cp.asnumpy(y.sum()))
if abs(value - 140.0) > 1e-4:
    raise RuntimeError(f"CuPy validation returned unexpected result: {value}")
print(f"CuPy CUDA ready: {count} device(s), validation={value:.1f}")
'@
    $CuPyProbe = (& $Python -c $CuPyProbeCode 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "CuPy CUDA self-test failed after installing the bundled CUDA component libraries. $CuPyProbe"
    }
    Write-Host $CuPyProbe
} else {
    Write-Host "No NVIDIA CUDA adapter detected; installing CPU ONNX backend."
    & $Python -m pip install -r (Join-Path $RepoDir "worker\requirements-onnx.txt")
    if ($LASTEXITCODE -ne 0) { throw "Failed to install CPU ONNX backend." }
    $UpdateRequirements += "worker\requirements-onnx.txt"
}

$GpuVendor = if ($HasNvidia) { "nvidia" } elseif ($HasAmd) { "amd" } elseif ($HasIntel) { "intel" } else { "unknown" }
$GpuName = Escape-SwarmValue (($GpuControllers -join " + "))
$MemoryLabel = if ($MemoryMb) { [string]$MemoryMb } else { "" }
$Labels = "gpu_vendor=$(Escape-SwarmValue $GpuVendor),gpu_name=$GpuName,cpu_cores=$CpuCores,memory_mb=$MemoryLabel,installer=windows-auto"

if (-not (Test-Path $IdentityFile)) {
    $OsCaption = "Windows"
    try { $OsCaption = (Get-CimInstance Win32_OperatingSystem).Caption } catch {}
    $PairBody = @{
        name = $env:COMPUTERNAME
        os_name = "Windows"
        platform = $OsCaption
        arch = $env:PROCESSOR_ARCHITECTURE
        gpu_name = ($GpuControllers -join " + ")
        labels = @{
            gpu_vendor = [string]$GpuVendor
            gpu_name = [string]$GpuName
            cpu_cores = [string]$CpuCores
            memory_mb = [string]$MemoryLabel
            installer = "windows-auto"
        }
    }

    Write-Host ""
    Write-Host "Requesting permission to join the swarm..."
    $Pairing = Invoke-RestMethod -Method Post -Uri "$ControllerUrl/pairing/request" -ContentType "application/json" -Body ($PairBody | ConvertTo-Json -Depth 5) -TimeoutSec 20
    if (-not $Pairing.request_id -or -not $Pairing.claim_secret) {
        throw "Controller did not return a valid pairing request. Make sure the controller has the pairing update installed."
    }

    Write-Host "Pairing request sent for $env:COMPUTERNAME."
    Write-Host "Approve this device in the controller dashboard: $ControllerUrl/"
    Write-Host "Waiting for controller approval..."

    $deadline = (Get-Date).AddMinutes($PairingTimeoutMinutes)
    $encodedSecret = [Uri]::EscapeDataString([string]$Pairing.claim_secret)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        $PairStatus = Invoke-RestMethod -Method Get -Uri "$ControllerUrl/pairing/request/$($Pairing.request_id)?secret=$encodedSecret" -TimeoutSec 20
        if ($PairStatus.status -eq "approved") {
            if (-not $PairStatus.worker_id -or -not $PairStatus.device_token) {
                throw "Controller approved the request but did not return worker credentials."
            }
            @{
                worker_id = [string]$PairStatus.worker_id
                device_token = [string]$PairStatus.device_token
            } | ConvertTo-Json | Set-Content -Encoding ASCII $IdentityFile
            Write-Host "Controller approved this worker. Identity received."
            break
        }
        if ($PairStatus.status -eq "denied") { throw "The controller denied this device's join request." }
        if ($PairStatus.status -eq "expired") { throw "The pairing request expired before it was approved." }
    }
    if (-not (Test-Path $IdentityFile)) {
        throw "Timed out waiting for controller approval after $PairingTimeoutMinutes minutes. Rerun the installer to create a new request."
    }
} else {
    Write-Host "Existing controller-issued worker identity found; pairing approval is not required again."
}

$EnvFile = Join-Path $ConfigDir "worker.env.ps1"
$SafeController = $ControllerUrl.Replace("'", "''")
$SafeLabels = $Labels.Replace("'", "''")
$SafeIdentity = $IdentityFile.Replace("'", "''")
$EnvLines = @(
    "`$env:SWARM_CONTROLLER_URL = '$SafeController'",
    "`$env:SWARM_ALLOW_INSECURE_REMOTE = '$AllowInsecure'",
    "`$env:SWARM_LABELS = '$SafeLabels'",
    "`$env:SWARM_IDENTITY_FILE = '$SafeIdentity'"
)
if ($CudaToolkitPath) {
    $SafeCudaPath = $CudaToolkitPath.Replace("'", "''")
    $EnvLines += "`$env:CUDA_PATH = '$SafeCudaPath'"
    $EnvLines += "`$env:PATH = '$SafeCudaPath\bin;' + `$env:PATH"
}
$EnvLines | Set-Content -Encoding UTF8 $EnvFile

$Runner = Join-Path $InstallDir "run-worker.ps1"
@"
`$ErrorActionPreference = 'Stop'
. '$EnvFile'
& '$Python' '$RepoDir\worker\worker.py'
"@ | Set-Content -Encoding UTF8 $Runner

Write-Host "Testing worker initialization, hardware registration, and accelerator discovery..."
. $EnvFile
$ProbeStdout = Join-Path $ConfigDir "worker-probe.stdout.log"
$ProbeStderr = Join-Path $ConfigDir "worker-probe.stderr.log"
Remove-Item $ProbeStdout, $ProbeStderr -Force -ErrorAction SilentlyContinue
$Probe = Start-Process -FilePath $Python -ArgumentList @("-u", (Join-Path $RepoDir "worker\worker.py")) -PassThru -WindowStyle Hidden -RedirectStandardOutput $ProbeStdout -RedirectStandardError $ProbeStderr
$ProbeDeadline = (Get-Date).AddSeconds(45)
$Registered = $false
while ((Get-Date) -lt $ProbeDeadline) {
    Start-Sleep -Seconds 1
    if (Test-Path $ProbeStdout) {
        $ProbeOutput = Get-Content $ProbeStdout -Raw -ErrorAction SilentlyContinue
        if ($ProbeOutput -match 'joined swarm as ') {
            $Registered = $true
            break
        }
    }
    if ($Probe.HasExited) { break }
}
$ProbeOutput = if (Test-Path $ProbeStdout) { ([string](Get-Content $ProbeStdout -Raw -ErrorAction SilentlyContinue)).Trim() } else { "" }
$ProbeError = if (Test-Path $ProbeStderr) { ([string](Get-Content $ProbeStderr -Raw -ErrorAction SilentlyContinue)).Trim() } else { "" }
if (-not $Registered) {
    if (-not $Probe.HasExited) { Stop-Process -Id $Probe.Id -Force -ErrorAction SilentlyContinue }
    throw "Worker did not complete controller registration. stdout: $ProbeOutput stderr: $ProbeError"
}
if ($ProbeOutput) { Write-Host $ProbeOutput }
if (-not $Probe.HasExited) { Stop-Process -Id $Probe.Id -Force -ErrorAction SilentlyContinue }

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
Write-Host "Compute Swarm worker installed, approved, registered, and initialized."
Write-Host "Controller: $ControllerUrl"
Write-Host "Python: $BasePython"
Write-Host "CPU logical processors: $CpuCores"
if ($MemoryMb) { Write-Host "RAM: $MemoryMb MB" }
Write-Host "GPU vendor: $GpuVendor"
Write-Host "GPU(s): $($GpuControllers -join '; ')"
if ($HasNvidia) { Write-Host "CuPy CUDA: validated" }
Write-Host "Worker task: $TaskName"
Write-Host "Auto-update task: $UpdateTaskName"
Write-Host "Install directory: $InstallDir"