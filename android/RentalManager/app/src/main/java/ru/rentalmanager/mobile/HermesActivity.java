package ru.rentalmanager.mobile;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.ArrayAdapter;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.text.NumberFormat;
import java.util.Locale;

public class HermesActivity extends Activity {
    private final int bg = Color.rgb(8, 11, 15);
    private final int surface = Color.rgb(17, 24, 32);
    private final int surface2 = Color.rgb(24, 33, 43);
    private final int text = Color.rgb(244, 241, 233);
    private final int muted = Color.rgb(143, 154, 167);
    private final int accent = Color.rgb(198, 165, 107);
    private final int green = Color.rgb(111, 155, 124);
    private final int red = Color.rgb(201, 111, 104);

    private ApiClient api;
    private LinearLayout content;
    private TextView subtitle;

    interface Job {
        Object run() throws Exception;
    }

    interface Done {
        void run(Object value);
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        api = new ApiClient(this);
        buildUi();
        loadSummary();
    }

    private void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(bg);

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.VERTICAL);
        header.setPadding(dp(18), dp(18), dp(18), dp(10));
        TextView title = text("Hermes Core", 30, true);
        subtitle = text("Загружаю центр управления AI", 13, false);
        subtitle.setTextColor(muted);
        header.addView(title);
        header.addView(subtitle);
        root.addView(header);

        HorizontalScrollView navScroll = new HorizontalScrollView(this);
        navScroll.setHorizontalScrollBarEnabled(false);
        LinearLayout nav = new LinearLayout(this);
        nav.setOrientation(LinearLayout.HORIZONTAL);
        nav.setPadding(dp(12), 0, dp(12), dp(10));
        nav.addView(navButton("Сводка", v -> loadSummary()));
        nav.addView(navButton("Кейсы", v -> loadCases()));
        nav.addView(navButton("Подтверждения", v -> loadProposals()));
        nav.addView(navButton("Обязательства", v -> loadCommitments()));
        nav.addView(navButton("Настройки", v -> loadSettings()));
        navScroll.addView(nav);
        root.addView(navScroll);

        ScrollView scroll = new ScrollView(this);
        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(dp(14), 0, dp(14), dp(30));
        scroll.addView(content);
        root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(root);
    }

    private void loadSummary() {
        runApi("Загружаю сводку", () -> api.getJson("/api/android/hermes/summary"), value -> renderSummary((JSONObject) value));
    }

    private void renderSummary(JSONObject data) {
        content.removeAllViews();
        JSONObject overview = data.optJSONObject("overview");
        if (overview == null) overview = new JSONObject();
        subtitle.setText(overview.optBoolean("enabled") ? "Hermes включён" : "Hermes выключен в настройках");
        LinearLayout metrics = card();
        metrics.addView(text("Операционный контур", 19, true));
        metrics.addView(metric("Активные кейсы", String.valueOf(overview.optInt("active_cases"))));
        metrics.addView(metric("Ждут владельца", String.valueOf(overview.optInt("waiting_owner"))));
        metrics.addView(metric("Ждут жильца", String.valueOf(overview.optInt("waiting_tenant"))));
        metrics.addView(metric("Нужны подтверждения", String.valueOf(overview.optInt("pending_proposals"))));
        metrics.addView(metric("Стоимость за месяц", money(overview.optDouble("cost_month_rub"))));
        metrics.addView(metric("Прогноз за месяц", money(overview.optDouble("monthly_forecast_rub"))));
        content.addView(metrics);

        JSONObject preview = data.optJSONObject("briefing_preview");
        LinearLayout briefing = card();
        briefing.addView(text("Следующая сводка", 19, true));
        String previewText = preview == null ? "" : preview.optString("text");
        briefing.addView(hint(previewText.isEmpty() ? "Новых или изменившихся кейсов нет." : previewText));
        if (preview != null) briefing.addView(hint(preview.optInt("length") + " / " + preview.optInt("limit") + " символов"));
        content.addView(briefing);

        LinearLayout actions = card();
        actions.addView(primaryButton("Открыть кейсы", v -> loadCases()));
        actions.addView(secondaryButton("Открыть подтверждения", v -> loadProposals()));
        actions.addView(secondaryButton("Открыть обязательства", v -> loadCommitments()));
        content.addView(actions);
    }

    private void loadCases() {
        runApi("Загружаю кейсы", () -> api.getArray("/api/android/hermes/cases"), value -> renderCases((JSONArray) value));
    }

    private void renderCases(JSONArray cases) {
        content.removeAllViews();
        subtitle.setText("Операционные кейсы · " + cases.length());
        if (cases.length() == 0) {
            content.addView(emptyCard("Открытых кейсов нет."));
            return;
        }
        for (int i = 0; i < cases.length(); i++) {
            JSONObject item = cases.optJSONObject(i);
            if (item == null) continue;
            LinearLayout card = card();
            card.addView(text(item.optString("label") + " · " + item.optString("title"), 18, true));
            card.addView(hint(item.optString("compact_summary")));
            card.addView(hint("Статус: " + item.optString("status") + " · приоритет " + String.format(Locale.US, "%.1f", item.optDouble("priority_score"))));
            if (item.optDouble("amount_total") > 0) card.addView(hint("Сумма: " + money(item.optDouble("amount_total"))));
            int caseId = item.optInt("id");
            card.addView(primaryButton("Подробнее", v -> loadCaseDetails(caseId)));
            content.addView(card);
        }
    }

    private void loadCaseDetails(int caseId) {
        runApi("Открываю кейс", () -> api.getJson("/api/android/hermes/cases/" + caseId), value -> showCaseDialog((JSONObject) value));
    }

    private void showCaseDialog(JSONObject item) {
        String body = item.optString("rolling_summary")
            + "\n\nСтатус: " + item.optString("status")
            + "\nОжидается: " + item.optString("waiting_for", "—")
            + "\nСледующая проверка: " + item.optString("next_review_at", "—")
            + "\n\nИстория: " + item.optJSONArray("history");
        new AlertDialog.Builder(this)
            .setTitle(item.optString("label") + " · " + item.optString("title"))
            .setMessage(body)
            .setPositiveButton("Закрыть", null)
            .show();
    }

    private void loadProposals() {
        runApi("Загружаю подтверждения", () -> api.getArray("/api/android/hermes/proposals"), value -> renderProposals((JSONArray) value));
    }

    private void renderProposals(JSONArray proposals) {
        content.removeAllViews();
        subtitle.setText("Предложения действий");
        int shown = 0;
        for (int i = 0; i < proposals.length(); i++) {
            JSONObject item = proposals.optJSONObject(i);
            if (item == null || !"pending".equals(item.optString("status"))) continue;
            shown++;
            LinearLayout card = card();
            int proposalId = item.optInt("id");
            int safetyLevel = item.optInt("safety_level");
            card.addView(text("#" + proposalId + " · " + item.optString("action_type"), 18, true));
            card.addView(hint(item.optString("preview")));
            TextView safety = hint("Уровень безопасности: " + safetyLevel);
            safety.setTextColor(safetyLevel >= 3 ? red : accent);
            card.addView(safety);
            LinearLayout actions = row();
            actions.addView(primaryButton("Подтвердить", v -> confirmProposal(proposalId, safetyLevel)), new LinearLayout.LayoutParams(0, dp(48), 1));
            actions.addView(secondaryButton("Отклонить", v -> rejectProposal(proposalId)), new LinearLayout.LayoutParams(0, dp(48), 1));
            card.addView(actions);
            content.addView(card);
        }
        if (shown == 0) content.addView(emptyCard("Нет предложений, ожидающих подтверждения."));
    }

    private void confirmProposal(int proposalId, int safetyLevel) {
        if (safetyLevel < 3) {
            executeProposal(proposalId, "");
            return;
        }
        EditText pin = new EditText(this);
        pin.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD);
        pin.setHint("PIN владельца");
        new AlertDialog.Builder(this)
            .setTitle("Критическое или массовое действие")
            .setMessage("Для уровня 3 повторно введите PIN владельца.")
            .setView(pin)
            .setPositiveButton("Подтвердить", (dialog, which) -> executeProposal(proposalId, pin.getText().toString()))
            .setNegativeButton("Отмена", null)
            .show();
    }

    private void executeProposal(int proposalId, String pin) {
        JSONObject body = new JSONObject();
        try {
            body.put("confirmation_pin", pin);
        } catch (Exception ignored) {
        }
        runApi("Выполняю действие", () -> api.postJson("/api/android/hermes/proposals/" + proposalId + "/confirm", body), value -> {
            toast("Действие выполнено");
            loadProposals();
        });
    }

    private void rejectProposal(int proposalId) {
        new AlertDialog.Builder(this)
            .setTitle("Отклонить предложение?")
            .setPositiveButton("Отклонить", (dialog, which) -> runApi("Отклоняю", () -> api.postJson("/api/android/hermes/proposals/" + proposalId + "/reject", new JSONObject()), value -> loadProposals()))
            .setNegativeButton("Отмена", null)
            .show();
    }

    private void loadCommitments() {
        runApi("Загружаю обязательства", () -> api.getArray("/api/android/hermes/commitments"), value -> renderCommitments((JSONArray) value));
    }

    private void renderCommitments(JSONArray commitments) {
        content.removeAllViews();
        subtitle.setText("Обязательства владельца");
        int shown = 0;
        for (int i = 0; i < commitments.length(); i++) {
            JSONObject item = commitments.optJSONObject(i);
            if (item == null || "completed".equals(item.optString("status"))) continue;
            shown++;
            int commitmentId = item.optInt("id");
            LinearLayout card = card();
            card.addView(text(item.optString("description"), 18, true));
            card.addView(hint("Срок: " + item.optString("due_at", "—") + " · " + item.optString("status")));
            LinearLayout actions = row();
            actions.addView(primaryButton("Готово", v -> commitmentAction(commitmentId, "complete")), new LinearLayout.LayoutParams(0, dp(48), 1));
            actions.addView(secondaryButton("На день", v -> commitmentAction(commitmentId, "postpone")), new LinearLayout.LayoutParams(0, dp(48), 1));
            card.addView(actions);
            content.addView(card);
        }
        if (shown == 0) content.addView(emptyCard("Активных обязательств нет."));
    }

    private void commitmentAction(int commitmentId, String action) {
        runApi("Сохраняю", () -> api.postJson("/api/hermes/commitments/" + commitmentId + "/" + action, new JSONObject()), value -> loadCommitments());
    }

    private void loadSettings() {
        runApi("Загружаю настройки", () -> api.getJson("/api/android/hermes/settings"), value -> renderSettings((JSONObject) value));
    }

    private void renderSettings(JSONObject settings) {
        content.removeAllViews();
        subtitle.setText("Настройки автономности и общения");
        LinearLayout card = card();
        CheckBox enabled = check("Включить Hermes", settings.optBoolean("ai_enabled"));
        CheckBox briefing = check("Ежедневная детерминированная сводка", settings.optBoolean("hermes_briefing_enabled", true));
        CheckBox levelOne = check("Автономные действия уровня 1", settings.optBoolean("hermes_auto_level_one_enabled"));
        CheckBox templates = check("Утверждённые шаблонные напоминания", settings.optBoolean("hermes_auto_template_reminders", true));
        card.addView(enabled);
        card.addView(briefing);
        card.addView(levelOne);
        card.addView(templates);
        Spinner mode = new Spinner(this);
        String[] values = {"Автоматический", "Только черновики", "Только финансы"};
        mode.setAdapter(new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item, values));
        String currentMode = settings.optString("ai_tenant_mode", "auto");
        mode.setSelection("draft_only".equals(currentMode) ? 1 : "finance_only".equals(currentMode) ? 2 : 0);
        card.addView(hint("Режим общения с жильцами"));
        card.addView(mode, new LinearLayout.LayoutParams(-1, dp(52)));
        card.addView(primaryButton("Сохранить", v -> {
            JSONObject body = new JSONObject();
            try {
                body.put("ai_enabled", enabled.isChecked());
                body.put("hermes_briefing_enabled", briefing.isChecked());
                body.put("hermes_auto_level_one_enabled", levelOne.isChecked());
                body.put("hermes_auto_template_reminders", templates.isChecked());
                body.put("ai_tenant_mode", mode.getSelectedItemPosition() == 1 ? "draft_only" : mode.getSelectedItemPosition() == 2 ? "finance_only" : "auto");
            } catch (Exception ignored) {
            }
            runApi("Сохраняю настройки", () -> api.postJson("/api/android/hermes/settings", body), value -> toast("Настройки сохранены"));
        }));
        content.addView(card);
    }

    private void runApi(String loading, Job job, Done done) {
        subtitle.setText(loading + "…");
        new Thread(() -> {
            try {
                Object value = job.run();
                runOnUiThread(() -> done.run(value));
            } catch (Exception exception) {
                runOnUiThread(() -> {
                    subtitle.setText("Ошибка связи с Hermes");
                    toast(exception.getMessage() == null ? "Ошибка API" : exception.getMessage());
                });
            }
        }, "hermes-api").start();
    }

    private LinearLayout card() {
        LinearLayout view = new LinearLayout(this);
        view.setOrientation(LinearLayout.VERTICAL);
        view.setPadding(dp(16), dp(14), dp(16), dp(14));
        view.setBackground(round(surface, 16));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.setMargins(0, 0, 0, dp(12));
        view.setLayoutParams(params);
        return view;
    }

    private LinearLayout emptyCard(String value) {
        LinearLayout card = card();
        card.addView(hint(value));
        return card;
    }

    private LinearLayout row() {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        return row;
    }

    private TextView metric(String label, String value) {
        TextView view = text(label + ":  " + value, 16, false);
        view.setPadding(0, dp(8), 0, 0);
        return view;
    }

    private TextView text(String value, int size, boolean bold) {
        TextView view = new TextView(this);
        view.setText(value == null ? "" : value);
        view.setTextSize(size);
        view.setTextColor(text);
        if (bold) view.setTypeface(Typeface.DEFAULT_BOLD);
        return view;
    }

    private TextView hint(String value) {
        TextView view = text(value, 13, false);
        view.setTextColor(muted);
        view.setPadding(0, dp(7), 0, dp(7));
        return view;
    }

    private CheckBox check(String label, boolean checked) {
        CheckBox box = new CheckBox(this);
        box.setText(label);
        box.setTextColor(text);
        box.setChecked(checked);
        box.setPadding(0, dp(6), 0, dp(6));
        return box;
    }

    private Button navButton(String label, View.OnClickListener listener) {
        Button button = secondaryButton(label, listener);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-2, dp(44));
        params.setMargins(0, 0, dp(8), 0);
        button.setLayoutParams(params);
        return button;
    }

    private Button primaryButton(String label, View.OnClickListener listener) {
        return button(label, accent, Color.rgb(8, 11, 15), listener);
    }

    private Button secondaryButton(String label, View.OnClickListener listener) {
        return button(label, surface2, text, listener);
    }

    private Button button(String label, int color, int textColor, View.OnClickListener listener) {
        Button button = new Button(this);
        button.setText(label);
        button.setTextColor(textColor);
        button.setTextSize(13);
        button.setAllCaps(false);
        button.setBackground(round(color, 12));
        button.setOnClickListener(listener);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, dp(48));
        params.setMargins(0, dp(8), dp(6), 0);
        button.setLayoutParams(params);
        return button;
    }

    private GradientDrawable round(int color, int radius) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(color);
        drawable.setCornerRadius(dp(radius));
        return drawable;
    }

    private String money(double value) {
        NumberFormat format = NumberFormat.getCurrencyInstance(new Locale("ru", "RU"));
        format.setMaximumFractionDigits(0);
        return format.format(value);
    }

    private void toast(String value) {
        Toast.makeText(this, value, Toast.LENGTH_LONG).show();
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
