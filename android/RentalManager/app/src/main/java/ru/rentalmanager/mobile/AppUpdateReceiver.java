package ru.rentalmanager.mobile;

import android.app.DownloadManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public final class AppUpdateReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context, Intent intent) {
        if (!DownloadManager.ACTION_DOWNLOAD_COMPLETE.equals(intent.getAction())) return;
        long id = intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1);
        if (id != AppUpdates.downloadId(context)) return;
        PendingResult result = goAsync();
        AppUpdates.completed(context, result::finish);
    }
}
