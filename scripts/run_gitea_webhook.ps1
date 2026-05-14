[CmdletBinding()]
param(
    [string]$EnvFile = ".env.gitea.local",
    [int]$Port,
    [string]$Python
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path

function Import-EnvFile {
    param(
        [string]$Path
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return
    }

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    $lineNumber = 0
    foreach ($line in Get-Content -LiteralPath $Path) {
        $lineNumber++
        $trimmed = $line.Trim()

        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }

        if ($trimmed -notmatch "^([A-Za-z_][A-Za-z0-9_]*(?:__[A-Za-z0-9_]+)*)=(.*)$") {
            throw "Invalid env entry at ${Path}:${lineNumber}. Expected KEY=value with double-underscore config names."
        }

        $name = $Matches[1]
        $value = $Matches[2].Trim()

        if (
            $value.Length -ge 2 -and (
                ($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'"))
            )
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        Set-Item -Path "Env:$name" -Value $value
    }
}

function Require-EnvVars {
    param(
        [string[]]$Names
    )

    $missing = @($Names | Where-Object { -not [Environment]::GetEnvironmentVariable($_) })
    if ($missing.Count -gt 0) {
        throw "Missing required environment variables: $($missing -join ', '). Copy .env.gitea.local.example to .env.gitea.local and fill them in."
    }
}

if ([System.IO.Path]::IsPathRooted($EnvFile)) {
    $envFilePath = $EnvFile
} else {
    $envFilePath = Join-Path $repoRoot $EnvFile
}

Import-EnvFile -Path $envFilePath

if (-not $env:CONFIG__GIT_PROVIDER) {
    $env:CONFIG__GIT_PROVIDER = "gitea"
}

if ($env:CONFIG__GIT_PROVIDER -ne "gitea") {
    throw "CONFIG__GIT_PROVIDER must be 'gitea' for the Gitea webhook server."
}

Require-EnvVars -Names @(
    "GITEA__URL",
    "GITEA__PERSONAL_ACCESS_TOKEN",
    "GITEA__WEBHOOK_SECRET",
    "OPENAI__KEY"
)

if ($PSBoundParameters.ContainsKey("Port")) {
    if ($Port -lt 1 -or $Port -gt 65535) {
        throw "Port must be between 1 and 65535."
    }
    $env:PORT = [string]$Port
} elseif (-not $env:PORT) {
    $env:PORT = "3000"
}

if (-not $Python) {
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        $Python = $venvPython
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $Python = (Get-Command python).Source
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $Python = (Get-Command py).Source
    } else {
        throw "Python was not found. Install Python 3.12+, then run: python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt; pip install -e ."
    }
}

$pythonVersion = (& $Python --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Failed to run Python executable '$Python'. Pass a valid executable with -Python <path>."
}

$pythonVersionInfo = (& $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Failed to inspect Python version from '$Python'."
}

$pythonVersionParts = $pythonVersionInfo.Split(".")
if ([int]$pythonVersionParts[0] -lt 3 -or ([int]$pythonVersionParts[0] -eq 3 -and [int]$pythonVersionParts[1] -lt 12)) {
    throw "Python 3.12+ is required by pyproject.toml. Found $pythonVersion."
}

$previousLocation = Get-Location
try {
    Set-Location $repoRoot
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$repoRoot;$($env:PYTHONPATH)" } else { $repoRoot }

    Write-Host "Starting PR-Agent Gitea webhook server on port $($env:PORT)."
    Write-Host "Using $pythonVersion."
    Write-Host "Local test URL: http://127.0.0.1:$($env:PORT)/docs"

    $addresses = @()
    try {
        $addresses = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object { $_.IPAddress -notlike "127.*" -and $_.PrefixOrigin -ne "WellKnown" } |
            Select-Object -ExpandProperty IPAddress)
    } catch {
        $addresses = @()
    }

    if ($addresses.Count -gt 0) {
        Write-Host "Candidate Gitea webhook URLs:"
        foreach ($address in $addresses) {
            Write-Host "  http://${address}:$($env:PORT)/api/v1/gitea_webhooks"
        }
    } else {
        Write-Host "Configure Gitea webhook URL as: http://<this-host>:$($env:PORT)/api/v1/gitea_webhooks"
    }

    & $Python "pr_agent/servers/gitea_app.py"
    exit $LASTEXITCODE
} finally {
    Set-Location $previousLocation
}
