package ru.rentalmanager.mobile;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.graphics.Bitmap;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;

public final class UiSmokeInstrumentation extends Instrumentation {
    private final JSONArray captures = new JSONArray();
    private UiSmokeActivity host;
    private File output;

    @Override public void onCreate(Bundle arguments) { super.onCreate(arguments); start(); }

    @Override public void onStart() {
        Bundle result = new Bundle();
        try {
            if (getTargetContext().checkSelfPermission("android.permission.INTERNET") == PackageManager.PERMISSION_GRANTED) {
                throw new AssertionError("The UI smoke APK must not have INTERNET permission");
            }
            output = new File(getTargetContext().getExternalFilesDir(null), "ui-smoke");
            if (!output.isDirectory() && !output.mkdirs()) throw new AssertionError("Cannot create screenshot directory");
            host = (UiSmokeActivity) startActivitySync(new Intent(getTargetContext(), UiSmokeActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            for (float scale : new float[]{1f, 1.3f}) {
                final float fontScale = scale;
                main(() -> {
                    Configuration config = new Configuration(host.getResources().getConfiguration());
                    config.fontScale = fontScale;
                    host.getResources().updateConfiguration(config, host.getResources().getDisplayMetrics());
                });
                for (boolean empty : new boolean[]{false, true}) {
                    final boolean emptyState = empty;
                    main(() -> host.prepare(this, emptyState));
                    String prefix = (scale == 1f ? "normal" : "large") + (empty ? "-empty-" : "-populated-");
                    for (String tab : new String[]{"dashboard", "tasks", "payments", "properties", "more"}) {
                        main(() -> host.render(tab, "renderCurrentTab"));
                        capture(prefix + tab, tab);
                    }
                    if (!empty) {
                        for (String method : new String[]{"renderTenants", "renderMeters", "renderTariffs", "renderExpenses", "renderUtilities"}) {
                            main(() -> host.render("more", method));
                            capture(prefix + method, "more");
                        }
                    }
                }
            }
            main(() -> {
                host.prepare(this, false);
                JSONObject dashboard = ((JSONObject) host.get("bootstrap")).getJSONObject("dashboard");
                JSONObject summary = (JSONObject) host.invoke("premiumMonthSummary", new Class<?>[]{JSONObject.class}, dashboard);
                if (summary.getDouble("salary_paid") != 1234567 || summary.getDouble("advance_paid") != 12340) throw new AssertionError("Use server financial values");
                java.util.Calendar month = (java.util.Calendar) host.get("selectedMonth");
                month.add(java.util.Calendar.MONTH, 1);
                host.set("progressRentLoading", true);
                summary = (JSONObject) host.invoke("premiumMonthSummary", new Class<?>[]{JSONObject.class}, dashboard);
                if (summary.length() != 0) throw new AssertionError("Stale month must not fabricate financial totals");
                host.render("dashboard", "renderCurrentTab");
                StringBuilder text = new StringBuilder();
                inspect((View) host.get("content"), text, new JSONArray());
                if (!text.toString().contains("Сводка загружается")) throw new AssertionError("Missing summary loading state");
                if (text.toString().contains("Зарплата · получено")) throw new AssertionError("Missing summary shown as money");
            });
            capture("missing-month-summary", "dashboard");
            main(() -> {
                host.prepare(this, false);
                JSONArray charges = (JSONArray) host.get("progressRentCharges");
                charges.getJSONObject(0).put("personal_status", "paid").put("personal_paid", 12000).put("apartment", "БД4");
                View details = (View) host.invoke("salaryDetails", new Class<?>[]{JSONArray.class}, charges);
                StringBuilder text = new StringBuilder();
                inspect(details, text, new JSONArray());
                if (!text.toString().contains("Анна") || !text.toString().contains("Иван")) throw new AssertionError("Salary detail tenant names are incorrect");
                if (!text.toString().contains("БД4") || !text.toString().contains("Получено") || !text.toString().contains("Ожидается")) throw new AssertionError("Salary detail missing apartment or personal status");
                ViewGroup content = (ViewGroup) host.get("content");
                content.removeAllViews(); content.addView(details);
                charges.getJSONObject(0).put("personal_status", "partial").put("personal_paid", 5000)
                    .put("payments", new JSONArray("[{\"channel\":\"personal\",\"status\":\"accepted\",\"paid_at\":\"2026-09-11T12:00:00\"}]"));
                View partial = (View) host.invoke("salaryDetails", new Class<?>[]{JSONArray.class}, charges);
                StringBuilder partialText = new StringBuilder();
                inspect(partial, partialText, new JSONArray());
                String remaining = (String) host.invoke("money", new Class<?>[]{double.class}, 7000d);
                if (!partialText.toString().contains("Долг  " + remaining)
                    || !partialText.toString().contains("Факт: 11.09")) throw new AssertionError("Partial salary amount or payment date is incorrect");
            });
            capture("salary-by-apartment", "dashboard");
            writeReport();
            result.putString("stream", "UI_SMOKE_PASS " + captures.length() + " screenshots; " + output.getAbsolutePath() + "\n");
            finish(Activity.RESULT_OK, result);
        } catch (Throwable error) {
            android.util.Log.e("RentalUiSmoke", "UI smoke failed", error);
            result.putString("stream", "UI_SMOKE_FAIL " + android.util.Log.getStackTraceString(error) + "\n");
            finish(Activity.RESULT_CANCELED, result);
        }
    }

    private interface CheckedAction { void run() throws Exception; }

    private void main(CheckedAction action) throws Exception {
        final Throwable[] error = {null};
        runOnMainSync(() -> { try { action.run(); } catch (Throwable failure) { error[0] = failure; } });
        if (error[0] != null) throw new Exception("UI action failed", error[0]);
        waitForIdleSync();
    }

    private void capture(String name, String tab) throws Exception {
        Thread.sleep(250);
        JSONObject record = new JSONObject().put("name", name);
        main(() -> {
            ViewGroup nav = (ViewGroup) host.get("bottomBar");
            if (nav.getChildCount() != 5) throw new AssertionError("Expected five navigation tabs: " + name);
            if (nav.getHeight() <= 0 || nav.getWidth() <= 0) throw new AssertionError("Navigation not laid out: " + name);
            ViewGroup content = (ViewGroup) host.get("content");
            if (content.getChildCount() == 0) throw new AssertionError("Empty render tree: " + name);
            StringBuilder text = new StringBuilder();
            JSONArray clipped = new JSONArray();
            inspect(host.getWindow().getDecorView(), text, clipped);
            record.put("text", text.toString()).put("clipped_text", clipped);
        });
        Bitmap screenshot = getUiAutomation().takeScreenshot();
        if (screenshot == null) throw new AssertionError("Screenshot unavailable: " + name);
        try (FileOutputStream stream = new FileOutputStream(new File(output, name + ".png"))) {
            screenshot.compress(Bitmap.CompressFormat.PNG, 100, stream);
        }
        screenshot.recycle();
        captures.put(record);
        Bundle progress = new Bundle();
        progress.putString("stream", "Captured " + name + "\n");
        sendStatus(0, progress);
    }

    private void inspect(View view, StringBuilder text, JSONArray clipped) {
        if (view.getVisibility() != View.VISIBLE) return;
        if (view instanceof TextView) {
            TextView label = (TextView) view;
            String value = label.getText().toString();
            if (!value.isEmpty()) text.append(value).append('\n');
            if (label.getLayout() != null && label.getHeight() > 0 && !value.isEmpty()) {
                int available = label.getHeight() - label.getCompoundPaddingTop() - label.getCompoundPaddingBottom();
                if (label.getLayout().getHeight() > available + 2) clipped.put(value);
            }
        }
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++) inspect(group.getChildAt(i), text, clipped);
        }
    }

    private void writeReport() throws Exception {
        JSONObject report = new JSONObject().put("synthetic", true).put("internet_permission", false).put("captures", captures);
        try (FileOutputStream stream = new FileOutputStream(new File(output, "report.json"))) {
            stream.write(report.toString(2).getBytes(StandardCharsets.UTF_8));
        }
    }
}
