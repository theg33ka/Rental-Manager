package ru.rentalmanager.mobile;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.NotificationManager;
import android.app.TimePickerDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.text.Editable;
import android.text.InputType;
import android.text.TextWatcher;
import android.view.View;
import android.view.ViewGroup;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.Switch;
import android.widget.TextView;

import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

public class NotificationSettingsActivity extends Activity {
    private EditText baseUrl;
    private Switch notificationsEnabled, stickyDebt, quietEnabled;
    private Spinner interval, mode;
    private Button quietStart, quietEnd, test, permissionAction;
    private TextView permissionStatus, saveStatus, testResult;
    private LinearLayout connectionFields;
    private boolean checking;
    private String savedSnapshot = "";
    private final Map<String, Switch> eventBoxes = new LinkedHashMap<>();

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        NotificationHelper.ensureChannels(this);
        buildUi();
        loadPrefs();
    }

    @Override protected void onResume() {
        super.onResume();
        refreshPermissionStatus();
    }

    private void buildUi() {
        LinearLayout root = MobileUi.column(this);
        root.setBackgroundColor(MobileUi.BG);
        LinearLayout header = MobileUi.row(this);
        header.setPadding(dp(16), dp(10), dp(16), dp(12));
        Button back = MobileUi.iconButton(this, "back", "Назад");
        back.setOnClickListener(v -> leaveSettings());
        header.addView(back);
        LinearLayout heading = MobileUi.column(this);
        heading.setPadding(dp(14), 0, 0, 0);
        heading.addView(text("Уведомления", 23, MobileUi.TEXT, true));
        heading.addView(text("Только нужные события", 12, MobileUi.MUTED, false));
        header.addView(heading, new LinearLayout.LayoutParams(0, -2, 1));
        root.addView(header);
        ScrollView scroll = new ScrollView(this);
        scroll.setClipToPadding(false);
        LinearLayout content = MobileUi.column(this);
        content.setPadding(dp(16), dp(2), dp(16), dp(16));
        scroll.addView(content);
        root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));

        LinearLayout permission = card();
        permission.addView(text("Разрешение Android", 16, MobileUi.TEXT, true));
        permissionStatus = text("", 13, MobileUi.MUTED, false);
        permission.addView(permissionStatus);
        permissionAction = button("Настройки Android", false, v -> openNotificationSettings());
        permission.addView(permissionAction);
        content.addView(permission);

        LinearLayout delivery = card();
        notificationsEnabled = toggle(delivery, "Получать уведомления", "Проверять события в фоне, когда приложение закрыто.");
        interval = spinner(new String[]{"Каждые 15 минут", "Каждые 30 минут", "Каждый час", "Каждые 3 часа", "Каждые 6 часов", "Каждые 12 часов"});
        delivery.addView(field("Частота проверки", interval));
        delivery.addView(text("Android может отложить проверку для экономии батареи.", 12, MobileUi.MUTED, false));
        mode = spinner(new String[]{"Со звуком и вибрацией", "Только вибрация", "Без звука и вибрации"});
        delivery.addView(field("Как сообщать о новом событии", mode));
        content.addView(delivery);

        LinearLayout quiet = card();
        quietEnabled = toggle(quiet, "Тихие часы", "В этот период звуковые напоминания не приходят.");
        LinearLayout quietRow = MobileUi.row(this);
        quietStart = button("22:00", false, v -> pickTime(quietStart));
        quietEnd = button("08:00", false, v -> pickTime(quietEnd));
        quietRow.addView(field("Начало", quietStart), new LinearLayout.LayoutParams(0, -2, 1));
        quietRow.addView(space(12));
        quietRow.addView(field("Окончание", quietEnd), new LinearLayout.LayoutParams(0, -2, 1));
        quiet.addView(quietRow);
        quietEnabled.setOnCheckedChangeListener((v, enabled) -> {
            quietStart.setEnabled(enabled); quietEnd.setEnabled(enabled); updateDirtyState();
        });
        content.addView(quiet);

        content.addView(MobileUi.section(this, "Какие события важны"));
        LinearLayout presets = MobileUi.row(this);
        presets.addView(button("Только важное", false, v -> applyPreset(false)), new LinearLayout.LayoutParams(0, -2, 1));
        presets.addView(space(10));
        presets.addView(button("Все события", false, v -> applyPreset(true)), new LinearLayout.LayoutParams(0, -2, 1));
        content.addView(presets);
        content.addView(text("Уточните выбор ниже и сохраните настройки.", 12, MobileUi.MUTED, false));
        LinearLayout important = card();
        important.addView(text("Требует решения", 16, MobileUi.TEXT, true));
        addEvent(important, NotificationPrefs.KEY_SUSPICIOUS_RECEIPTS, "Чеки на проверку", "Нужно подтвердить или отклонить платёж.");
        addEvent(important, NotificationPrefs.KEY_RENT_OVERDUE, "Просроченная аренда", "Истёк срок оплаты, есть задолженность.");
        addEvent(important, NotificationPrefs.KEY_UTILITY_OVERDUE, "Долги за коммуналку", "Просроченные счета жильцов.");
        addEvent(important, NotificationPrefs.KEY_PROVIDER_DEBTS, "Оплата поставщикам", "Есть неоплаченные счета поставщиков.");
        addEvent(important, NotificationPrefs.KEY_MANUAL_DEBTS, "Ручные долги", "Задолженность, добавленная вручную.");
        content.addView(important);
        LinearLayout planned = card();
        planned.addView(text("Сроки и текущая работа", 16, MobileUi.TEXT, true));
        addEvent(planned, NotificationPrefs.KEY_RENT_TODAY, "Аренда на сегодня", "Наступил день очередной оплаты.");
        addEvent(planned, NotificationPrefs.KEY_STALE_READINGS, "Показания поставщику", "Пора передать общедомовые показания.");
        addEvent(planned, NotificationPrefs.KEY_RENT_PARTIAL, "Частичная оплата аренды", "Поступила часть суммы начисления.");
        addEvent(planned, NotificationPrefs.KEY_UTILITY_ISSUED, "Выставленные счета", "Коммунальные счета ожидают оплаты.");
        addEvent(planned, NotificationPrefs.KEY_MONTHLY_REPORTS, "Месячные отчёты", "Есть отчёты, которые ещё не закрыты.");
        content.addView(planned);
        LinearLayout persistent = card();
        stickyDebt = toggle(persistent, "Закреплять сводку долгов", "Тихая сводка, пока есть квартиры с долгами без отсрочки.");
        content.addView(persistent);

        LinearLayout check = card();
        check.addView(text("Проверка уведомлений", 16, MobileUi.TEXT, true));
        check.addView(text("Сохраняет выбор и получает актуальные события. Пробное уведомление учитывает общий выключатель и тихие часы.", 13, MobileUi.MUTED, false));
        test = button("Проверить сейчас", false, v -> { if (savePrefs()) runManualCheck(); });
        check.addView(test);
        testResult = text("Результат появится здесь.", 13, MobileUi.MUTED, false);
        testResult.setTextIsSelectable(true);
        check.addView(testResult);
        content.addView(check);

        LinearLayout connection = card();
        connection.addView(button("Подключение к серверу", false, v -> {
            connectionFields.setVisibility(connectionFields.getVisibility() == View.VISIBLE ? View.GONE : View.VISIBLE);
        }));
        connectionFields = MobileUi.column(this);
        baseUrl = MobileUi.field(this, "https://server.example.ru");
        baseUrl.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        connectionFields.addView(field("Адрес сервера", baseUrl));
        connectionFields.addView(text("Адрес панели без /api и других путей.", 12, MobileUi.MUTED, false));
        connectionFields.setVisibility(View.GONE);
        connection.addView(connectionFields);
        content.addView(connection);

        LinearLayout footer = MobileUi.column(this);
        footer.setPadding(dp(16), dp(8), dp(16), dp(10));
        footer.setBackgroundColor(MobileUi.BG);
        saveStatus = text("", 12, MobileUi.MUTED, false);
        footer.addView(saveStatus);
        footer.addView(button("Сохранить настройки", true, v -> savePrefs()));
        root.addView(footer);
        setContentView(root);
        MobileUi.applyWindow(this, root);
        baseUrl.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int start, int count, int after) {}
            @Override public void onTextChanged(CharSequence s, int start, int before, int count) { updateDirtyState(); }
            @Override public void afterTextChanged(Editable s) {}
        });
    }

    private void loadPrefs() {
        baseUrl.setText(NotificationPrefs.baseUrl(this));
        notificationsEnabled.setChecked(NotificationPrefs.notificationsEnabled(this));
        stickyDebt.setChecked(NotificationPrefs.stickyDebtEnabled(this));
        String start = NotificationPrefs.quietStart(this), end = NotificationPrefs.quietEnd(this);
        boolean quiet = !start.equals(end);
        quietStart.setText(quiet ? validTime(start, "22:00") : "22:00");
        quietEnd.setText(quiet ? validTime(end, "08:00") : "08:00");
        quietEnabled.setChecked(quiet);
        quietStart.setEnabled(quiet); quietEnd.setEnabled(quiet);
        int[] values = {15, 30, 60, 180, 360, 720};
        int selected = 2;
        for (int i = 0; i < values.length; i++) if (values[i] == NotificationPrefs.intervalMinutes(this)) selected = i;
        interval.setSelection(selected);
        String savedMode = NotificationPrefs.mode(this);
        mode.setSelection(NotificationPrefs.MODE_VIBRATE.equals(savedMode) ? 1 : NotificationPrefs.MODE_SILENT.equals(savedMode) ? 2 : 0);
        for (Map.Entry<String, Switch> entry : eventBoxes.entrySet()) entry.getValue().setChecked(NotificationPrefs.eventEnabled(this, entry.getKey()));
        savedSnapshot = snapshot();
        updateDirtyState();
    }

    private boolean savePrefs() {
        String url = NotificationPrefs.normalizeBaseUrl(baseUrl.getText().toString());
        Uri parsed = Uri.parse(url);
        if (!("https".equals(parsed.getScheme()) || "http".equals(parsed.getScheme())) || parsed.getHost() == null
            || parsed.getUserInfo() != null || parsed.getQuery() != null || parsed.getFragment() != null
            || (parsed.getPath() != null && !parsed.getPath().isEmpty())) {
            connectionFields.setVisibility(View.VISIBLE);
            baseUrl.setError("Укажите адрес сервера без пути"); baseUrl.requestFocus();
            saveStatus.setText("Проверьте адрес сервера"); saveStatus.setTextColor(MobileUi.DANGER); return false;
        }
        if (quietEnabled.isChecked() && quietStart.getText().toString().equals(quietEnd.getText().toString())) {
            saveStatus.setText("Начало и окончание тихих часов должны отличаться");
            saveStatus.setTextColor(MobileUi.DANGER); return false;
        }
        int[] intervals = {15, 30, 60, 180, 360, 720};
        int index = Math.max(0, Math.min(intervals.length - 1, interval.getSelectedItemPosition()));
        int modeIndex = mode.getSelectedItemPosition();
        SharedPreferences.Editor editor = NotificationPrefs.prefs(this).edit();
        editor.putString(NotificationPrefs.KEY_BASE_URL, url);
        editor.putBoolean(NotificationPrefs.KEY_NOTIFICATIONS_ENABLED, notificationsEnabled.isChecked());
        editor.putBoolean(NotificationPrefs.KEY_STICKY_DEBT, stickyDebt.isChecked());
        editor.putInt(NotificationPrefs.KEY_INTERVAL_MINUTES, intervals[index]);
        editor.putString(NotificationPrefs.KEY_MODE, modeIndex == 1 ? NotificationPrefs.MODE_VIBRATE : modeIndex == 2 ? NotificationPrefs.MODE_SILENT : NotificationPrefs.MODE_LOUD);
        editor.putString(NotificationPrefs.KEY_QUIET_START, quietEnabled.isChecked() ? quietStart.getText().toString() : "00:00");
        editor.putString(NotificationPrefs.KEY_QUIET_END, quietEnabled.isChecked() ? quietEnd.getText().toString() : "00:00");
        for (Map.Entry<String, Switch> entry : eventBoxes.entrySet()) editor.putBoolean(entry.getKey(), entry.getValue().isChecked());
        editor.apply();
        NotificationHelper.refreshPreferences(this);
        if (notificationsEnabled.isChecked()) ReminderScheduler.schedule(this); else ReminderScheduler.cancel(this);
        savedSnapshot = snapshot(); updateDirtyState();
        saveStatus.setText("Настройки сохранены"); saveStatus.setTextColor(MobileUi.SUCCESS);
        return true;
    }

    private void runManualCheck() {
        if (checking) return;
        checking = true; test.setEnabled(false); test.setText("Проверяем…");
        testResult.setText("Получаем события с сервера…"); testResult.setTextColor(MobileUi.MUTED);
        new Thread(() -> {
            DashboardDigest digest = NotificationRepository.fetchDigest(this);
            NotificationHelper.notifyDigest(this, digest, true);
            runOnUiThread(() -> {
                if (isFinishing() || isDestroyed()) return;
                checking = false; test.setEnabled(true); test.setText("Проверить ещё раз");
                testResult.setText(digest.bigText());
                testResult.setTextColor(!digest.networkOk || !digest.authorized ? MobileUi.DANGER : MobileUi.TEXT);
                refreshPermissionStatus();
            });
        }, "rental-manual-check").start();
    }

    private void refreshPermissionStatus() {
        if (permissionStatus == null) return;
        NotificationManager manager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        boolean permission = Build.VERSION.SDK_INT < 33 || checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED;
        boolean allowed = permission && (Build.VERSION.SDK_INT < 24 || manager == null || manager.areNotificationsEnabled());
        permissionStatus.setText(allowed ? "Уведомления разрешены. Звук и категории также настраиваются в Android."
            : "Android блокирует уведомления. Разрешите их, чтобы получать напоминания.");
        permissionStatus.setTextColor(allowed ? MobileUi.MUTED : MobileUi.WARNING);
        permissionAction.setText(allowed ? "Настройки Android" : "Разрешить уведомления");
    }

    private void openNotificationSettings() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
            && !NotificationPrefs.prefs(this).getBoolean("notification_permission_requested", false)) {
            NotificationPrefs.prefs(this).edit().putBoolean("notification_permission_requested", true).apply();
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 210); return;
        }
        Intent intent = Build.VERSION.SDK_INT >= 26
            ? new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, getPackageName())
            : new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + getPackageName()));
        startActivity(intent);
    }

    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(request, permissions, results); refreshPermissionStatus();
    }

    private void pickTime(Button target) {
        String[] value = target.getText().toString().split(":");
        new TimePickerDialog(this, (view, hour, minute) -> {
            target.setText(String.format(Locale.ROOT, "%02d:%02d", hour, minute)); updateDirtyState();
        }, Integer.parseInt(value[0]), Integer.parseInt(value[1]), true).show();
    }

    private String validTime(String raw, String fallback) {
        return raw != null && raw.matches("(?:[01][0-9]|2[0-3]):[0-5][0-9]") ? raw : fallback;
    }

    private void applyPreset(boolean all) {
        for (Map.Entry<String, Switch> entry : eventBoxes.entrySet()) {
            String key = entry.getKey();
            entry.getValue().setChecked(all || NotificationPrefs.KEY_SUSPICIOUS_RECEIPTS.equals(key)
                || NotificationPrefs.KEY_RENT_OVERDUE.equals(key) || NotificationPrefs.KEY_UTILITY_OVERDUE.equals(key)
                || NotificationPrefs.KEY_PROVIDER_DEBTS.equals(key) || NotificationPrefs.KEY_STALE_READINGS.equals(key));
        }
        updateDirtyState();
    }

    private String snapshot() {
        if (baseUrl == null || interval == null || mode == null || stickyDebt == null || quietStart == null) return "";
        StringBuilder result = new StringBuilder(baseUrl.getText().toString().trim());
        result.append('|').append(notificationsEnabled.isChecked()).append('|').append(stickyDebt.isChecked())
            .append('|').append(interval.getSelectedItemPosition()).append('|').append(mode.getSelectedItemPosition())
            .append('|').append(quietEnabled.isChecked()).append('|').append(quietStart.getText()).append('|').append(quietEnd.getText());
        for (Switch value : eventBoxes.values()) result.append('|').append(value.isChecked());
        return result.toString();
    }

    private void updateDirtyState() {
        if (saveStatus == null || savedSnapshot.isEmpty()) return;
        boolean dirty = !savedSnapshot.equals(snapshot());
        saveStatus.setText(dirty ? "Есть несохранённые изменения" : "Все изменения сохранены");
        saveStatus.setTextColor(dirty ? MobileUi.WARNING : MobileUi.MUTED);
    }

    @Override public void onBackPressed() { leaveSettings(); }
    private void leaveSettings() {
        if (savedSnapshot.equals(snapshot())) { finish(); return; }
        new AlertDialog.Builder(this).setTitle("Сохранить настройки?").setMessage("Вы изменили параметры уведомлений.")
            .setPositiveButton("Сохранить", (d, w) -> { if (savePrefs()) finish(); })
            .setNegativeButton("Не сохранять", (d, w) -> finish()).setNeutralButton("Продолжить", null).show();
    }

    private Switch toggle(LinearLayout parent, String title, String description) {
        Switch toggle = new Switch(this);
        toggle.setText(title); toggle.setTextSize(15); toggle.setTextColor(MobileUi.TEXT);
        toggle.setMinHeight(dp(48)); toggle.setPadding(0, dp(4), 0, dp(4)); toggle.setSwitchPadding(dp(12));
        parent.addView(toggle, new LinearLayout.LayoutParams(-1, -2));
        TextView hint = text(description, 12, MobileUi.MUTED, false); hint.setPadding(0, 0, 0, dp(12)); parent.addView(hint);
        toggle.setOnCheckedChangeListener((v, checked) -> updateDirtyState());
        return toggle;
    }
    private void addEvent(LinearLayout parent, String key, String title, String description) { eventBoxes.put(key, toggle(parent, title, description)); }
    private Spinner spinner(String[] values) {
        Spinner spinner = new Spinner(this);
        spinner.setAdapter(new ArrayAdapter<String>(this, android.R.layout.simple_spinner_item, values) {
            @Override public View getView(int p, View c, ViewGroup parent) { return item(p, false); }
            @Override public View getDropDownView(int p, View c, ViewGroup parent) { return item(p, true); }
            private TextView item(int p, boolean dropdown) {
                TextView view = text(getItem(p), 14, MobileUi.TEXT, false);
                view.setPadding(dp(12), dp(14), dp(12), dp(14)); view.setMinHeight(dp(48));
                if (dropdown) view.setBackgroundColor(MobileUi.SURFACE_ALT);
                return view;
            }
        });
        spinner.setBackground(MobileUi.shape(this, MobileUi.SURFACE_ALT, 12, MobileUi.BORDER));
        spinner.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override public void onItemSelected(AdapterView<?> p, View v, int position, long id) { updateDirtyState(); }
            @Override public void onNothingSelected(AdapterView<?> p) {}
        });
        return spinner;
    }
    private LinearLayout field(String title, View view) {
        LinearLayout field = MobileUi.column(this); field.setPadding(0, dp(8), 0, dp(6));
        field.addView(text(title, 12, MobileUi.MUTED, false)); field.addView(view, new LinearLayout.LayoutParams(-1, -2)); return field;
    }
    private Button button(String title, boolean primary, View.OnClickListener listener) {
        Button button = MobileUi.button(this, title, primary);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2); params.setMargins(0, dp(8), 0, dp(4));
        button.setLayoutParams(params); button.setOnClickListener(listener); return button;
    }
    private View space(int size) { View view = new View(this); view.setLayoutParams(new LinearLayout.LayoutParams(dp(size), 1)); return view; }
    private LinearLayout card() { return MobileUi.card(this); }
    private TextView text(String value, int size, int color, boolean bold) { return MobileUi.text(this, value, size, color, bold); }
    private int dp(int value) { return MobileUi.dp(this, value); }
}
