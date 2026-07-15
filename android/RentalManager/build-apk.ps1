param(
    [string]$SdkRoot = "$env:LOCALAPPDATA\Android\Sdk",
    [string]$BuildToolsVersion = "35.0.0",
    [string]$JavaHome = "C:\Program Files\Eclipse Adoptium\jdk-17.0.8.7-hotspot",
    [string]$KeystorePath = ""
)

$ErrorActionPreference = "Stop"

function Run-Native([string]$Command, [string[]]$Arguments) {
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed: $Command $Arguments"
    }
}

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ManifestPath = Join-Path $Root "app\src\main\AndroidManifest.xml"
[xml]$ManifestXml = Get-Content -LiteralPath $ManifestPath
$AndroidNamespace = "http://schemas.android.com/apk/res/android"
$VersionName = $ManifestXml.manifest.GetAttribute("versionName", $AndroidNamespace)
$VersionedApk = Join-Path $Root "build\rental-manager-mobile-$VersionName.apk"
$BuildTools = Join-Path $SdkRoot "build-tools\$BuildToolsVersion"
$AndroidJar = Join-Path $SdkRoot "platforms\android-35\android.jar"
$Javac = Join-Path $JavaHome "bin\javac.exe"
$Jar = Join-Path $JavaHome "bin\jar.exe"
$Keytool = Join-Path $JavaHome "bin\keytool.exe"
if (!$KeystorePath) {
    $KeystorePath = Join-Path $Root "signing\rental-manager-dev.keystore"
}

if (!(Test-Path $AndroidJar)) {
    throw "Android platform jar not found: $AndroidJar"
}

Remove-Item -Recurse -Force "$Root\build\compiled", "$Root\build\gen", "$Root\build\classes", "$Root\build\dex" -ErrorAction SilentlyContinue
Remove-Item -Force "$Root\build\unsigned.apk", "$Root\build\classes.jar", "$Root\build\rental-manager-mobile-aligned.apk", "$Root\build\rental-manager-mobile.apk" -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $VersionedApk -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$Root\build\compiled", "$Root\build\gen", "$Root\build\classes", "$Root\build\dex" | Out-Null

Run-Native "$BuildTools\aapt2.exe" @("compile", "--dir", "$Root\app\src\main\res", "-o", "$Root\build\compiled")
$Flats = Get-ChildItem "$Root\build\compiled" -Filter *.flat | ForEach-Object { $_.FullName }
Run-Native "$BuildTools\aapt2.exe" (@("link", "-o", "$Root\build\unsigned.apk", "-I", $AndroidJar, "--manifest", "$Root\app\src\main\AndroidManifest.xml", "--java", "$Root\build\gen", "--auto-add-overlay") + $Flats)

$Sources = Get-ChildItem "$Root\app\src\main\java" -Recurse -Filter *.java | ForEach-Object { $_.FullName }
Run-Native $Javac (@("-encoding", "UTF-8", "-source", "8", "-target", "8", "-cp", "$AndroidJar;$Root\build\gen", "-d", "$Root\build\classes") + $Sources)
Run-Native $Jar @("cf", "$Root\build\classes.jar", "-C", "$Root\build\classes", ".")
Run-Native "$BuildTools\d8.bat" @("--release", "--min-api", "23", "--lib", $AndroidJar, "--output", "$Root\build\dex", "$Root\build\classes.jar")
Run-Native $Jar @("uf", "$Root\build\unsigned.apk", "-C", "$Root\build\dex", "classes.dex")
Run-Native "$BuildTools\zipalign.exe" @("-p", "-f", "4", "$Root\build\unsigned.apk", "$Root\build\rental-manager-mobile-aligned.apk")

New-Item -ItemType Directory -Force (Split-Path -Parent $KeystorePath) | Out-Null
if (!(Test-Path $KeystorePath)) {
    Run-Native $Keytool @("-genkeypair", "-v", "-keystore", $KeystorePath, "-storepass", "android", "-keypass", "android", "-alias", "androiddebugkey", "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000", "-dname", "CN=Android Debug,O=Rental Manager,C=RU")
}

Run-Native "$BuildTools\apksigner.bat" @("sign", "--ks", $KeystorePath, "--ks-pass", "pass:android", "--key-pass", "pass:android", "--out", "$Root\build\rental-manager-mobile.apk", "$Root\build\rental-manager-mobile-aligned.apk")
Run-Native "$BuildTools\apksigner.bat" @("verify", "--verbose", "$Root\build\rental-manager-mobile.apk")
Copy-Item -LiteralPath "$Root\build\rental-manager-mobile.apk" -Destination $VersionedApk
Run-Native "$BuildTools\apksigner.bat" @("verify", "--verbose", $VersionedApk)
Get-Item -LiteralPath $VersionedApk
