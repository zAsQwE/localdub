param([ValidateSet('Run', 'Install', 'Check')][string]$Action = 'Run', [switch]$Piper,
      [Parameter(ValueFromRemainingArguments=$true)][string[]]$AppArguments)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Text.UTF8Encoding]::new($false)
try { [Console]::InputEncoding = $OutputEncoding } catch { }
try { [Console]::OutputEncoding = $OutputEncoding } catch { }
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$Root = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'windows-tools.ps1')
try {
    $python = Find-LocalDubPython
    if ($Action -eq 'Run') {
        if (-not $python) { throw 'Не найден Python 3.10+. Запустите install-windows.bat.' }
        foreach ($tool in @((Find-LocalDubDotnet), (Find-LocalDubEspeak))) {
            if ($tool) { $env:PATH = (Split-Path $tool -Parent) + [IO.Path]::PathSeparator + $env:PATH }
        }
        & $python (Join-Path $Root 'localdub.py') @AppArguments
        exit $LASTEXITCODE
    }
    if ($Action -eq 'Check') {
        Write-Host "Python: $python"
        Write-Host ".NET 8: $(Find-LocalDubDotnet)"
        Write-Host "eSpeak NG: $(Find-LocalDubEspeak)"
        if (-not $python -or -not (Find-LocalDubDotnet)) { exit 1 }
        exit 0
    }
    $Piper = $Piper -or ($AppArguments -contains 'piper')
    if (-not $python) {
        Install-LocalDubPackage 'Python.Python.3.12' 'https://www.python.org/downloads/windows/'
        $python = Find-LocalDubPython
        if (-not $python) { throw 'Python 3.10+ не найден после установки.' }
    }
    Write-Host "Использую Python: $python"
    $dotnet = Find-LocalDubDotnet
    if (-not $dotnet) {
        Install-LocalDubPackage 'Microsoft.DotNet.Runtime.8' 'https://dotnet.microsoft.com/download/dotnet/8.0'
        $dotnet = Find-LocalDubDotnet
        if (-not $dotnet) { throw '.NET Runtime 8 не найден после установки.' }
    }
    Write-Host "Использую .NET 8: $dotnet"
    $espeak = Find-LocalDubEspeak
    if (-not $espeak -and -not $Piper) {
        Install-LocalDubPackage 'eSpeak-NG.eSpeak-NG' 'https://github.com/espeak-ng/espeak-ng/releases'
        $espeak = Find-LocalDubEspeak
        if (-not $espeak) { throw 'eSpeak NG не найден после установки.' }
    }
    if ($espeak) { Write-Host "Использую eSpeak NG: $espeak" }
    if ($Piper) {
        if (-not (Read-LocalDubTool $python @('-c', 'import piper'))) {
            $venv = Join-Path $Root '.venv'
            $venvPython = Join-Path $venv 'Scripts\python.exe'
            if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
                & $python -m venv $venv
                if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать окружение Piper.' }
            }
            $python = $venvPython
            & $python -m pip install piper-tts
            if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить Piper.' }
            if (-not (Read-LocalDubTool $python @('-c', 'import piper'))) { throw 'Piper не запускается после установки.' }
        }
        Write-Host 'Piper готов. Голос можно скачать в настройках LocalDub.'
    }
    Write-Host 'Готово. Запустите run.bat — открывать новую консоль не нужно.'
    exit 0
} catch {
    Write-Host "LocalDub: $_"
    exit 1
}
