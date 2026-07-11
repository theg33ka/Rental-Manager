package ru.rentalmanager.mobile;

import android.app.Activity;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import java.util.LinkedHashMap;
import java.util.Map;

public class NotificationSettingsActivity extends Activity {
    private final int bg = Color.rgb(8, 11, 15);
    private final int surface = Color.rgb(17, 24, 32);
    private final int field = Color.rgb(24, 33, 43);
    private final int text = Color.rgb(244, 241, 233);
    private final int muted = Color.rgb(143, 154, 167);
    private final int blue = Color.rgb(198, 165, 107);

    private EditText baseUrl;
    private EditText quietStart;
    private EditText quietEnd;
    private CheckBox notificationsEnabled;
    private CheckBox stickyDebt;
    private Spinner interval;
    private Spinner mode;
    private final Map<String, CheckBox> eventBoxes = new LinkedHashMap<>();

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        NotificationHelper.ensureChannels(this);
        buildUi();
        loadPrefs();
    }

    private void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(bg);

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.VERTICAL);
        header.setPadding(dp(20), dp(20), dp(20), dp(12));
        TextView title = text("Уведомления", 30, true);
        TextView subtitle = text("Пуш-уведомления, частота проверок, тихие часы и постоянное предупреждение по долгам.", 14, false);
        subtitle.setTextColor(muted);
        header.addView(title);
        header.addView(subtitle);
        root.addView(header);

        ScrollView scroll = new ScrollView(this);
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(dp(16), 0, dp(16), dp(24));
        scroll.addView(content);
        root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));

        LinearLayout hostCard = card();
        hostCard.addView(sectionTitle("Хост панели"));
        baseUrl = edit("https://rental-manager.example.ru");
        hostCard.addView(label("URL сервера", baseUrl));
        TextView hostHint = hint("Можно поменять после миграции с Railway на российский хост без новой сборки APK.");
        hostCard.addView(hostHint);
        content.addView(hostCard);

        LinearLayout mainCard = card();
        mainCard.addView(sectionTitle("Режим напоминаний"));
        notificationsEnabled = check("Включить фоновые проверки пульта");
        stickyDebt = check("Постоянное предупреждение, пока есть должники без отсрочки");
        mainCard.addView(notificationsEnabled);
        mainCard.addView(stickyDebt);
        interval = spinner(new String[]{"15 минут", "30 минут", "1 час", "3 часа", "6 часов", "12 часов"});
        mainCard.addView(label("Как часто проверять", interval));
        mode = spinner(new String[]{"Звук + вибрация", "Только вибрация", "Тихо"});
        mainCard.addView(label("Как будить", mode));
        quietStart = edit("22:00");
        quietEnd = edit("08:00");
        LinearLayout quietRow = new LinearLayout(this);
        quietRow.setOrientation(LinearLayout.HORIZONTAL);
        quietRow.setGravity(Gravity.CENTER);
        quietRow.addView(label("Тихий режим с", quietStart), new LinearLayout.LayoutParams(0, -2, 1));
        quietRow.addView(space(10), new LinearLayout.LayoutParams(dp(10), 1));
        quietRow.addView(label("до", quietEnd), new LinearLayout.LayoutParams(0, -2, 1));
        mainCard.addView(quietRow);
        content.addView(mainCard);

        LinearLayout eventsCard = card();
        eventsCard.addView(sectionTitle("О чём напоминать"));
        addEvent(eventsCard, NotificationPrefs.KEY_RENT_OVERDUE, "Просроченная аренда");
        addEvent(eventsCard, NotificationPrefs.KEY_RENT_PARTIAL, "Частично оплаченная аренда");
        addEvent(eventsCard, NotificationPrefs.KEY_RENT_TODAY, "Сегодня день оплаты аренды");
        addEvent(eventsCard, NotificationPrefs.KEY_UTILITY_OVERDUE, "Коммуналка с долгом");
        addEvent(eventsCard, NotificationPrefs.KEY_UTILITY_ISSUED, "Выставленная коммуналка");
        addEvent(eventsCard, NotificationPrefs.KEY_MANUAL_DEBTS, "Ручные долги");
        addEvent(eventsCard, NotificationPrefs.KEY_PROVIDER_DEBTS, "Долги поставщикам");
        addEvent(eventsCard, NotificationPrefs.KEY_STALE_READINGS, "Не переданы общедомовые показания");
        addEvent(eventsCard, NotificationPrefs.KEY_SUSPICIOUS_RECEIPTS, "Подозрительные чеки");
        addEvent(eventsCard, NotificationPrefs.KEY_MONTHLY_REPORTS, "Открытые месячные отчёты");
        content.addView(eventsCard);

        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        actions.setPadding(dp(16), dp(8), dp(16), dp(18));
        Button save = button("Сохранить", true);
        Button test = button("Проверить сейчас", false);
        actions.addView(save, new LinearLayout.LayoutParams(0, dp(48), 1));
        actions.addView(space(10), new LinearLayout.LayoutParams(dp(10), 1));
        actions.addView(test, new LinearLayout.LayoutParams(0, dp(48), 1));
        root.addView(actions);

        save.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                savePrefs();
                Toast.makeText(NotificationSettingsActivity.this, "Настройки сохранены", Toast.LENGTH_SHORT).show();
            }
        });
        test.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                savePrefs();
                runManualCheck();
            }
        });

        setContentView(root);
    }

    private void loadPrefs() {
        SharedPreferences prefs = NotificationPrefs.prefs(this);
        baseUrl.setText(NotificationPrefs.baseUrl(this));
        notificationsEnabled.setChecked(NotificationPrefs.notificationsEnabled(this));
        stickyDebt.setChecked(NotificationPrefs.stickyDebtEnabled(this));
        quietStart.setText(NotificationPrefs.quietStart(this));
        quietEnd.setText(NotificationPrefs.quietEnd(this));
        setIntervalSelection(NotificationPrefs.intervalMinutes(this));
        String savedMode = NotificationPrefs.mode(this);
        mode.setSelection(NotificationPrefs.MODE_VIBRATE.equals(savedMode) ? 1 : NotificationPrefs.MODE_SILENT.equals(savedMode) ? 2 : 0);
        for (Map.Entry<String, CheckBox> entry : eventBoxes.entrySet()) {
            entry.getValue().setChecked(prefs.getBoolean(entry.getKey(), true));
        }
    }

    private void savePrefs() {
        SharedPreferences.Editor editor = NotificationPrefs.prefs(this).edit();
        editor.putString(NotificationPrefs.KEY_BASE_URL, NotificationPrefs.normalizeBaseUrl(baseUrl.getText().toString()));
        editor.putBoolean(NotificationPrefs.KEY_NOTIFICATIONS_ENABLED, notificationsEnabled.isChecked());
        editor.putBoolean(NotificationPrefs.KEY_STICKY_DEBT, stickyDebt.isChecked());
        editor.putInt(NotificationPrefs.KEY_INTERVAL_MINUTES, selectedIntervalMinutes());
        editor.putString(NotificationPrefs.KEY_MODE, selectedMode());
        editor.putString(NotificationPrefs.KEY_QUIET_START, quietStart.getText().toString().trim());
        editor.putString(NotificationPrefs.KEY_QUIET_END, quietEnd.getText().toString().trim());
        for (Map.Entry<String, CheckBox> entry : eventBoxes.entrySet()) {
            editor.putBoolean(entry.getKey(), entry.getValue().isChecked());
        }
        editor.apply();
        if (notificationsEnabled.isChecked()) ReminderScheduler.schedule(this);
        else ReminderScheduler.cancel(this);
    }

    private void runManualCheck() {
        Toast.makeText(this, "Проверяю пульт...", Toast.LENGTH_SHORT).show();
        new Thread(new Runnable() {
            @Override
            public void run() {
                final DashboardDigest digest = NotificationRepository.fetchDigest(NotificationSettingsActivity.this);
                NotificationHelper.notifyDigest(NotificationSettingsActivity.this, digest, true);
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        Toast.makeText(NotificationSettingsActivity.this, digest.text(), Toast.LENGTH_LONG).show();
                    }
                });
            }
        }, "rental-manual-check").start();
    }

    private void addEvent(LinearLayout parent, String key, String label) {
        CheckBox box = check(label);
        eventBoxes.put(key, box);
        parent.addView(box);
    }

    private int selectedIntervalMinutes() {
        int index = interval.getSelectedItemPosition();
        int[] values = {15, 30, 60, 180, 360, 720};
        if (index < 0 || index >= values.length) return 60;
        return values[index];
    }

    private void setIntervalSelection(int minutes) {
        int[] values = {15, 30, 60, 180, 360, 720};
        int selected = 2;
        for (int i = 0; i < values.length; i++) {
            if (values[i] == minutes) selected = i;
        }
        interval.setSelection(selected);
    }

    private String selectedMode() {
        int index = mode.getSelectedItemPosition();
        if (index == 1) return NotificationPrefs.MODE_VIBRATE;
        if (index == 2) return NotificationPrefs.MODE_SILENT;
        return NotificationPrefs.MODE_LOUD;
    }

    private LinearLayout card() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(16), dp(14), dp(16), dp(14));
        card.setBackgroundColor(surface);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.setMargins(0, 0, 0, dp(12));
        card.setLayoutParams(params);
        card.setElevation(dp(2));
        return card;
    }

    private TextView sectionTitle(String value) {
        TextView view = text(value, 18, true);
        view.setPadding(0, 0, 0, dp(8));
        return view;
    }

    private TextView hint(String value) {
        TextView view = text(value, 13, false);
        view.setTextColor(muted);
        view.setPadding(0, dp(6), 0, 0);
        return view;
    }

    private LinearLayout label(String value, View field) {
        LinearLayout wrap = new LinearLayout(this);
        wrap.setOrientation(LinearLayout.VERTICAL);
        wrap.setPadding(0, dp(6), 0, dp(6));
        TextView label = text(value, 13, false);
        label.setTextColor(muted);
        wrap.addView(label);
        wrap.addView(field, new LinearLayout.LayoutParams(-1, dp(48)));
        return wrap;
    }

    private TextView text(String value, int sp, boolean bold) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(sp);
        view.setTextColor(text);
        if (bold) view.setTypeface(android.graphics.Typeface.DEFAULT_BOLD);
        return view;
    }

    private EditText edit(String hint) {
        EditText view = new EditText(this);
        view.setSingleLine(true);
        view.setHint(hint);
        view.setHintTextColor(muted);
        view.setTextColor(text);
        view.setBackgroundColor(field);
        view.setTextSize(15);
        view.setPadding(dp(12), 0, dp(12), 0);
        return view;
    }

    private CheckBox check(String value) {
        CheckBox view = new CheckBox(this);
        view.setText(value);
        view.setTextColor(text);
        view.setTextSize(15);
        view.setPadding(0, dp(4), 0, dp(4));
        return view;
    }

    private Spinner spinner(String[] values) {
        Spinner spinner = new Spinner(this);
        ArrayAdapter<String> adapter = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item, values);
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        spinner.setAdapter(adapter);
        return spinner;
    }

    private Button button(String text, boolean primary) {
        Button button = new Button(this);
        button.setText(text);
        button.setAllCaps(false);
        if (primary) {
            button.setTextColor(Color.WHITE);
            button.setBackgroundColor(blue);
        }
        return button;
    }

    private View space(int dpValue) {
        View view = new View(this);
        view.setMinimumWidth(dp(dpValue));
        return view;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
