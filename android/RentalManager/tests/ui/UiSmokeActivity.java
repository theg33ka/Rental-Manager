package ru.rentalmanager.mobile;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import android.content.pm.ActivityInfo;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.Calendar;

public final class UiSmokeActivity extends Activity {
    MainActivity screen;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        TextView placeholder = new TextView(this);
        placeholder.setText("Isolated synthetic UI smoke");
        setContentView(placeholder);
    }

    void prepare(Instrumentation instrumentation, boolean empty) throws Exception {
        ActivityInfo info = getPackageManager().getActivityInfo(getComponentName(), 0);
        screen = (MainActivity) instrumentation.newActivity(MainActivity.class, this,
            getWindow().getDecorView().getWindowToken(), getApplication(), new Intent(this, UiSmokeActivity.class), info,
            "Synthetic Rental Manager", null, "ui-smoke", null);
        screen.setTheme(R.style.AppTheme);
        NotificationPrefs.setBaseUrl(this, "https://synthetic.invalid");
        set("api", new ApiClient(this));
        Calendar month = Calendar.getInstance();
        month.set(2026, Calendar.SEPTEMBER, 19, 12, 0, 0);
        set("selectedMonth", month);
        JSONObject payload = empty ? emptyPayload() : populatedPayload();
        invoke("applyAppState", new Class<?>[]{JSONObject.class}, payload);
        set("bootstrapLoadedAt", System.currentTimeMillis() + 3600000L);
        set("progressRentMonthKey", "2026-09");
        set("progressRentCharges", payload.getJSONArray("rent_charges"));
        set("progressUtilityBills", payload.getJSONArray("utility_bills"));
        set("progressMonthSummary", payload.getJSONObject("bootstrap").getJSONObject("dashboard").getJSONObject("month_summary"));
        invoke("buildShell");
        View frame = (View) get("appFrame");
        if (frame.getParent() instanceof ViewGroup) ((ViewGroup) frame.getParent()).removeView(frame);
        setContentView(frame);
        MobileUi.applyWindow(this, frame);
    }

    void render(String tab, String method) throws Exception {
        set("currentTab", tab);
        invoke("buildBottomNav");
        ((ViewGroup) get("content")).removeAllViews();
        invoke(method);
        invoke("hideLoadingCard");
    }

    Object get(String name) throws Exception {
        Field field = MainActivity.class.getDeclaredField(name);
        field.setAccessible(true);
        return field.get(screen);
    }

    void set(String name, Object value) throws Exception {
        Field field = MainActivity.class.getDeclaredField(name);
        field.setAccessible(true);
        field.set(screen, value);
    }

    Object invoke(String name) throws Exception { return invoke(name, new Class<?>[0]); }

    Object invoke(String name, Class<?>[] types, Object... args) throws Exception {
        Method method = MainActivity.class.getDeclaredMethod(name, types);
        method.setAccessible(true);
        return method.invoke(screen, args);
    }

    private JSONObject emptyPayload() throws Exception {
        JSONObject summary = new JSONObject("{\"year\":2026,\"month\":9,\"salary_paid\":0,\"salary_due\":0,\"bill_payment_paid\":0,\"bill_payment_due\":0,\"advance_paid\":0,\"advance_due\":0,\"occupied\":0,\"total_apartments\":0,\"paid_count\":0,\"pending_count\":0,\"overdue_count\":0}");
        JSONObject bootstrap = new JSONObject().put("dashboard", new JSONObject().put("month_summary", summary));
        for (String key : new String[]{"objects", "leases", "meters", "services"}) bootstrap.put(key, new JSONArray());
        bootstrap.put("settings", new JSONObject());
        bootstrap.put("auth", new JSONObject().put("role", "owner"));
        JSONObject payload = new JSONObject().put("bootstrap", bootstrap);
        for (String key : new String[]{"rent_charges", "utility_bills", "expenses", "tariffs", "utility_timeline", "message_targets", "suspicious_receipts"}) payload.put(key, new JSONArray());
        return payload;
    }

    private JSONObject populatedPayload() throws Exception {
        JSONObject payload = emptyPayload();
        JSONObject bootstrap = payload.getJSONObject("bootstrap");
        JSONObject summary = bootstrap.getJSONObject("dashboard").getJSONObject("month_summary");
        summary.put("salary_paid", 1234567).put("salary_due", 2345678).put("bill_payment_paid", 32540)
            .put("bill_payment_due", 42860).put("advance_paid", 12340).put("advance_due", 17650)
            .put("occupied", 2).put("total_apartments", 3).put("paid_count", 1).put("pending_count", 1)
            .put("overdue_count", 1).put("utility_bills_issued", true).put("utility_provider_paid", false);
        bootstrap.put("objects", new JSONArray("[{\"id\":1,\"name\":\"Тестовый дом на Центральной\",\"apartments\":[{\"id\":11,\"name\":\"Квартира 101\",\"object_name\":\"Тестовый дом\",\"active\":true,\"active_lease_id\":21},{\"id\":12,\"name\":\"Квартира 102\",\"object_name\":\"Тестовый дом\",\"active\":true,\"active_lease_id\":22},{\"id\":13,\"name\":\"Студия 103\",\"active\":true}]}]"));
        bootstrap.put("leases", new JSONArray("[{\"id\":21,\"tenant\":\"Анна Тестовая\",\"apartment_id\":11,\"object\":\"Тестовый дом\",\"apartment\":\"Квартира 101\",\"start_date\":\"2026-01-01\",\"ip_amount\":21000,\"personal_amount\":12000},{\"id\":22,\"tenant\":\"Иван Примеров\",\"apartment_id\":12,\"object\":\"Тестовый дом\",\"apartment\":\"Квартира 102\",\"start_date\":\"2026-03-01\",\"ip_amount\":19000,\"personal_amount\":11000}]"));
        bootstrap.put("services", new JSONArray("[{\"id\":31,\"name\":\"Электроэнергия\",\"active\":true}]"));
        bootstrap.put("meters", new JSONArray("[{\"id\":41,\"object\":\"Тестовый дом\",\"name\":\"Электричество · квартира 101\",\"scope\":\"apartment\",\"last_reading_date\":\"2026-09-15\",\"last_reading\":14528.6}]"));
        JSONArray charges = new JSONArray("[{\"id\":51,\"lease_id\":21,\"tenant\":\"Анна Тестовая\",\"object\":\"Тестовый дом\",\"apartment\":\"Квартира 101\",\"period_start\":\"2026-09-01\",\"period_end\":\"2026-09-30\",\"due_date\":\"2026-09-10\",\"status\":\"partial\",\"ip_due\":21000,\"ip_paid\":21000,\"personal_due\":12000,\"personal_paid\":5000,\"total_due\":33000,\"total_paid\":26000},{\"id\":52,\"lease_id\":22,\"tenant\":\"Иван Примеров\",\"object\":\"Тестовый дом\",\"apartment\":\"Квартира 102\",\"period_start\":\"2026-09-01\",\"period_end\":\"2026-09-30\",\"due_date\":\"2026-09-25\",\"status\":\"pending\",\"ip_due\":19000,\"personal_due\":11000,\"total_due\":30000}]");
        payload.put("rent_charges", charges);
        bootstrap.getJSONObject("dashboard").put("rent_partial", new JSONArray().put(charges.getJSONObject(0)))
            .put("rent_overdue", new JSONArray().put(charges.getJSONObject(0))).put("rent_today", new JSONArray().put(charges.getJSONObject(1)));
        payload.put("utility_bills", new JSONArray("[{\"id\":61,\"service_id\":31,\"object\":\"Тестовый дом\",\"service\":\"Электроэнергия\",\"period_start\":\"2026-09-01\",\"period_end\":\"2026-09-30\",\"status\":\"issued\",\"provider_paid\":false,\"total_cost\":3820,\"lines\":[{\"id\":62,\"lease_id\":21,\"tenant\":\"Анна Тестовая\",\"object\":\"Тестовый дом\",\"apartment\":\"Квартира 101\",\"service\":\"Электроэнергия\",\"status\":\"overdue\",\"due_date\":\"2026-09-12\",\"total_amount\":3820,\"paid_amount\":1000}]}]"));
        payload.put("tariffs", new JSONArray("[{\"id\":71,\"service\":\"Электроэнергия\",\"name\":\"Основной тариф\",\"starts_on\":\"2026-01-01\",\"tiers\":\"до 1000 кВт·ч: 5,40 ₽\"}]"));
        payload.put("expenses", new JSONArray("[{\"id\":81,\"category\":\"Ремонт\",\"amount\":4250,\"object\":\"Тестовый дом\",\"apartment\":\"Квартира 101\",\"expense_date\":\"2026-09-17\",\"description\":\"Замена смесителя и расходные материалы\",\"compensation_status\":\"pending\"}]"));
        payload.put("message_targets", new JSONArray("[{\"lease_id\":21,\"tenant\":\"Анна Тестовая\",\"object\":\"Тестовый дом\",\"apartment\":\"Квартира 101\"}]"));
        return payload;
    }
}
