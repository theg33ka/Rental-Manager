param(
    [switch]$Configure,
    [string]$Url,
    [ValidateSet('inspect', 'propose', 'proposals', 'confirm')]
    [string]$Command = 'proposals',
    [int]$ApartmentId,
    [string]$PlanFile,
    [int]$ProposalId,
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
$credentialRoot = Join-Path $env:LOCALAPPDATA 'RentalManager\maintenance'
$credentialPath = Join-Path $credentialRoot 'owner.xml'
$configPath = Join-Path $credentialRoot 'host.json'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path

if ($Configure) {
    $hostUri = $null
    if (-not [Uri]::TryCreate($Url, [UriKind]::Absolute, [ref]$hostUri) -or
        $hostUri.Scheme -ne 'https' -or $hostUri.UserInfo -or
        $hostUri.AbsolutePath -ne '/' -or $hostUri.Query -or $hostUri.Fragment) {
        throw 'Укажите HTTPS origin хоста без пути, параметров и пароля.'
    }
    New-Item -ItemType Directory -Path $credentialRoot -Force | Out-Null
    $securePin = Read-Host 'PIN владельца (сохраняется через Windows DPAPI для текущего пользователя)' -AsSecureString
    $credential = New-Object System.Management.Automation.PSCredential('owner', $securePin)
    $credential | Export-Clixml -LiteralPath $credentialPath
    @{ url = $hostUri.GetLeftPart([UriPartial]::Authority) } | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8
    Write-Output 'Локальный доступ сохранён. Для проверки выполните -Command inspect -ApartmentId <id>.'
    return
}

if (-not (Test-Path -LiteralPath $credentialPath) -or -not (Test-Path -LiteralPath $configPath)) {
    throw 'Сначала выполните host.ps1 -Configure -Url https://ваш-хост и введите PIN в локальном окне.'
}
if ($Url) { throw 'Хост привязан к сохранённому доступу. Для его смены выполните -Configure.' }
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$credential = Import-Clixml -LiteralPath $credentialPath
$clientArgs = @('--url', $config.url, $Command)
switch ($Command) {
    'inspect' {
        if ($ApartmentId -le 0) { throw 'Нужен -ApartmentId.' }
        $clientArgs += [string]$ApartmentId
    }
    'propose' {
        if (-not $PlanFile) { throw 'Нужен -PlanFile с operation, arguments и reason.' }
        $clientArgs += (Resolve-Path -LiteralPath $PlanFile).Path
    }
    'confirm' {
        if ($ProposalId -le 0 -or -not $Apply) { throw 'Нужны -ProposalId и явный флаг -Apply.' }
        $clientArgs += @([string]$ProposalId, '--apply')
    }
}
$previousPin = $env:RENTAL_MAINTENANCE_PIN
try {
    $env:RENTAL_MAINTENANCE_PIN = $credential.GetNetworkCredential().Password
    & (Join-Path $projectRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'host_client.py') @clientArgs
    if ($LASTEXITCODE -ne 0) { throw 'Операция на хосте не выполнена.' }
} finally {
    $env:RENTAL_MAINTENANCE_PIN = $previousPin
    $credential = $null
}
