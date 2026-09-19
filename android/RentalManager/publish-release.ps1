param(
    [string]$SdkRoot = "$env:LOCALAPPDATA\Android\Sdk",
    [string]$BuildToolsVersion = "35.0.0",
    [string]$ApkPath = "",
    [string]$PreviousApkPath = "",
    [string]$NotesPath = "",
    [switch]$PrepareOnly
)

$ErrorActionPreference = "Stop"
$Repository = "theg33ka/Rental-Manager"
$Root = (Resolve-Path -LiteralPath $PSScriptRoot).ProviderPath
$RepoRoot = [IO.Path]::GetFullPath((Join-Path $Root "..\.."))
$BuildRoot = Join-Path $Root "build"
$BuildTools = Join-Path $SdkRoot "build-tools\$BuildToolsVersion"

function Read-Native([string]$Command, [string[]]$Arguments) {
    $Output = & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Command (exit $LASTEXITCODE)" }
    return ($Output -join "`n").Trim()
}

function Read-ApkMetadata([string]$Path) {
    $Badging = Read-Native (Join-Path $BuildTools "aapt.exe") @("dump", "badging", $Path)
    $PackageMatch = [regex]::Match($Badging, "(?m)^package: name='([^']+)' versionCode='([0-9]+)' versionName='([^']+)'")
    $SdkMatch = [regex]::Match($Badging, "(?m)^sdkVersion:'([0-9]+)'")
    if (!$PackageMatch.Success -or !$SdkMatch.Success) { throw "Cannot read APK package, version or minimum SDK" }
    $Certificates = Read-Native (Join-Path $BuildTools "apksigner.bat") @("verify", "--print-certs", $Path)
    $Digests = @([regex]::Matches($Certificates, '(?m)^Signer #[0-9]+ certificate SHA-256 digest: ([a-fA-F0-9]{64})\s*$') | ForEach-Object { $_.Groups[1].Value.ToLowerInvariant() } | Sort-Object)
    if ($Digests.Count -ne 1) { throw "Expected a verified APK with one signing certificate" }
    return @{
        Package = $PackageMatch.Groups[1].Value
        VersionCode = [long]$PackageMatch.Groups[2].Value
        VersionName = $PackageMatch.Groups[3].Value
        MinSdk = [int]$SdkMatch.Groups[1].Value
        Signer = $Digests[0]
    }
}

[xml]$Manifest = Get-Content -LiteralPath (Join-Path $Root "app\src\main\AndroidManifest.xml")
$AndroidNamespace = "http://schemas.android.com/apk/res/android"
$VersionName = $Manifest.manifest.GetAttribute("versionName", $AndroidNamespace)
$VersionCode = [long]$Manifest.manifest.GetAttribute("versionCode", $AndroidNamespace)
$MinSdk = [int]$Manifest.manifest.'uses-sdk'.GetAttribute("minSdkVersion", $AndroidNamespace)
if ($VersionName -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or $VersionCode -le 0) {
    throw "Invalid Android release version"
}
$AssetName = "rental-manager-mobile-$VersionName.apk"
if (!$ApkPath) { $ApkPath = Join-Path $BuildRoot $AssetName }
$ApkPath = (Resolve-Path -LiteralPath $ApkPath).ProviderPath
if ([IO.Path]::GetFileName($ApkPath) -cne $AssetName) { throw "Use the versioned APK filename: $AssetName" }
$Apk = Read-ApkMetadata $ApkPath
if ($Apk.Package -cne "ru.rentalmanager.mobile" -or $Apk.Package -cne $Manifest.manifest.package -or
    $Apk.VersionCode -ne $VersionCode -or $Apk.VersionName -cne $VersionName -or $Apk.MinSdk -ne $MinSdk) {
    throw "APK metadata does not match the current Android manifest; rebuild before publishing"
}
if ($PreviousApkPath) {
    $Previous = Read-ApkMetadata (Resolve-Path -LiteralPath $PreviousApkPath).ProviderPath
    if ($Previous.Package -cne $Apk.Package -or $Previous.Signer -cne $Apk.Signer) {
        throw "The APK cannot update the previous package: signing certificate or package differs"
    }
    if ($Apk.VersionCode -le $Previous.VersionCode) { throw "The new versionCode must exceed the previous APK versionCode" }
}

$Tag = "android-v$VersionName"
$ReleaseUrl = "https://github.com/$Repository/releases/download/$Tag"
$Digest = (Get-FileHash -LiteralPath $ApkPath -Algorithm SHA256).Hash.ToLowerInvariant()
$Metadata = [ordered]@{
    available = $true
    version_code = $VersionCode
    version_name = $VersionName
    package_name = $Apk.Package
    min_sdk = $MinSdk
    download_url = "$ReleaseUrl/$AssetName"
    sha256 = $Digest
    size_bytes = (Get-Item -LiteralPath $ApkPath).Length
}
if ((Get-Item -LiteralPath $BuildRoot).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw "The build directory must not be a symbolic link or junction"
}
$MetadataPath = Join-Path $BuildRoot "android-update.json"
if ((Test-Path -LiteralPath $MetadataPath) -and ((Get-Item -LiteralPath $MetadataPath).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw "The update manifest must not be a symbolic link"
}
[IO.File]::WriteAllText($MetadataPath, ($Metadata | ConvertTo-Json) + "`n", (New-Object Text.UTF8Encoding($false)))
if ($PrepareOnly) {
    Write-Output "Verified Android $VersionName ($VersionCode); generated $MetadataPath. Nothing was published."
    return
}
if (!$NotesPath -or !(Test-Path -LiteralPath $NotesPath -PathType Leaf)) {
    throw "Supply -NotesPath with the reviewed release notes; store temporary notes under build"
}
$NotesPath = (Resolve-Path -LiteralPath $NotesPath).ProviderPath

$Branch = Read-Native "git" @("-C", $RepoRoot, "branch", "--show-current")
$Status = Read-Native "git" @("-C", $RepoRoot, "status", "--porcelain")
if ($Branch -cne "main" -or $Status) { throw "Publishing requires clean, committed main" }
$Commit = Read-Native "git" @("-C", $RepoRoot, "rev-parse", "HEAD")
$Origin = Read-Native "git" @("-C", $RepoRoot, "remote", "get-url", "origin")
if ($Origin -notmatch '^https://github\.com/theg33ka/Rental-Manager(?:\.git)?/?$' -and
    $Origin -notmatch '^git@github\.com:theg33ka/Rental-Manager(?:\.git)?$') {
    throw "origin must point to the configured GitHub release repository"
}
$RemoteMain = Read-Native "git" @("-C", $RepoRoot, "ls-remote", "origin", "refs/heads/main")
if (($RemoteMain -split '\s+')[0] -cne $Commit) { throw "Push main before publishing; live origin/main does not match HEAD" }
$RepositoryInfo = (Read-Native "gh" @("repo", "view", $Repository, "--json", "isPrivate")) | ConvertFrom-Json
if ($RepositoryInfo.isPrivate) { throw "Automatic updates require a public release repository" }
$TagRefs = Read-Native "git" @("-C", $RepoRoot, "ls-remote", "origin", "refs/tags/$Tag", "refs/tags/$Tag^{}")
if ($TagRefs) {
    $RefLines = @($TagRefs -split "`n")
    $Peeled = @($RefLines | Where-Object { $_.EndsWith("^{}") })
    $TagCommit = (($RefLines[0] -split '\s+')[0])
    if ($Peeled.Count -gt 0) { $TagCommit = (($Peeled[0] -split '\s+')[0]) }
    if ($TagCommit -cne $Commit) { throw "The release tag already points to a different commit" }
}
$Releases = @((Read-Native "gh" @("release", "list", "--repo", $Repository, "--limit", "1000", "--json", "tagName,isDraft")) | ConvertFrom-Json)
foreach ($Release in $Releases) {
    if (!$Release.isDraft -and $Release.tagName -match '^android-v([0-9]+\.[0-9]+\.[0-9]+)$' -and
        [version]$Matches[1] -ge [version]$VersionName) {
        throw "A published Android release already has this or a newer version; releases are immutable"
    }
}
$Existing = @($Releases | Where-Object { $_.tagName -ceq $Tag })
if ($Existing.Count -eq 0) {
    Read-Native "gh" @("release", "create", $Tag, "--repo", $Repository, "--draft", "--target", $Commit,
        "--title", "Rental Manager Android $VersionName", "--notes-file", $NotesPath) | Write-Output
}

$Release = (Read-Native "gh" @("release", "view", $Tag, "--repo", $Repository, "--json", "isDraft,targetCommitish,databaseId")) | ConvertFrom-Json
if (!$Release.isDraft -or $Release.targetCommitish -cne $Commit) {
    throw "Only a draft created for this exact pushed commit can be resumed"
}
$RemoteRelease = (Read-Native "gh" @("api", "repos/$Repository/releases/$($Release.databaseId)")) | ConvertFrom-Json
$ExpectedFiles = @($ApkPath, $MetadataPath)
$ExpectedNames = @($AssetName, "android-update.json")
foreach ($RemoteAsset in $RemoteRelease.assets) {
    if ($RemoteAsset.name -cnotin $ExpectedNames) { throw "The draft contains unexpected assets; review it before continuing" }
}
foreach ($File in $ExpectedFiles) {
    $Name = [IO.Path]::GetFileName($File)
    $ExpectedDigest = "sha256:" + (Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash.ToLowerInvariant()
    $RemoteAssets = @($RemoteRelease.assets | Where-Object { $_.name -ceq $Name })
    if ($RemoteAssets.Count -gt 0) {
        if ($RemoteAssets.Count -ne 1 -or $RemoteAssets[0].digest -cne $ExpectedDigest -or
            $RemoteAssets[0].size -ne (Get-Item -LiteralPath $File).Length -or $RemoteAssets[0].state -cne "uploaded") {
            throw "Existing draft asset $Name does not match the verified local file; refusing to replace it"
        }
    }
    else {
        Read-Native "gh" @("release", "upload", $Tag, $File, "--repo", $Repository) | Write-Output
    }
}
$RemoteRelease = (Read-Native "gh" @("api", "repos/$Repository/releases/$($Release.databaseId)")) | ConvertFrom-Json
foreach ($File in $ExpectedFiles) {
    $Name = [IO.Path]::GetFileName($File)
    $ExpectedDigest = "sha256:" + (Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash.ToLowerInvariant()
    $RemoteAssets = @($RemoteRelease.assets | Where-Object { $_.name -ceq $Name })
    if ($RemoteAssets.Count -ne 1 -or $RemoteAssets[0].digest -cne $ExpectedDigest -or
        $RemoteAssets[0].size -ne (Get-Item -LiteralPath $File).Length -or $RemoteAssets[0].state -cne "uploaded") {
        throw "Uploaded asset $Name failed verification; the release remains a draft"
    }
}
if ((Read-Native "git" @("-C", $RepoRoot, "status", "--porcelain")) -or
    (Read-Native "git" @("-C", $RepoRoot, "rev-parse", "HEAD")) -cne $Commit) {
    throw "The checkout changed during release preparation; the release remains a draft"
}
Read-Native "gh" @("release", "edit", $Tag, "--repo", $Repository, "--draft=false", "--latest", "--notes-file", $NotesPath) | Write-Output
Write-Output "Published https://github.com/$Repository/releases/tag/$Tag"
Write-Output "Update feed: https://github.com/$Repository/releases/latest/download/android-update.json"
