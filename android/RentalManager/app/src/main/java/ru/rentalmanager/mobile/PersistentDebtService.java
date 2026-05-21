package ru.rentalmanager.mobile;

import android.app.Service;
import android.content.Intent;
import android.os.IBinder;

public class PersistentDebtService extends Service {
    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String title = intent == null ? "" : intent.getStringExtra("title");
        String text = intent == null ? "" : intent.getStringExtra("text");
        if (title == null || title.trim().isEmpty()) title = "Есть квартиры-должники";
        if (text == null || text.trim().isEmpty()) text = "Постоянное уведомление активно, пока на дашборде есть долги без отсрочки.";
        startForeground(NotificationHelper.NOTIFICATION_STICKY_DEBT, NotificationHelper.stickyNotification(this, title, text));
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
