# Windows PowerShell 5.1: dependency discovery shared by install and run.
function Read-LocalDubTool([string]$File, [string[]]$Arguments) {
    $ErrorActionPreference = 'SilentlyContinue'
    $PSNativeCommandUseErrorActionPreference = $false
    $global:LASTEXITCODE = -1
    try {
        $output = @(& $File @Arguments 2>$null)
        if ($global:LASTEXITCODE -eq 0) { return @{text=($output -join "`n")} }
    } catch { }
    return $null
}

function Find-LocalDubPython {
    $candidates = @((Join-Path $Root '.venv\Scripts\python.exe'))
    $py = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue
    if ($py) {
        $probe = Read-LocalDubTool $py.Source @('-3', '-c', 'import sys; print(sys.executable)')
        if ($probe) { $candidates += $probe.text.Trim() }
    }
    foreach ($key in @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore', 'HKLM:\Software\WOW6432Node\Python\PythonCore')) {
        foreach ($version in @(Get-ChildItem -LiteralPath $key -ErrorAction SilentlyContinue)) {
            $value = Get-ItemProperty ($version.PSPath + '\InstallPath') -ErrorAction SilentlyContinue
            if ($value.'(default)') { $candidates += Join-Path $value.'(default)' 'python.exe' }
        }
    }
    foreach ($base in @($env:LOCALAPPDATA, $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($base) {
            $candidates += @(Get-ChildItem -Path (Join-Path $base 'Programs\Python\Python*\python.exe'),
                (Join-Path $base 'Python*\python.exe') -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
        }
    }
    $candidates += @(Get-Command python.exe, python3.exe -CommandType Application -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
    foreach ($file in @($candidates | Where-Object { $_ } | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { continue }
        $probe = Read-LocalDubTool $file @('-c', 'import sys; assert sys.version_info >= (3,10); print(sys.executable)')
        if ($probe -and (Test-Path -LiteralPath $probe.text.Trim() -PathType Leaf)) { return $probe.text.Trim() }
    }
    return $null
}

function Find-LocalDubDotnet {
    $candidates = @()
    $candidates += @(Get-Command dotnet.exe -CommandType Application -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
    foreach ($base in @($env:DOTNET_ROOT, $env:ProgramFiles, $env:LOCALAPPDATA, $env:USERPROFILE)) {
        if ($base) {
            $candidates += Join-Path $base 'dotnet.exe'
            $candidates += Join-Path $base 'dotnet\dotnet.exe'
            $candidates += Join-Path $base 'Microsoft\dotnet\dotnet.exe'
            $candidates += Join-Path $base '.dotnet\dotnet.exe'
        }
    }
    foreach ($file in @($candidates | Where-Object { $_ } | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { continue }
        $probe = Read-LocalDubTool $file @('--list-runtimes')
        if ($probe -and $probe.text -match '(?m)^Microsoft\.NETCore\.App 8\.') { return $file }
    }
    return $null
}

function Find-LocalDubEspeak {
    $candidates = @()
    $candidates += @(Get-Command espeak-ng.exe -CommandType Application -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
        if ($base) { $candidates += Join-Path $base 'eSpeak NG\espeak-ng.exe' }
    }
    foreach ($file in @($candidates | Where-Object { $_ } | Select-Object -Unique)) {
        if ((Test-Path -LiteralPath $file -PathType Leaf) -and (Read-LocalDubTool $file @('--version'))) { return $file }
    }
    return $null
}

function Install-LocalDubPackage([string]$Id, [string]$Guide) {
    $winget = Get-Command winget.exe -CommandType Application -ErrorAction SilentlyContinue
    if (-not $winget) { throw "Не найден winget. Установите $Id по инструкции: $Guide" }
    Write-Host "Устанавливаю $Id..."
    & $winget.Source install --exact --id $Id --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -notin @(0, 3010)) { throw "Не удалось установить $Id. Инструкция: $Guide" }
    $env:PATH = $env:PATH + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}
