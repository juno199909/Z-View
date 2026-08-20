param(
    [ValidateSet('Start', 'Stop', 'Restart')]
    [string]$Action = 'Start',
    [switch]$OpenBrowser,
    [switch]$InstallDependencies
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppRootCandidates = @(
    (Join-Path $ScriptRoot 'IT2026\IT2026'),
    (Join-Path $ScriptRoot 'IT2026')
)
$AppRoot = $null
foreach ($candidate in $AppRootCandidates) {
    if ((Test-Path -LiteralPath (Join-Path $candidate 'assets_api.py')) -and
        (Test-Path -LiteralPath (Join-Path $candidate 'software_management_api_complete_v2.py')) -and
        (Test-Path -LiteralPath (Join-Path $candidate 'software_policy_api.py')) -and
        (Test-Path -LiteralPath (Join-Path $candidate 'frontend\package.json'))) {
        $AppRoot = (Resolve-Path -LiteralPath $candidate).Path
        break
    }
}

if (-not $AppRoot) {
    throw 'Unable to locate the application root.'
}

$LogRoot = Join-Path $env:TEMP 'ZView'
$LogDir = Join-Path $LogRoot 'logs'
$StateFile = Join-Path $LogRoot 'platform-processes.json'

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Get-CommandPath {
    param(
        [Parameter(Mandatory)]
        [string]$Name
    )

    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "Required command not found: $Name"
    }

    $source = $cmd.Source
    if ($Name -eq 'npm' -and $source -like '*.ps1') {
        $cmdVariant = [System.IO.Path]::ChangeExtension($source, '.cmd')
        if (Test-Path -LiteralPath $cmdVariant) {
            return $cmdVariant
        }
    }

    return $source
}

function Wait-Port {
    param(
        [Parameter(Mandatory)]
        [int]$Port,
        [int]$TimeoutSeconds = 60
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $client = [System.Net.Sockets.TcpClient]::new()
            try {
                $iar = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
                if ($iar.AsyncWaitHandle.WaitOne(500)) {
                    $client.EndConnect($iar)
                    return $true
                }
            } finally {
                $client.Close()
            }
        } catch {
        }
        Start-Sleep -Milliseconds 500
    }

    return $false
}

function Stop-Tree {
    param(
        [Parameter(Mandatory)]
        [int]$ProcessId
    )

    if ($ProcessId -le 0) {
        return
    }

    try {
        $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if ($proc) {
            & taskkill /PID $ProcessId /T /F | Out-Null
        }
    } catch {
    }
}

function Load-State {
    if (-not (Test-Path -LiteralPath $StateFile)) {
        return @()
    }

    try {
        $state = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
        if ($state -is [System.Array]) {
            return @($state)
        }
        if ($state) {
            return @($state)
        }
    } catch {
    }

    return @()
}

function Save-State {
    param([object[]]$Entries)
    $Entries | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $StateFile -Encoding UTF8
}

function Stop-OrphanedManagedProcesses {
    $managedScriptPattern = '(^|\s)(assets_api\.py|software_management_api_complete_v2\.py|software_policy_api\.py)(\s|$)'
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | ForEach-Object {
        if ($_.CommandLine -and $_.CommandLine -match $managedScriptPattern) {
            Stop-Tree -ProcessId ([int]$_.ProcessId)
        }
    }
}

function Stop-ManagedPlatform {
    $entries = Load-State
    foreach ($entry in ($entries | Sort-Object @{ Expression = { $_.name -eq 'frontend' }; Descending = $true })) {
        if ($entry.pid) {
            Stop-Tree -ProcessId ([int]$entry.pid)
        }
    }
    Stop-OrphanedManagedProcesses
    if (Test-Path -LiteralPath $StateFile) {
        Remove-Item -LiteralPath $StateFile -Force
    }
}

function Test-PythonDeps {
    param([string]$PythonExe)

    & $PythonExe -c "import fastapi,uvicorn,mysql.connector,requests,websockets" | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Start-ManagedProcess {
    param(
        [Parameter(Mandatory)]
        [string]$Name,
        [Parameter(Mandatory)]
        [string]$FilePath,
        [Parameter(Mandatory)]
        [string[]]$ArgumentList,
        [Parameter(Mandatory)]
        [string]$WorkingDirectory,
        [Parameter(Mandatory)]
        [int]$Port,
        [Parameter(Mandatory)]
        [string]$StdOutLog,
        [Parameter(Mandatory)]
        [string]$StdErrLog
    )

    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden `
        -PassThru `
        -RedirectStandardOutput $StdOutLog `
        -RedirectStandardError $StdErrLog

    if (-not (Wait-Port -Port $Port -TimeoutSeconds 60)) {
        Stop-Tree -ProcessId $process.Id
        throw "$Name did not become ready on port $Port."
    }

    return [pscustomobject]@{
        name = $Name
        pid = $process.Id
        port = $Port
        stdout = $StdOutLog
        stderr = $StdErrLog
        command = $FilePath
        args = ($ArgumentList -join ' ')
    }
}

if ($Action -eq 'Stop') {
    Stop-ManagedPlatform
    Write-Host 'Platform stopped.'
    return
}

if ($Action -eq 'Restart') {
    Stop-ManagedPlatform
}

$PythonExe = Get-CommandPath -Name 'python'
$NpmExe = Get-CommandPath -Name 'npm'
$NodeExe = Get-CommandPath -Name 'node'

$RequirementsPath = Join-Path $AppRoot 'requirements.txt'
$FrontendRoot = Join-Path $AppRoot 'frontend'
$ViteEntrypoint = Join-Path $FrontendRoot 'node_modules\vite\bin\vite.js'

if ($InstallDependencies -or -not (Test-PythonDeps -PythonExe $PythonExe)) {
    Write-Host 'Installing/updating Python dependencies...'
    & $PythonExe -m pip install -r $RequirementsPath
}

if ($InstallDependencies -or -not (Test-Path -LiteralPath (Join-Path $FrontendRoot 'node_modules'))) {
    Write-Host 'Installing/updating frontend dependencies...'
    Push-Location $FrontendRoot
    try {
        & $NpmExe install --no-fund --no-audit
    } finally {
        Pop-Location
    }
}

if (-not (Test-Path -LiteralPath $ViteEntrypoint -PathType Leaf)) {
    throw "Vite entrypoint not found after dependency setup: $ViteEntrypoint"
}

Stop-ManagedPlatform
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$processes = @()
$processes += Start-ManagedProcess `
    -Name 'assets-api' `
    -FilePath $PythonExe `
    -ArgumentList @('assets_api.py') `
    -WorkingDirectory $AppRoot `
    -Port 8080 `
    -StdOutLog (Join-Path $LogDir 'assets-api.out.log') `
    -StdErrLog (Join-Path $LogDir 'assets-api.err.log')

$processes += Start-ManagedProcess `
    -Name 'software-api' `
    -FilePath $PythonExe `
    -ArgumentList @('software_management_api_complete_v2.py') `
    -WorkingDirectory $AppRoot `
    -Port 8081 `
    -StdOutLog (Join-Path $LogDir 'software-api.out.log') `
    -StdErrLog (Join-Path $LogDir 'software-api.err.log')

$processes += Start-ManagedProcess `
    -Name 'policy-api' `
    -FilePath $PythonExe `
    -ArgumentList @('software_policy_api.py') `
    -WorkingDirectory $AppRoot `
    -Port 8082 `
    -StdOutLog (Join-Path $LogDir 'policy-api.out.log') `
    -StdErrLog (Join-Path $LogDir 'policy-api.err.log')

$processes += Start-ManagedProcess `
    -Name 'frontend' `
    -FilePath $NodeExe `
        -ArgumentList @($ViteEntrypoint, 'preview', '--host', '0.0.0.0') `
    -WorkingDirectory $FrontendRoot `
    -Port 5173 `
    -StdOutLog (Join-Path $LogDir 'frontend.out.log') `
    -StdErrLog (Join-Path $LogDir 'frontend.err.log')

Save-State -Entries $processes

Write-Host 'Platform started.'
foreach ($entry in $processes) {
    Write-Host ("{0}: pid={1} port={2}" -f $entry.name, $entry.pid, $entry.port)
}
Write-Host ("Logs: {0}" -f $LogDir)
Write-Host 'Frontend: http://localhost:5173'
Write-Host 'API: http://localhost:8080/docs'

if ($OpenBrowser) {
    Start-Process 'http://localhost:5173' | Out-Null
}
