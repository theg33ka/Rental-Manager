package ru.rentalmanager.mobile;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.job.JobInfo;
import android.app.job.JobScheduler;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.File;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

final class AppUpdates {
    private static final int JOB_ID = 6202;
    private static final int NOTIFICATION_ID = 6203;
    private static final String CHANNEL = "app_updates";
    private static final String MIME = "application/vnd.android.package-archive";
    private static final String RELEASE_ROOT = "https://github.com/theg33ka/Rental-Manager/releases/";
    private static final ExecutorService WORKER = Executors.newSingleThreadExecutor();
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static final CopyOnWriteArrayList<Runnable> LISTENERS = new CopyOnWriteArrayList<>();

    private AppUpdates() {}

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences("app_updates", Context.MODE_PRIVATE);
    }

    static void observe(Runnable listener) { LISTENERS.addIfAbsent(listener); }
    static void unobserve(Runnable listener) { LISTENERS.remove(listener); }
    private static void changed() { MAIN.post(() -> { for (Runnable listener : LISTENERS) listener.run(); }); }
    static long downloadId(Context context) { return prefs(context).getLong("download_id", -1); }
    static boolean automatic(Context context) { return prefs(context).getBoolean("automatic", true); }
    static boolean ready(Context context) { return prefs(context).getBoolean("ready", false); }

    static String status(Context context) {
        return prefs(context).getString("status", "Проверка и скачивание через Wi-Fi и мобильную сеть");
    }

    static void schedule(Context context) {
        JobScheduler scheduler = (JobScheduler) context.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        if (scheduler == null) return;
        if (!automatic(context)) { scheduler.cancel(JOB_ID); return; }
        for (JobInfo job : scheduler.getAllPendingJobs()) if (job.getId() == JOB_ID) return;
        scheduler.schedule(new JobInfo.Builder(JOB_ID, new ComponentName(context, AppUpdateJobService.class))
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
            .setPeriodic(UpdatePolicy.BACKGROUND_INTERVAL_MS)
            .setPersisted(true).build());
    }

    static void check(Context context, boolean force, boolean manual, Runnable done) {
        Context app = context.getApplicationContext();
        WORKER.execute(() -> {
            try {
                try { reconcile(app); } catch (Exception error) { failDownload(app); }
                if (!manual && !automatic(app)) return;
                SharedPreferences p = prefs(app);
                String base = NotificationPrefs.baseUrl(app);
                if (!base.equals(p.getString("source", ""))) {
                    discard(app);
                    p.edit().putString("source", base).remove("last_check").apply();
                }
                if (!UpdatePolicy.checkDue(System.currentTimeMillis(), p.getLong("last_check", 0), force)) return;
                p.edit().putLong("last_check", System.currentTimeMillis()).apply();
                JSONObject info;
                try { info = fetch(base + "/api/mobile-update"); }
                catch (Exception error) { info = new JSONObject(); }
                if (!info.optBoolean("available")) info = fetch(RELEASE_ROOT + "latest/download/android-update.json");
                if (!info.optBoolean("available")) {
                    if (downloadId(app) < 0) setStatus(app, "Обновления пока не опубликованы");
                    return;
                }
                long version = info.getLong("version_code");
                if (!UpdatePolicy.isNewer(installedVersion(app), version)) {
                    discard(app);
                    setStatus(app, "Установлена актуальная версия «" + installedName(app) + "»");
                    return;
                }
                if (info.getInt("min_sdk") > Build.VERSION.SDK_INT) {
                    setStatus(app, "Новая версия требует более новой версии Android");
                    return;
                }
                if (!app.getPackageName().equals(info.getString("package_name"))
                    || !UpdatePolicy.validDigest(info.getString("sha256"))
                    || info.getLong("size_bytes") <= 0) throw new Exception("Invalid release");
                URL origin = new URL(base);
                URL target = new URL(origin, info.getString("download_url"));
                boolean sameOrigin = origin.getHost().equals(target.getHost()) && origin.getPort() == target.getPort();
                boolean releaseAsset = target.toString().startsWith(RELEASE_ROOT + "download/android-v");
                if (!"https".equals(target.getProtocol()) || (!sameOrigin && !releaseAsset)
                    || target.getUserInfo() != null) throw new Exception("Invalid URL");
                if (downloadId(app) >= 0 && p.getLong("version", 0) == version
                    && info.getString("sha256").equalsIgnoreCase(p.getString("sha256", ""))) return;
                discard(app);
                String fileName = "rental-manager-" + version + "-" + info.getString("sha256").substring(0, 12) + ".apk";
                File destination = new File(app.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), fileName);
                if (destination.exists() && !destination.delete()) throw new Exception("Old download");
                DownloadManager.Request request = new DownloadManager.Request(Uri.parse(target.toString()))
                    .setTitle("Rental Manager · " + info.getString("version_name"))
                    .setDescription("Загрузка обновления приложения")
                    .setMimeType(MIME)
                    .setAllowedOverMetered(true)
                    .setAllowedOverRoaming(false)
                    .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE)
                    .setDestinationInExternalFilesDir(app, Environment.DIRECTORY_DOWNLOADS, fileName);
                // Сохраняем описание до enqueue: завершение может прийти сразу после старта.
                p.edit().putLong("version", version).putString("version_name", info.getString("version_name"))
                    .putString("sha256", info.getString("sha256"))
                    .putLong("size", info.getLong("size_bytes")).putString("file", fileName).putBoolean("ready", false).commit();
                long id = downloads(app).enqueue(request);
                p.edit().putLong("download_id", id).commit();
                setStatus(app, "Скачивается версия «" + info.getString("version_name") + "»");
                reconcile(app);
            } catch (Exception error) {
                if (!ready(app)) setStatus(app, "Не удалось проверить обновления. Повторим позже.");
            } finally {
                changed();
                if (done != null) MAIN.post(done);
            }
        });
    }

    private static JSONObject fetch(String address) throws Exception { return fetch(address, 0); }

    private static JSONObject fetch(String address, int redirects) throws Exception {
        if (redirects > 5) throw new Exception("Too many redirects");
        HttpURLConnection connection = (HttpURLConnection) new URL(address).openConnection();
        try {
            connection.setConnectTimeout(10000);
            connection.setReadTimeout(10000);
            connection.setInstanceFollowRedirects(false);
            connection.setRequestProperty("Accept", "application/json");
            int status = connection.getResponseCode();
            if (status == 301 || status == 302 || status == 303 || status == 307 || status == 308) {
                URL target = new URL(new URL(address), connection.getHeaderField("Location"));
                if (!"https".equals(target.getProtocol()) || target.getUserInfo() != null
                    || !("github.com".equals(target.getHost()) || "release-assets.githubusercontent.com".equals(target.getHost())))
                    throw new Exception("Invalid redirect");
                return fetch(target.toString(), redirects + 1);
            }
            if (status != 200) throw new Exception("Update unavailable");
            try (InputStream input = connection.getInputStream()) {
                java.io.ByteArrayOutputStream output = new java.io.ByteArrayOutputStream();
                byte[] buffer = new byte[4096];
                int read;
                while ((read = input.read(buffer)) != -1) {
                    if (output.size() + read > 32768) throw new Exception("Invalid metadata");
                    output.write(buffer, 0, read);
                }
                return new JSONObject(output.toString("UTF-8"));
            }
        } finally { connection.disconnect(); }
    }

    private static DownloadManager downloads(Context context) {
        return (DownloadManager) context.getSystemService(Context.DOWNLOAD_SERVICE);
    }

    static void completed(Context context, Runnable done) {
        Context app = context.getApplicationContext();
        WORKER.execute(() -> {
            try { reconcile(app); } catch (Exception error) { failDownload(app); }
            finally { changed(); if (done != null) done.run(); }
        });
    }

    private static void reconcile(Context context) throws Exception {
        SharedPreferences p = prefs(context);
        long id = downloadId(context);
        if (id < 0) return;
        if (!UpdatePolicy.isNewer(installedVersion(context), p.getLong("version", 0))) {
            discard(context);
            return;
        }
        try (Cursor cursor = downloads(context).query(new DownloadManager.Query().setFilterById(id))) {
            if (cursor == null || !cursor.moveToFirst()) { failDownload(context); return; }
            int state = cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS));
            if (state == DownloadManager.STATUS_FAILED) { failDownload(context); return; }
            if (state != DownloadManager.STATUS_SUCCESSFUL) {
                setStatus(context, state == DownloadManager.STATUS_PAUSED ? "Загрузка продолжится при подключении к сети" : "Скачивается версия «" + p.getString("version_name", "") + "»");
                return;
            }
        }
        if (ready(context)) return;
        verify(context);
        p.edit().putBoolean("ready", true).apply();
        setStatus(context, "Версия «" + p.getString("version_name", "") + "» скачана · Установить");
        notifyReady(context);
    }

    private static void verify(Context context) throws Exception {
        SharedPreferences p = prefs(context);
        File file = new File(context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), p.getString("file", "missing"));
        if (!file.isFile() || file.length() != p.getLong("size", -1)) throw new Exception("Invalid size");
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream stream = new java.io.FileInputStream(file)) {
            byte[] buffer = new byte[32768];
            int size;
            while ((size = stream.read(buffer)) != -1) digest.update(buffer, 0, size);
        }
        StringBuilder actual = new StringBuilder();
        for (byte value : digest.digest()) actual.append(String.format(java.util.Locale.ROOT, "%02x", value & 255));
        if (!actual.toString().equalsIgnoreCase(p.getString("sha256", ""))) throw new Exception("Invalid checksum");
        PackageManager pm = context.getPackageManager();
        PackageInfo archive = pm.getPackageArchiveInfo(file.getAbsolutePath(), PackageManager.GET_SIGNATURES);
        PackageInfo installed = pm.getPackageInfo(context.getPackageName(), PackageManager.GET_SIGNATURES);
        if (archive == null || !context.getPackageName().equals(archive.packageName)
            || version(archive) != p.getLong("version", 0) || !UpdatePolicy.isNewer(version(installed), version(archive))
            || !sameSignatures(installed.signatures, archive.signatures)) throw new Exception("Invalid package signature");
    }

    private static boolean sameSignatures(Signature[] left, Signature[] right) {
        if (left == null || right == null || left.length == 0 || left.length != right.length) return false;
        return new java.util.HashSet<>(Arrays.asList(left)).equals(new java.util.HashSet<>(Arrays.asList(right)));
    }

    private static long version(PackageInfo info) {
        return Build.VERSION.SDK_INT >= 28 ? info.getLongVersionCode() : info.versionCode;
    }

    private static long installedVersion(Context context) throws Exception {
        return version(context.getPackageManager().getPackageInfo(context.getPackageName(), 0));
    }

    static String installedName(Context context) {
        try { return context.getPackageManager().getPackageInfo(context.getPackageName(), 0).versionName; }
        catch (Exception ignored) { return ""; }
    }

    private static void setStatus(Context context, String status) { prefs(context).edit().putString("status", status).apply(); }

    private static void discard(Context context) {
        long id = downloadId(context);
        if (id >= 0) downloads(context).remove(id);
        prefs(context).edit().remove("download_id").remove("version").remove("file").remove("sha256").putBoolean("ready", false).apply();
        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager != null) manager.cancel(NOTIFICATION_ID);
    }

    private static void failDownload(Context context) {
        discard(context);
        prefs(context).edit().remove("last_check").apply();
        setStatus(context, "Не удалось загрузить или проверить APK. Повторите проверку.");
    }

    private static void notifyReady(Context context) {
        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager == null || (Build.VERSION.SDK_INT >= 33 && context.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)) return;
        if (Build.VERSION.SDK_INT >= 26) manager.createNotificationChannel(new NotificationChannel(CHANNEL, "Обновления приложения", NotificationManager.IMPORTANCE_DEFAULT));
        Intent open = new Intent(context, MainActivity.class).putExtra("open_update", true)
            .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pending = PendingIntent.getActivity(context, NOTIFICATION_ID, open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder builder = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(context, CHANNEL) : new Notification.Builder(context);
        manager.notify(NOTIFICATION_ID, builder.setSmallIcon(R.drawable.ic_stat_rental)
            .setContentTitle("Обновление готово к установке")
            .setContentText("Rental Manager «" + prefs(context).getString("version_name", "") + "»")
            .setContentIntent(pending).setAutoCancel(true).build());
    }

    static void showDialog(Activity activity) {
        new AlertDialog.Builder(activity)
            .setTitle("Обновления · " + installedName(activity))
            .setMessage(status(activity) + "\n\nАвтоскачивание: " + (automatic(activity) ? "включено" : "выключено")
                + ". Wi-Fi и мобильная сеть; без роуминга. Установка — после вашего подтверждения.")
            .setPositiveButton(ready(activity) ? "Установить" : "Проверить", (dialog, which) -> {
                if (ready(activity)) install(activity);
                else {
                    Toast.makeText(activity, "Проверяем обновления…", Toast.LENGTH_SHORT).show();
                    check(activity, true, true, () -> { if (!activity.isFinishing() && !activity.isDestroyed()) showDialog(activity); });
                }
            })
            .setNeutralButton(automatic(activity) ? "Отключить авто" : "Включить авто", (dialog, which) -> {
                prefs(activity).edit().putBoolean("automatic", !automatic(activity)).apply();
                schedule(activity);
                if (automatic(activity)) check(activity, true, false, null);
                changed();
            })
            .setNegativeButton("Позже", null).show();
    }

    private static void install(Activity activity) {
        if (Build.VERSION.SDK_INT >= 26 && !activity.getPackageManager().canRequestPackageInstalls()) {
            new AlertDialog.Builder(activity).setTitle("Разрешить установку")
                .setMessage("Разрешите Rental Manager устанавливать обновления. После возврата нажмите «Установить» ещё раз.")
                .setPositiveButton("Настройки", (dialog, which) -> {
                    try { activity.startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + activity.getPackageName()))); }
                    catch (Exception error) { Toast.makeText(activity, "Откройте разрешение установки в настройках Android", Toast.LENGTH_LONG).show(); }
                }).setNegativeButton("Позже", null).show();
            return;
        }
        WORKER.execute(() -> {
            try {
                verify(activity);
                Uri uri = downloads(activity).getUriForDownloadedFile(downloadId(activity));
                if (uri == null) throw new Exception("Missing download");
                MAIN.post(() -> {
                    if (activity.isFinishing() || activity.isDestroyed()) return;
                    try { activity.startActivity(new Intent(Intent.ACTION_VIEW).setDataAndType(uri, MIME).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)); }
                    catch (Exception error) { Toast.makeText(activity, "Не удалось открыть установщик Android", Toast.LENGTH_LONG).show(); }
                });
            } catch (Exception error) {
                failDownload(activity);
                MAIN.post(() -> { if (!activity.isFinishing() && !activity.isDestroyed()) showDialog(activity); });
            } finally { changed(); }
        });
    }
}
