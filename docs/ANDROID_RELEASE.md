# Публикация Android-обновлений

APK распространяется через публичные GitHub Releases `theg33ka/Rental-Manager`. Бинарные файлы, ключи подписи и пароли не добавляются в Git. Amvera-контейнер не содержит локальную папку Android build.

## Сборка и проверка

1. На `main` увеличьте `versionCode` и `versionName` в Android manifest, обновите `CURRENT_STATE.md`.
2. Передайте существующий стабильный keystore в `build-apk.ps1 -KeystorePath`. Отсутствующий явно переданный ключ вызывает ошибку: скрипт не создаёт ему замену. Сохранение сертификата обязательно для установки поверх предыдущей версии. Без параметра скрипт сохраняет локальный development-режим; это не отдельная production signing strategy.
3. Соберите APK, выполните целевые проверки, установите его через `adb install -r` поверх предыдущего APK и проверьте сохранение сервера/сессии, уведомления и обновления. Установка обновления подтверждается системным установщиком Android.
4. Подготовьте JSON и проверьте совместимость подписи без публикации:

```powershell
powershell -ExecutionPolicy Bypass -File .\android\RentalManager\publish-release.ps1 `
  -PreviousApkPath .\android\RentalManager\build\rental-manager-mobile-0.1.7.apk `
  -PrepareOnly
```

Скрипт читает версию, package и minimum SDK из собранного APK через Android SDK, сравнивает их с manifest и проверяет подпись `apksigner`. При указании предыдущего APK проверяет совпадение сертификата/package и увеличение `versionCode`. Результат — игнорируемый Git файл `android/RentalManager/build/android-update.json` с полями `available`, `version_code`, `version_name`, `package_name`, `min_sdk`, `download_url`, `sha256`, `size_bytes`.

## Публикация

Сохраните проверенные заметки релиза в UTF-8, например в `android/RentalManager/build/release-notes.md`. После проверок зафиксируйте изменения и отправьте `main` в `origin`. Затем выполните:

```powershell
powershell -ExecutionPolicy Bypass -File .\android\RentalManager\publish-release.ps1 `
  -PreviousApkPath .\android\RentalManager\build\rental-manager-mobile-0.1.7.apk `
  -NotesPath .\android\RentalManager\build\release-notes.md
```

Для следующего релиза замените путь предыдущего APK. Используется существующая авторизация `gh`; токены не передаются аргументами. Публикация требует чистый `main`, совпадающий с актуальным `origin/main`, и публичный репозиторий. Скрипт создаёт draft `android-v<versionName>` для точного commit, загружает versioned APK и JSON, сверяет SHA-256/размер загруженных assets и только затем публикует Latest.

Прерванный draft можно продолжить, если commit и уже загруженные assets полностью совпадают. Скрипт не заменяет несовпадающие assets и не меняет опубликованные релизы. Ошибка загрузки оставляет draft скрытым от обычных клиентов. Не удаляйте предыдущие опубликованные APK: они нужны для воспроизводимой проверки и восстановления.

Публичный указатель обновлений: `https://github.com/theg33ka/Rental-Manager/releases/latest/download/android-update.json`. JSON содержит URL APK конкретного релиза. После публикации проверьте JSON без GitHub-авторизации, скачайте APK по указанному URL, сверьте его SHA-256 и проверьте получение обновления предыдущей версией приложения. APK первой версии с поддержкой обновлений требуется установить вручную.

См. также [операционный runbook](OPERATIONS_RUNBOOK.md), [доступы](ACCESS_AND_SECRETS.md) и [тестирование](TESTING.md).
