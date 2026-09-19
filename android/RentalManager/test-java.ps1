param([string]$JavaHome = 'C:\Program Files\Eclipse Adoptium\jdk-17.0.8.7-hotspot')
$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$OutputDirectory = Join-Path $ProjectRoot 'build\java-tests'
New-Item -ItemType Directory -Force $OutputDirectory | Out-Null
$Sources = @(
    (Join-Path $ProjectRoot 'app\src\main\java\ru\rentalmanager\mobile\UpdatePolicy.java'),
    (Join-Path $ProjectRoot 'tests\ru\rentalmanager\mobile\UpdatePolicyTest.java'),
    (Join-Path $ProjectRoot 'app\src\main\java\ru\rentalmanager\mobile\NotificationPolicy.java'),
    (Join-Path $ProjectRoot 'tests\ru\rentalmanager\mobile\NotificationPolicyTest.java')
)
& (Join-Path $JavaHome 'bin\javac.exe') -encoding UTF-8 -d $OutputDirectory @Sources
if ($LASTEXITCODE -ne 0) { throw 'Java test compilation failed' }
& (Join-Path $JavaHome 'bin\java.exe') -cp $OutputDirectory ru.rentalmanager.mobile.UpdatePolicyTest
if ($LASTEXITCODE -ne 0) { throw 'Java tests failed' }
& (Join-Path $JavaHome 'bin\java.exe') -cp $OutputDirectory ru.rentalmanager.mobile.NotificationPolicyTest
if ($LASTEXITCODE -ne 0) { throw 'Notification tests failed' }
