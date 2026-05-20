package ru.rentalmanager.mobile;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.text.InputType;
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

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

public class MainActivity extends Activity {
    private static final int REQUEST_NOTIFICATIONS = 7102;

    private final int bg = Color.rgb(7, 10, 14);
    private final int surface = Color.rgb(18, 23, 31);
    private final int surface2 = Color.rgb(26, 33, 43);
    private final int hairline = Color.rgb(44, 54, 68);
    private final int text = Color.rgb(243, 247, 251);
    private final int muted = Color.rgb(142, 153, 167);
    private final int blue = Color.rgb(10, 132, 255);
    private final int green = Color.rgb(48, 209, 88);
    private final int orange = Color.rgb(255, 159, 10);
    private final int red = Color.rgb(255, 69, 58);

    private ApiClient api;
    private LinearLayout root;
    private LinearLayout content;
    private LinearLayout bottomBar;
    private TextView screenTitle;
    private TextView screenSubtitle;
    private JSONObject bootstrap;
    private JSONArray rentCharges = new JSONArray();
    private JSONArray utilityBills = new JSONArray();
    private JSONArray utilityTimeline = new JSONArray();
    private JSONArray expenses = new JSONArray();
    private JSONArray tariffs = new JSONArray();
    private JSONArray messageTargets = new JSONArray();
    private JSONArray suspiciousReceipts = new JSONArray();
    private String currentTab = "dashboard";
    private String servicesMode = "utilities";

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
        NotificationHelper.ensureChannels(this);
        requestNotificationPermission();
        buildShell();
        ReminderScheduler.schedule(this);
        if (!NotificationPrefs.hasCustomBaseUrl(this)) {
            showHostDialog();
        } else {
            checkAuthAndLoad();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (screenSubtitle != null) screenSubtitle.setText(api.baseUrl());
    }

    private void buildShell() {
        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(bg);

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setPadding(dp(18), dp(18), dp(14), dp(10));

        LinearLayout titleBox = new LinearLayout(this);
        titleBox.setOrientation(LinearLayout.VERTICAL);
        TextView eyebrow = label("Rental Manager", 12, muted, false);
        screenTitle = label("Пульт", 31, text, true);
        screenSubtitle = label(api.baseUrl(), 12, muted, false);
        titleBox.addView(eyebrow);
        titleBox.addView(screenTitle);
        titleBox.addView(screenSubtitle);
        header.addView(titleBox, new LinearLayout.LayoutParams(0, -2, 1));

        Button host = pillButton("Хост", false);
        Button refresh = pillButton("Обновить", false);
        header.addView(host);
        header.addView(space(8), new LinearLayout.LayoutParams(dp(8), 1));
        header.addView(refresh);
        root.addView(header);

        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(false);
        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(dp(14), dp(4), dp(14), dp(18));
        scroll.addView(content);
        root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));

        bottomBar = new LinearLayout(this);
        bottomBar.setOrientation(LinearLayout.HORIZONTAL);
        bottomBar.setGravity(Gravity.CENTER);
        bottomBar.setPadding(dp(8), dp(8), dp(8), dp(8));
        bottomBar.setBackgroundColor(Color.rgb(11, 15, 21));
        root.addView(bottomBar, new LinearLayout.LayoutParams(-1, dp(72)));
        setContentView(root);

        host.setOnClickListener(v -> showHostDialog());
        refresh.setOnClickListener(v -> loadCurrentTab(true));
        buildBottomNav();
    }

    private void buildBottomNav() {
        bottomBar.removeAllViews();
        addNav("Дом", "dashboard");
        addNav("Жильцы", "tenants");
        addNav("Оплаты", "payments");
        addNav("Учёт", "services");
        addNav("Ещё", "more");
    }

    private void addNav(String title, String tab) {
        Button button = navButton(title, tab.equals(currentTab));
        button.setOnClickListener(v -> {
            currentTab = tab;
            buildBottomNav();
            loadCurrentTab(false);
        });
        bottomBar.addView(button, new LinearLayout.LayoutParams(0, -1, 1));
    }

    private void checkAuthAndLoad() {
        runApi("Проверяю вход", () -> api.getJson("/api/auth/status"), value -> {
            JSONObject status = (JSONObject) value;
            if (!status.optBoolean("authenticated")) {
                showLogin();
            } else {
                loadCurrentTab(true);
            }
        });
    }

    private void showLogin() {
        screenTitle.setText("Вход");
        screenSubtitle.setText(api.baseUrl());
        content.removeAllViews();
        addHero("Нативный пульт аренды", "PIN owner открывает полный контроль. Guest оставим для отчётов, а всё серьёзное пусть не шляется без спроса.");
        LinearLayout card = card();
        EditText pin = input("PIN-код", true);
        pin.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD);
        CheckBox remember = checkbox("Запомнить устройство");
        remember.setChecked(true);
        Button login = primaryButton("Войти");
        Button host = secondaryButton("Сменить хост");
        card.addView(field("PIN", pin));
        card.addView(remember);
        card.addView(login, new LinearLayout.LayoutParams(-1, dp(50)));
        card.addView(host, new LinearLayout.LayoutParams(-1, dp(46)));
        content.addView(card);
        login.setOnClickListener(v -> runApi("Вхожу", () -> api.login(pin.getText().toString(), remember.isChecked()), value -> loadCurrentTab(true)));
        host.setOnClickListener(v -> showHostDialog());
    }

    private void loadCurrentTab(boolean force) {
        if ("dashboard".equals(currentTab)) {
            runApi("Загружаю дашборд", () -> {
                bootstrap = api.getJson("/api/bootstrap");
                return bootstrap;
            }, value -> renderDashboard());
        } else if ("tenants".equals(currentTab)) {
            runApi("Загружаю жильцов", () -> {
                bootstrap = api.getJson("/api/bootstrap");
                return bootstrap;
            }, value -> renderTenants());
        } else if ("payments".equals(currentTab)) {
            runApi("Загружаю оплаты", () -> {
                bootstrap = api.getJson("/api/bootstrap");
                rentCharges = api.getArray("/api/rent-charges");
                suspiciousReceipts = api.getArray("/api/payment-receipts/suspicious");
                return null;
            }, value -> renderPayments());
        } else if ("services".equals(currentTab)) {
            runApi("Загружаю учёт", () -> {
                bootstrap = api.getJson("/api/bootstrap");
                utilityBills = api.getArray("/api/utility-bills");
                utilityTimeline = api.getArray("/api/utilities/timeline");
                expenses = api.getArray("/api/expenses");
                tariffs = api.getArray("/api/tariffs");
                return null;
            }, value -> renderServices());
        } else {
            runApi("Загружаю настройки", () -> {
                bootstrap = api.getJson("/api/bootstrap");
                messageTargets = api.getArray("/api/messages/targets");
                return null;
            }, value -> renderMore());
        }
    }

    private void renderDashboard() {
        screenTitle.setText("Пульт");
        screenSubtitle.setText(api.baseUrl());
        content.removeAllViews();
        JSONObject dashboard = obj(bootstrap, "dashboard");
        addHero("Дела по аренде", "Критичные карточки сверху, спокойные снизу. Всё нативно, без браузерных подпорок.");

        LinearLayout grid = new LinearLayout(this);
        grid.setOrientation(LinearLayout.VERTICAL);
        content.addView(grid);
        addMetricRow(grid, "Просрочка аренды", len(dashboard, "rent_overdue"), "Коммуналка", len(dashboard, "utility_overdue"));
        addMetricRow(grid, "Сегодня оплата", len(dashboard, "rent_today"), "Отчёты", len(dashboard, "monthly_reports"));
        addMetricRow(grid, "Чеки проверить", len(dashboard, "suspicious_receipts"), "Счётчики", len(dashboard, "stale_readings"));

        JSONArray reports = arr(dashboard, "monthly_reports");
        if (reports.length() > 0) {
            content.addView(section("Месячные отчёты"));
            forEach(reports, item -> {
                LinearLayout card = card();
                card.addView(label(item.optString("title", "Отчёт"), 18, text, true));
                card.addView(label(item.optString("issue_count", "0") + " проблем · " + item.optString("severity"), 13, muted, false));
                Button download = secondaryButton("Скачать");
                download.setOnClickListener(v -> download("/api/reports/monthly.xlsx?year=" + item.optInt("year") + "&month=" + item.optInt("month") + "&kind=" + item.optString("kind", "full"), "monthly.xlsx"));
                card.addView(download);
                content.addView(card);
            });
        }

        content.addView(section("Требует внимания"));
        addAttentionCards(dashboard, "rent_overdue", "Просрочена аренда", red, "payments");
        addAttentionCards(dashboard, "rent_partial", "Частичная аренда", orange, "payments");
        addAttentionCards(dashboard, "rent_today", "Сегодня срок оплаты", blue, "payments");
        addAttentionCards(dashboard, "utility_overdue", "Долг по коммуналке", red, "services");
        addAttentionCards(dashboard, "utility_issued", "Коммуналка выставлена", orange, "services");
        addAttentionCards(dashboard, "manual_debts", "Ручной долг", orange, "payments");
        addAttentionCards(dashboard, "stale_readings", "Давно нет показаний", blue, "services");
        addAttentionCards(dashboard, "suspicious_receipts", "Подозрительный чек", red, "payments");
        if (content.getChildCount() < 5) {
            LinearLayout ok = card();
            ok.addView(label("Критичных задач нет", 19, text, true));
            ok.addView(label("Тихий день. Подозрительно, но приятно.", 14, muted, false));
            content.addView(ok);
        }
    }

    private void addAttentionCards(JSONObject dashboard, String key, String title, int color, String targetTab) {
        JSONArray items = arr(dashboard, key);
        forEach(items, item -> {
            LinearLayout card = cardWithAccent(color);
            card.addView(label(title, 17, text, true));
            String where = joinNonEmpty(item.optString("object"), item.optString("apartment"));
            String person = item.optString("tenant");
            String amount = item.has("debt") ? money(item.optDouble("debt")) : item.has("total_cost") ? money(item.optDouble("total_cost")) : "";
            card.addView(label(joinNonEmpty(where, person, amount), 14, muted, false));
            Button open = secondaryButton("Открыть");
            open.setOnClickListener(v -> {
                currentTab = targetTab;
                buildBottomNav();
                loadCurrentTab(false);
            });
            card.addView(open);
            content.addView(card);
        });
    }

    private void renderTenants() {
        screenTitle.setText("Жильцы");
        content.removeAllViews();
        addHero("Заселение и квартиры", "Карточки вместо таблиц. Да, таблицы тоже умеем, но пальцем по ним грустно.");
        Button add = primaryButton("Заселить жильца");
        add.setOnClickListener(v -> showOnboardDialog(null));
        content.addView(add, new LinearLayout.LayoutParams(-1, dp(52)));

        content.addView(section("Активные договоры"));
        JSONArray leases = arr(bootstrap, "leases");
        forEach(leases, lease -> {
            if (!lease.optBoolean("active", true)) return;
            LinearLayout card = card();
            card.addView(label(lease.optString("tenant", "Жилец"), 19, text, true));
            card.addView(label(joinNonEmpty(lease.optString("object"), lease.optString("apartment")) + " · с " + lease.optString("start_date"), 14, muted, false));
            card.addView(label("ИП " + money(lease.optDouble("ip_amount")) + " · перевод " + money(lease.optDouble("personal_amount")), 14, muted, false));
            LinearLayout actions = row();
            actions.addView(smallButton("Изменить", v -> showOnboardDialog(lease)), new LinearLayout.LayoutParams(0, dp(42), 1));
            actions.addView(smallButton(lease.optBoolean("ignored") ? "Учитывать" : "Скрыть", v -> toggleLeaseIgnore(lease)), new LinearLayout.LayoutParams(0, dp(42), 1));
            actions.addView(smallButton("Выезд", v -> moveOut(lease)), new LinearLayout.LayoutParams(0, dp(42), 1));
            card.addView(actions);
            content.addView(card);
        });

        content.addView(section("Квартиры"));
        JSONArray objects = arr(bootstrap, "objects");
        forEach(objects, object -> {
            JSONArray apartments = arr(object, "apartments");
            forEach(apartments, apartment -> {
                LinearLayout card = card();
                card.addView(label(object.optString("name") + " · " + apartment.optString("name"), 17, text, true));
                card.addView(label(apartment.optBoolean("active", true) ? "активна" : "неактивна", 13, apartment.optBoolean("active", true) ? green : muted, false));
                Button toggle = secondaryButton(apartment.optBoolean("active", true) ? "Отключить" : "Включить");
                toggle.setOnClickListener(v -> toggleApartment(apartment));
                card.addView(toggle);
                content.addView(card);
            });
        });
    }

    private void showOnboardDialog(JSONObject existing) {
        boolean editing = existing != null;
        LinearLayout form = dialogForm();
        Spinner apartment = spinner(apartmentLabels(), apartmentIds());
        EditText name = input("ФИО", false);
        EditText phone = input("+7...", false);
        EditText telegram = input("@username", false);
        EditText whatsapp = input("+7...", false);
        EditText start = input("2026-05-21", false);
        EditText end = input("", false);
        EditText day = input("день оплаты", false);
        EditText ip = input("ИП", false);
        EditText personal = input("перевод", false);
        EditText deposit = input("залог", false);
        EditText notes = input("комментарий", false);
        if (editing) {
            selectSpinnerValue(apartment, String.valueOf(existing.optInt("apartment_id")));
            name.setText(existing.optString("tenant"));
            phone.setText(existing.optString("phone"));
            telegram.setText(existing.optString("telegram"));
            whatsapp.setText(existing.optString("whatsapp"));
            start.setText(existing.optString("start_date"));
            end.setText(existing.optString("end_date"));
            day.setText(String.valueOf(existing.optInt("payment_day")));
            ip.setText(String.valueOf(existing.optDouble("ip_amount")));
            personal.setText(String.valueOf(existing.optDouble("personal_amount")));
            deposit.setText(String.valueOf(existing.optDouble("deposit_amount")));
            notes.setText(existing.optString("notes"));
        } else {
            start.setText(today());
        }
        form.addView(field("Квартира", apartment));
        form.addView(field("ФИО", name));
        form.addView(field("Телефон", phone));
        form.addView(field("Telegram", telegram));
        form.addView(field("WhatsApp", whatsapp));
        form.addView(field("Дата заезда", start));
        form.addView(field("Дата выезда", end));
        form.addView(field("День оплаты", day));
        form.addView(field("Платёж на ИП", ip));
        form.addView(field("Личный перевод", personal));
        form.addView(field("Залог", deposit));
        form.addView(field("Комментарий", notes));
        new AlertDialog.Builder(this)
            .setTitle(editing ? "Изменить жильца" : "Заселить жильца")
            .setView(wrapDialog(form))
            .setPositiveButton("Сохранить", (dialog, which) -> {
                try {
                    JSONObject body = new JSONObject();
                    body.put("apartment_id", Integer.parseInt(spinnerValue(apartment)));
                    body.put("full_name", name.getText().toString());
                    body.put("phone", phone.getText().toString());
                    body.put("telegram", telegram.getText().toString());
                    body.put("whatsapp", whatsapp.getText().toString());
                    body.put("start_date", start.getText().toString());
                    body.put("end_date", end.getText().toString());
                    body.put("payment_day", emptyToNull(day));
                    body.put("ip_amount", number(ip));
                    body.put("personal_amount", number(personal));
                    body.put("deposit_amount", number(deposit));
                    body.put("notes", notes.getText().toString());
                    if (editing) {
                        int id = existing.optInt("id");
                        runApi("Сохраняю", () -> api.patchJson("/api/leases/" + id, body), value -> loadCurrentTab(true));
                    } else {
                        runApi("Заселяю", () -> api.postJson("/api/leases/onboard", body), value -> loadCurrentTab(true));
                    }
                } catch (Exception ex) {
                    toast(ex.getMessage());
                }
            })
            .setNegativeButton("Отмена", null)
            .show();
    }

    private void renderPayments() {
        screenTitle.setText("Оплаты");
        content.removeAllViews();
        addHero("Аренда и платежи", "Долги, отсрочки, ручные оплаты и чеки. Деньги любят порядок, кто бы спорил.");
        LinearLayout actions = row();
        actions.addView(smallButton("Сгенерировать", v -> runApi("Генерирую", () -> api.postJson("/api/rent-charges/generate", new JSONObject()), value -> loadCurrentTab(true))), new LinearLayout.LayoutParams(0, dp(44), 1));
        actions.addView(smallButton("Ручная оплата", v -> showManualPaymentDialog()), new LinearLayout.LayoutParams(0, dp(44), 1));
        content.addView(actions);

        content.addView(section("Аренда"));
        forEach(rentCharges, charge -> {
            LinearLayout card = cardWithAccent(statusColor(charge.optString("status")));
            card.addView(label(joinNonEmpty(charge.optString("object"), charge.optString("apartment")), 18, text, true));
            card.addView(label(charge.optString("tenant") + " · срок " + charge.optString("due_date"), 14, muted, false));
            card.addView(label("К оплате " + money(charge.optDouble("total_due")) + " · долг " + money(charge.optDouble("debt")), 14, muted, false));
            card.addView(label(charge.optString("status"), 13, statusColor(charge.optString("status")), true));
            LinearLayout row1 = row();
            row1.addView(smallButton("ИП", v -> payRent(charge, "ip")), new LinearLayout.LayoutParams(0, dp(42), 1));
            row1.addView(smallButton("Перевод", v -> payRent(charge, "personal")), new LinearLayout.LayoutParams(0, dp(42), 1));
            row1.addView(smallButton("Отсрочка", v -> deferRent(charge)), new LinearLayout.LayoutParams(0, dp(42), 1));
            card.addView(row1);
            content.addView(card);
        });

        if (suspiciousReceipts.length() > 0) {
            content.addView(section("Чеки на проверку"));
            forEach(suspiciousReceipts, receipt -> {
                LinearLayout card = cardWithAccent(red);
                card.addView(label(money(receipt.optDouble("amount")), 18, text, true));
                card.addView(label(joinNonEmpty(receipt.optString("apartment"), receipt.optString("tenant"), receipt.optString("recipient_name")), 14, muted, false));
                LinearLayout actions2 = row();
                actions2.addView(smallButton("Принять", v -> moderateReceipt(receipt, "accept")), new LinearLayout.LayoutParams(0, dp(42), 1));
                actions2.addView(smallButton("Скрыть", v -> ignoreReceipt(receipt)), new LinearLayout.LayoutParams(0, dp(42), 1));
                card.addView(actions2);
                content.addView(card);
            });
        }
    }

    private void showManualPaymentDialog() {
        LinearLayout form = dialogForm();
        Spinner lease = spinner(leaseLabels(), leaseIds());
        Spinner kind = spinner(new String[]{"Аренда", "Коммуналка"}, new String[]{"rent", "utility"});
        Spinner channel = spinner(new String[]{"Перевод", "ИП", "На расходы"}, new String[]{"personal", "ip", "expense_fund"});
        EditText amount = input("0", false);
        EditText paidAt = input("2026-05-21T12:00", false);
        EditText notes = input("комментарий", false);
        paidAt.setText(today() + "T12:00");
        form.addView(field("Жилец", lease));
        form.addView(field("Тип", kind));
        form.addView(field("Канал", channel));
        form.addView(field("Сумма", amount));
        form.addView(field("Дата и время", paidAt));
        form.addView(field("Комментарий", notes));
        new AlertDialog.Builder(this)
            .setTitle("Ручная оплата")
            .setView(wrapDialog(form))
            .setPositiveButton("Добавить", (dialog, which) -> {
                try {
                    JSONObject body = new JSONObject();
                    body.put("lease_id", Integer.parseInt(spinnerValue(lease)));
                    body.put("kind", spinnerValue(kind));
                    body.put("channel", spinnerValue(channel));
                    body.put("source", "manual");
                    body.put("amount", number(amount));
                    body.put("paid_at", paidAt.getText().toString());
                    body.put("notes", notes.getText().toString());
                    runApi("Добавляю оплату", () -> api.postJson("/api/payment-receipts/manual", body), value -> loadCurrentTab(true));
                } catch (Exception ex) {
                    toast(ex.getMessage());
                }
            })
            .setNegativeButton("Отмена", null)
            .show();
    }

    private void renderServices() {
        screenTitle.setText("Учёт");
        content.removeAllViews();
        addHero("Коммуналка, счётчики, расходы", "Всё, что обычно расползается по вкладкам, собрано в один рабочий экран.");
        LinearLayout tabs = row();
        tabs.addView(modeButton("Коммуналка", "utilities"), new LinearLayout.LayoutParams(0, dp(44), 1));
        tabs.addView(modeButton("Счётчики", "meters"), new LinearLayout.LayoutParams(0, dp(44), 1));
        tabs.addView(modeButton("Тарифы", "tariffs"), new LinearLayout.LayoutParams(0, dp(44), 1));
        tabs.addView(modeButton("Расходы", "expenses"), new LinearLayout.LayoutParams(0, dp(44), 1));
        content.addView(tabs);
        if ("meters".equals(servicesMode)) renderMeters();
        else if ("tariffs".equals(servicesMode)) renderTariffs();
        else if ("expenses".equals(servicesMode)) renderExpenses();
        else renderUtilities();
    }

    private void renderUtilities() {
        Button calculate = primaryButton("Рассчитать коммуналку");
        calculate.setOnClickListener(v -> showUtilityCalcDialog());
        content.addView(calculate, new LinearLayout.LayoutParams(-1, dp(52)));
        content.addView(section("Счета"));
        forEach(utilityBills, bill -> {
            LinearLayout card = cardWithAccent(bill.optBoolean("provider_paid") ? green : orange);
            card.addView(label(bill.optString("object") + " · " + bill.optString("service"), 18, text, true));
            card.addView(label(bill.optString("period_start") + " — " + bill.optString("period_end"), 13, muted, false));
            card.addView(label("Итого " + money(bill.optDouble("total_cost")), 15, text, true));
            LinearLayout row = row();
            row.addView(smallButton("Выставить", v -> issueBill(bill)), new LinearLayout.LayoutParams(0, dp(42), 1));
            row.addView(smallButton("Поставщик оплачен", v -> providerPaid(bill)), new LinearLayout.LayoutParams(0, dp(42), 1));
            row.addView(smallButton("Удалить", v -> deleteBill(bill)), new LinearLayout.LayoutParams(0, dp(42), 1));
            card.addView(row);
            content.addView(card);
        });
    }

    private void renderMeters() {
        Button add = primaryButton("Добавить показание");
        add.setOnClickListener(v -> showReadingDialog());
        content.addView(add, new LinearLayout.LayoutParams(-1, dp(52)));
        content.addView(section("Счётчики"));
        forEach(arr(bootstrap, "meters"), meter -> {
            LinearLayout card = card();
            card.addView(label(meter.optString("object") + " · " + meter.optString("name"), 17, text, true));
            card.addView(label(meter.optString("scope") + " · последнее " + meter.optString("last_reading_date", "нет"), 13, muted, false));
            content.addView(card);
        });
    }

    private void renderTariffs() {
        Button add = primaryButton("Добавить тариф");
        add.setOnClickListener(v -> showTariffDialog());
        content.addView(add, new LinearLayout.LayoutParams(-1, dp(52)));
        content.addView(section("Тарифы"));
        forEach(tariffs, tariff -> {
            LinearLayout card = card();
            card.addView(label(tariff.optString("service") + " · " + tariff.optString("name"), 17, text, true));
            card.addView(label("с " + tariff.optString("starts_on") + " · " + tariff.optString("tiers"), 13, muted, false));
            content.addView(card);
        });
    }

    private void renderExpenses() {
        Button add = primaryButton("Добавить расход");
        add.setOnClickListener(v -> showExpenseDialog());
        content.addView(add, new LinearLayout.LayoutParams(-1, dp(52)));
        content.addView(section("Расходы"));
        forEach(expenses, expense -> {
            LinearLayout card = cardWithAccent("pending".equals(expense.optString("compensation_status")) ? orange : green);
            card.addView(label(expense.optString("category", "Расход") + " · " + money(expense.optDouble("amount")), 18, text, true));
            card.addView(label(joinNonEmpty(expense.optString("object"), expense.optString("apartment"), expense.optString("expense_date")), 13, muted, false));
            card.addView(label(expense.optString("description"), 13, muted, false));
            if ("pending".equals(expense.optString("compensation_status"))) {
                card.addView(smallButton("Компенсировано", v -> compensateExpense(expense)));
            }
            content.addView(card);
        });
    }

    private void renderMore() {
        screenTitle.setText("Ещё");
        content.removeAllViews();
        addHero("Отчёты, сообщения, настройки", "Редкие, но важные действия. Прячем не потому что стыдно, а потому что каждый день они не нужны.");

        LinearLayout notif = card();
        notif.addView(label("Пуш-уведомления", 19, text, true));
        notif.addView(label("Частота, тихие часы, типы событий и постоянный debt-alert.", 14, muted, false));
        notif.addView(primaryButton("Настроить", v -> startActivity(new Intent(this, NotificationSettingsActivity.class))));
        content.addView(notif);

        LinearLayout reports = card();
        reports.addView(label("Excel-отчёты", 19, text, true));
        EditText start = input("2026-05-01", false);
        EditText end = input("2026-05-31", false);
        start.setText(firstDay());
        end.setText(today());
        reports.addView(field("С", start));
        reports.addView(field("По", end));
        String[] names = {"Аренда", "Коммуналка", "Расходы", "Долги", "История", "Для хозяина"};
        String[] paths = {"/api/reports/rent.xlsx", "/api/reports/utilities.xlsx", "/api/reports/expenses.xlsx", "/api/reports/debts.xlsx", "/api/reports/history.xlsx", "/api/reports/owner.xlsx"};
        for (int i = 0; i < names.length; i++) {
            final int index = i;
            reports.addView(secondaryButton(names[i], v -> {
                String query = "?start=" + start.getText().toString() + "&end=" + end.getText().toString();
                if (paths[index].contains("debts") || paths[index].contains("history")) query = "";
                download(paths[index] + query, names[index] + ".xlsx");
            }));
        }
        content.addView(reports);

        LinearLayout messages = card();
        messages.addView(label("Сообщения жильцам", 19, text, true));
        messages.addView(label("Превью и отправка через Telegram-бота.", 14, muted, false));
        messages.addView(primaryButton("Открыть получателей", v -> showMessageTargets()));
        messages.addView(secondaryButton("Прогнать напоминания", v -> runApi("Напоминания", () -> api.postJson("/api/reminders/run", new JSONObject()), value -> toast("Напоминания обработаны"))));
        content.addView(messages);

        LinearLayout settings = card();
        settings.addView(label("Настройки сервера", 19, text, true));
        settings.addView(secondaryButton("Открыть", v -> showServerSettingsDialog()));
        settings.addView(secondaryButton("Экспорт базы", v -> download("/api/admin/database-export", "rental-manager-db.json")));
        settings.addView(secondaryButton("Выйти из PIN-сессии", v -> logout()));
        content.addView(settings);
    }

    private void showMessageTargets() {
        LinearLayout list = dialogForm();
        forEach(messageTargets, target -> {
            LinearLayout card = card();
            card.addView(label(target.optString("tenant"), 17, text, true));
            card.addView(label(joinNonEmpty(target.optString("object"), target.optString("apartment"), target.optString("telegram")), 13, muted, false));
            card.addView(smallButton("Все долги", v -> previewMessage(target, "message_all_debts")));
            list.addView(card);
        });
        new AlertDialog.Builder(this).setTitle("Получатели").setView(wrapDialog(list)).setPositiveButton("Закрыть", null).show();
    }

    private void showServerSettingsDialog() {
        JSONObject settings = obj(bootstrap, "settings");
        LinearLayout form = dialogForm();
        EditText appUrl = input("https://...", false);
        EditText ownerChat = input("telegram id", false);
        EditText cutoff = input("2026-05-01", false);
        CheckBox enabled = checkbox("Автонапиоминания жильцам включены");
        appUrl.setText(settings.optString("app_base_url"));
        ownerChat.setText(settings.optString("telegram_owner_chat_id"));
        cutoff.setText(settings.optString("notification_cutoff_date"));
        enabled.setChecked(settings.optBoolean("notifications_enabled"));
        form.addView(field("Публичный URL", appUrl));
        form.addView(field("Owner chat id", ownerChat));
        form.addView(field("Игнорировать долги до", cutoff));
        form.addView(enabled);
        new AlertDialog.Builder(this)
            .setTitle("Настройки сервера")
            .setView(wrapDialog(form))
            .setPositiveButton("Сохранить", (dialog, which) -> {
                try {
                    JSONObject body = new JSONObject();
                    body.put("app_base_url", appUrl.getText().toString());
                    body.put("telegram_owner_chat_id", ownerChat.getText().toString());
                    body.put("notification_cutoff_date", cutoff.getText().toString());
                    body.put("notifications_enabled", enabled.isChecked());
                    runApi("Сохраняю настройки", () -> api.postJson("/api/settings", body), value -> loadCurrentTab(true));
                } catch (Exception ex) {
                    toast(ex.getMessage());
                }
            })
            .setNegativeButton("Отмена", null)
            .show();
    }

    private void previewMessage(JSONObject target, String key) {
        runApi("Готовлю сообщение", () -> {
            JSONObject body = new JSONObject();
            body.put("lease_id", target.optInt("lease_id"));
            body.put("template_key", key);
            return api.postJson("/api/messages/preview", body);
        }, value -> {
            JSONObject preview = (JSONObject) value;
            new AlertDialog.Builder(this)
                .setTitle("Сообщение")
                .setMessage(preview.optString("text", preview.toString()))
                .setPositiveButton("Отправить", (dialog, which) -> runApi("Отправляю", () -> api.postJson("/api/messages/send", obj(preview, "payload")), done -> toast("Отправлено")))
                .setNegativeButton("Отмена", null)
                .show();
        });
    }

    private void showUtilityCalcDialog() {
        LinearLayout form = dialogForm();
        Spinner service = spinner(serviceLabels(), serviceIds());
        EditText start = input("2026-05-01", false);
        EditText end = input("2026-05-31", false);
        CheckBox estimate = checkbox("Разрешить прогноз");
        start.setText(firstDay());
        end.setText(today());
        form.addView(field("Услуга", service));
        form.addView(field("Начало", start));
        form.addView(field("Конец", end));
        form.addView(estimate);
        new AlertDialog.Builder(this)
            .setTitle("Расчёт коммуналки")
            .setView(wrapDialog(form))
            .setPositiveButton("Рассчитать", (dialog, which) -> {
                try {
                    JSONObject body = new JSONObject();
                    body.put("service_id", Integer.parseInt(spinnerValue(service)));
                    body.put("period_start", start.getText().toString());
                    body.put("period_end", end.getText().toString());
                    body.put("allow_estimate", estimate.isChecked());
                    runApi("Считаю", () -> api.postJson("/api/utility-bills/calculate", body), value -> loadCurrentTab(true));
                } catch (Exception ex) {
                    toast(ex.getMessage());
                }
            })
            .setNegativeButton("Отмена", null)
            .show();
    }

    private void showReadingDialog() {
        LinearLayout form = dialogForm();
        Spinner meter = spinner(meterLabels(), meterIds());
        EditText date = input("2026-05-21", false);
        EditText value = input("0", false);
        EditText note = input("комментарий", false);
        date.setText(today());
        form.addView(field("Счётчик", meter));
        form.addView(field("Дата", date));
        form.addView(field("Показание", value));
        form.addView(field("Комментарий", note));
        new AlertDialog.Builder(this)
            .setTitle("Показание")
            .setView(wrapDialog(form))
            .setPositiveButton("Сохранить", (dialog, which) -> {
                try {
                    JSONObject body = new JSONObject();
                    body.put("meter_id", Integer.parseInt(spinnerValue(meter)));
                    body.put("reading_date", date.getText().toString());
                    body.put("value", number(value));
                    body.put("note", note.getText().toString());
                    runApi("Сохраняю", () -> api.postJson("/api/meter-readings", body), done -> loadCurrentTab(true));
                } catch (Exception ex) {
                    toast(ex.getMessage());
                }
            })
            .setNegativeButton("Отмена", null)
            .show();
    }

    private void showTariffDialog() {
        LinearLayout form = dialogForm();
        Spinner service = spinner(serviceLabels(), serviceIds());
        EditText starts = input("2026-05-21", false);
        EditText name = input("Тариф", false);
        EditText tiers = input("1000:4.2; *:7", false);
        starts.setText(today());
        form.addView(field("Услуга", service));
        form.addView(field("Действует с", starts));
        form.addView(field("Название", name));
        form.addView(field("Ступени", tiers));
        new AlertDialog.Builder(this)
            .setTitle("Новый тариф")
            .setView(wrapDialog(form))
            .setPositiveButton("Добавить", (dialog, which) -> {
                try {
                    JSONObject body = new JSONObject();
                    body.put("service_id", Integer.parseInt(spinnerValue(service)));
                    body.put("starts_on", starts.getText().toString());
                    body.put("name", name.getText().toString());
                    body.put("tiers", tiers.getText().toString());
                    runApi("Добавляю", () -> api.postJson("/api/tariffs", body), done -> loadCurrentTab(true));
                } catch (Exception ex) {
                    toast(ex.getMessage());
                }
            })
            .setNegativeButton("Отмена", null)
            .show();
    }

    private void showExpenseDialog() {
        LinearLayout form = dialogForm();
        Spinner apartment = spinner(apartmentLabelsWithEmpty(), apartmentIdsWithEmpty());
        EditText date = input("2026-05-21", false);
        EditText category = input("ремонт", false);
        EditText amount = input("0", false);
        Spinner source = spinner(new String[]{"Личные", "Арендный бюджет", "Другое"}, new String[]{"personal", "rental_budget", "other"});
        EditText method = input("карта", false);
        EditText description = input("описание", false);
        date.setText(today());
        form.addView(field("Квартира", apartment));
        form.addView(field("Дата", date));
        form.addView(field("Категория", category));
        form.addView(field("Сумма", amount));
        form.addView(field("Источник", source));
        form.addView(field("Способ оплаты", method));
        form.addView(field("Описание", description));
        new AlertDialog.Builder(this)
            .setTitle("Расход")
            .setView(wrapDialog(form))
            .setPositiveButton("Добавить", (dialog, which) -> {
                try {
                    JSONObject body = new JSONObject();
                    String aptId = spinnerValue(apartment);
                    if (!aptId.isEmpty()) body.put("apartment_id", Integer.parseInt(aptId));
                    body.put("expense_date", date.getText().toString());
                    body.put("category", category.getText().toString());
                    body.put("amount", number(amount));
                    body.put("source_funds", spinnerValue(source));
                    body.put("payment_method", method.getText().toString());
                    body.put("description", description.getText().toString());
                    runApi("Добавляю расход", () -> api.postJson("/api/expenses", body), done -> loadCurrentTab(true));
                } catch (Exception ex) {
                    toast(ex.getMessage());
                }
            })
            .setNegativeButton("Отмена", null)
            .show();
    }

    private void payRent(JSONObject charge, String channel) {
        double suggested = "ip".equals(channel) ? Math.max(0, charge.optDouble("ip_due") - charge.optDouble("ip_paid")) : Math.max(0, charge.optDouble("personal_due") - charge.optDouble("personal_paid"));
        promptNumber("Оплата " + channel, suggested, value -> {
            JSONObject body = new JSONObject();
            body.put("amount", value);
            body.put("channel", channel);
            body.put("source", "manual");
            return body;
        }, body -> runApi("Отмечаю оплату", () -> api.postJson("/api/rent-charges/" + charge.optInt("id") + "/payments", (JSONObject) body), done -> loadCurrentTab(true)));
    }

    private void deferRent(JSONObject charge) {
        promptNumber("Отсрочка, дней", 3, value -> {
            JSONObject body = new JSONObject();
            body.put("deferral_days", (int) value);
            body.put("deferral_note", "из Android");
            return body;
        }, body -> runApi("Сохраняю отсрочку", () -> api.postJson("/api/rent-charges/" + charge.optInt("id") + "/defer", (JSONObject) body), done -> loadCurrentTab(true)));
    }

    private void issueBill(JSONObject bill) {
        confirm("Выставить счёт жильцам?", () -> runApi("Выставляю", () -> api.postJson("/api/utility-bills/" + bill.optInt("id") + "/issue", new JSONObject()), done -> loadCurrentTab(true)));
    }

    private void providerPaid(JSONObject bill) {
        runApi("Отмечаю оплату поставщику", () -> api.postJson("/api/utility-bills/" + bill.optInt("id") + "/provider-paid", new JSONObject()), done -> loadCurrentTab(true));
    }

    private void deleteBill(JSONObject bill) {
        confirm("Удалить черновик коммуналки?", () -> runApi("Удаляю", () -> api.deleteJson("/api/utility-bills/" + bill.optInt("id")), done -> loadCurrentTab(true)));
    }

    private void compensateExpense(JSONObject expense) {
        runApi("Компенсирую", () -> api.postJson("/api/expenses/" + expense.optInt("id") + "/compensate", new JSONObject()), done -> loadCurrentTab(true));
    }

    private void toggleLeaseIgnore(JSONObject lease) {
        JSONObject body = new JSONObject();
        try {
            body.put("ignored", !lease.optBoolean("ignored"));
        } catch (Exception ignored) {
        }
        runApi("Сохраняю", () -> api.patchJson("/api/leases/" + lease.optInt("id") + "/ignore", body), done -> loadCurrentTab(true));
    }

    private void moveOut(JSONObject lease) {
        promptText("Дата выезда", today(), value -> {
            try {
                JSONObject body = new JSONObject();
                body.put("end_date", value);
                runApi("Оформляю выезд", () -> api.postJson("/api/leases/" + lease.optInt("id") + "/move-out", body), done -> loadCurrentTab(true));
            } catch (Exception ex) {
                toast(ex.getMessage());
            }
        });
    }

    private void toggleApartment(JSONObject apartment) {
        JSONObject body = new JSONObject();
        try {
            body.put("active", !apartment.optBoolean("active", true));
        } catch (Exception ignored) {
        }
        runApi("Сохраняю квартиру", () -> api.patchJson("/api/apartments/" + apartment.optInt("id"), body), done -> loadCurrentTab(true));
    }

    private void moderateReceipt(JSONObject receipt, String action) {
        JSONObject body = new JSONObject();
        try {
            body.put("action", action);
        } catch (Exception ignored) {
        }
        runApi("Проверяю чек", () -> api.postJson("/api/payment-receipts/" + receipt.optInt("id") + "/moderate", body), done -> loadCurrentTab(true));
    }

    private void ignoreReceipt(JSONObject receipt) {
        runApi("Скрываю чек", () -> api.postJson("/api/payment-receipts/" + receipt.optInt("id") + "/ignore", new JSONObject()), done -> loadCurrentTab(true));
    }

    private void logout() {
        runApi("Выхожу", () -> {
            api.logout();
            return new JSONObject();
        }, done -> showLogin());
    }

    private void showHostDialog() {
        EditText input = input("https://rental.example.ru", false);
        input.setText(api.baseUrl());
        new AlertDialog.Builder(this)
            .setTitle("Адрес сервера")
            .setMessage("Это адрес FastAPI-деплоя. После миграции меняется здесь, а APK остаётся тот же.")
            .setView(input)
            .setPositiveButton("Сохранить", (dialog, which) -> {
                NotificationPrefs.setBaseUrl(this, input.getText().toString());
                screenSubtitle.setText(api.baseUrl());
                checkAuthAndLoad();
            })
            .setNegativeButton("Отмена", null)
            .show();
    }

    private void download(String path, String fallbackName) {
        try {
            String url = api.baseUrl() + path;
            DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url));
            request.setTitle(fallbackName);
            request.setDescription("Rental Manager");
            request.addRequestHeader("Cookie", api.cookieHeader());
            request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, fallbackName);
            DownloadManager manager = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
            if (manager != null) manager.enqueue(request);
            toast("Скачиваю " + fallbackName);
        } catch (Exception ex) {
            toast(ex.getMessage());
        }
    }

    private void runApi(String loading, Job job, Done done) {
        toast(loading + "...");
        new Thread(() -> {
            try {
                Object result = job.run();
                runOnUiThread(() -> done.run(result));
            } catch (ApiClient.ApiException ex) {
                runOnUiThread(() -> {
                    if (ex.statusCode == 401 || ex.statusCode == 403) showLogin();
                    toast(ex.getMessage());
                });
            } catch (Exception ex) {
                runOnUiThread(() -> toast(ex.getMessage() == null ? "Ошибка" : ex.getMessage()));
            }
        }, "rental-api").start();
    }

    private void addHero(String title, String subtitle) {
        LinearLayout hero = card();
        hero.addView(label(title, 25, text, true));
        hero.addView(label(subtitle, 14, muted, false));
        content.addView(hero);
    }

    private void addMetricRow(LinearLayout parent, String a, int av, String b, int bv) {
        LinearLayout row = row();
        row.addView(metric(a, av), new LinearLayout.LayoutParams(0, dp(96), 1));
        row.addView(space(10), new LinearLayout.LayoutParams(dp(10), 1));
        row.addView(metric(b, bv), new LinearLayout.LayoutParams(0, dp(96), 1));
        parent.addView(row);
    }

    private LinearLayout metric(String title, int value) {
        LinearLayout card = card();
        card.setPadding(dp(14), dp(12), dp(14), dp(12));
        card.addView(label(String.valueOf(value), 27, text, true));
        card.addView(label(title, 13, muted, false));
        return card;
    }

    private Button modeButton(String title, String mode) {
        Button button = mode.equals(servicesMode) ? primaryButton(title) : secondaryButton(title);
        button.setOnClickListener(v -> {
            servicesMode = mode;
            renderServices();
        });
        return button;
    }

    private Button primaryButton(String title) {
        return styledButton(title, blue, Color.WHITE);
    }

    private Button primaryButton(String title, View.OnClickListener listener) {
        Button button = primaryButton(title);
        button.setOnClickListener(listener);
        return button;
    }

    private Button secondaryButton(String title) {
        return styledButton(title, surface2, text);
    }

    private Button secondaryButton(String title, View.OnClickListener listener) {
        Button button = secondaryButton(title);
        button.setOnClickListener(listener);
        return button;
    }

    private Button smallButton(String title, View.OnClickListener listener) {
        Button button = secondaryButton(title);
        button.setTextSize(12);
        button.setOnClickListener(listener);
        return button;
    }

    private Button smallButton(String title) {
        Button button = secondaryButton(title);
        button.setTextSize(12);
        return button;
    }

    private Button pillButton(String title, boolean active) {
        return styledButton(title, active ? blue : surface2, text);
    }

    private Button navButton(String title, boolean active) {
        Button button = styledButton(title, active ? Color.rgb(31, 38, 51) : Color.TRANSPARENT, active ? text : muted);
        button.setTextSize(12);
        return button;
    }

    private Button styledButton(String title, int bgColor, int textColor) {
        Button button = new Button(this);
        button.setText(title);
        button.setAllCaps(false);
        button.setTextColor(textColor);
        button.setTextSize(14);
        button.setBackground(round(bgColor, activeAlpha(bgColor), dp(14), bgColor == Color.TRANSPARENT ? Color.TRANSPARENT : hairline));
        return button;
    }

    private int activeAlpha(int color) {
        return color == Color.TRANSPARENT ? 0 : 255;
    }

    private LinearLayout card() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(16), dp(14), dp(16), dp(14));
        card.setBackground(round(surface, 255, dp(18), hairline));
        card.setElevation(dp(3));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.setMargins(0, 0, 0, dp(12));
        card.setLayoutParams(params);
        return card;
    }

    private LinearLayout cardWithAccent(int accent) {
        LinearLayout card = card();
        card.setBackground(round(surface, 255, dp(18), accent));
        return card;
    }

    private TextView section(String title) {
        TextView view = label(title, 20, text, true);
        view.setPadding(dp(4), dp(16), dp(4), dp(8));
        return view;
    }

    private LinearLayout row() {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER);
        row.setPadding(0, 0, 0, dp(8));
        return row;
    }

    private TextView label(String value, int sp, int color, boolean bold) {
        TextView view = new TextView(this);
        view.setText(value == null ? "" : value);
        view.setTextSize(sp);
        view.setTextColor(color);
        view.setPadding(0, dp(2), 0, dp(3));
        if (bold) view.setTypeface(Typeface.DEFAULT_BOLD);
        return view;
    }

    private EditText input(String hint, boolean password) {
        EditText input = new EditText(this);
        input.setHint(hint);
        input.setHintTextColor(muted);
        input.setTextColor(text);
        input.setSingleLine(true);
        input.setTextSize(15);
        input.setPadding(dp(12), 0, dp(12), 0);
        input.setBackground(round(surface2, 255, dp(12), hairline));
        if (password) input.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD);
        return input;
    }

    private CheckBox checkbox(String label) {
        CheckBox box = new CheckBox(this);
        box.setText(label);
        box.setTextColor(text);
        box.setTextSize(14);
        return box;
    }

    private LinearLayout field(String label, View field) {
        LinearLayout wrap = new LinearLayout(this);
        wrap.setOrientation(LinearLayout.VERTICAL);
        wrap.setPadding(0, dp(6), 0, dp(8));
        wrap.addView(label(label, 12, muted, false));
        wrap.addView(field, new LinearLayout.LayoutParams(-1, dp(48)));
        return wrap;
    }

    private LinearLayout dialogForm() {
        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        form.setPadding(dp(8), dp(4), dp(8), dp(4));
        return form;
    }

    private ScrollView wrapDialog(LinearLayout form) {
        ScrollView scroll = new ScrollView(this);
        scroll.addView(form);
        return scroll;
    }

    private Spinner spinner(List<String> labels, List<String> values) {
        Spinner spinner = new Spinner(this);
        ArrayAdapter<String> adapter = new ArrayAdapter<String>(this, android.R.layout.simple_spinner_item, labels);
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        spinner.setAdapter(adapter);
        spinner.setTag(values);
        return spinner;
    }

    private Spinner spinner(String[] labels, String[] values) {
        List<String> labelList = new ArrayList<>();
        List<String> valueList = new ArrayList<>();
        for (String label : labels) labelList.add(label);
        for (String value : values) valueList.add(value);
        return spinner(labelList, valueList);
    }

    private String spinnerValue(Spinner spinner) {
        List<String> values = (List<String>) spinner.getTag();
        int position = spinner.getSelectedItemPosition();
        if (position < 0 || position >= values.size()) return "";
        return values.get(position);
    }

    private void selectSpinnerValue(Spinner spinner, String value) {
        List<String> values = (List<String>) spinner.getTag();
        int index = values.indexOf(value);
        if (index >= 0) spinner.setSelection(index);
    }

    private GradientDrawable round(int color, int alpha, int radius, int strokeColor) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(color);
        drawable.setAlpha(alpha);
        drawable.setCornerRadius(radius);
        if (strokeColor != Color.TRANSPARENT) drawable.setStroke(1, strokeColor);
        return drawable;
    }

    private View space(int dpValue) {
        View view = new View(this);
        view.setMinimumWidth(dp(dpValue));
        view.setMinimumHeight(dp(dpValue));
        return view;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void promptText(String title, String value, TextDone done) {
        EditText input = input(title, false);
        input.setText(value);
        new AlertDialog.Builder(this)
            .setTitle(title)
            .setView(input)
            .setPositiveButton("OK", (dialog, which) -> done.run(input.getText().toString()))
            .setNegativeButton("Отмена", null)
            .show();
    }

    private interface TextDone {
        void run(String value);
    }

    private interface JsonFactory {
        JSONObject make(double value) throws Exception;
    }

    private void promptNumber(String title, double suggested, JsonFactory factory, Done done) {
        EditText input = input(title, false);
        input.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_FLAG_DECIMAL);
        input.setText(String.valueOf(suggested));
        new AlertDialog.Builder(this)
            .setTitle(title)
            .setView(input)
            .setPositiveButton("OK", (dialog, which) -> {
                try {
                    done.run(factory.make(Double.parseDouble(input.getText().toString().replace(",", "."))));
                } catch (Exception ex) {
                    toast(ex.getMessage());
                }
            })
            .setNegativeButton("Отмена", null)
            .show();
    }

    private void confirm(String message, Runnable ok) {
        new AlertDialog.Builder(this)
            .setTitle("Подтверждение")
            .setMessage(message)
            .setPositiveButton("Да", (dialog, which) -> ok.run())
            .setNegativeButton("Отмена", null)
            .show();
    }

    private List<String> apartmentLabels() {
        List<String> labels = new ArrayList<>();
        forEach(arr(bootstrap, "objects"), object -> forEach(arr(object, "apartments"), apartment -> labels.add(object.optString("name") + " · " + apartment.optString("name"))));
        return labels;
    }

    private List<String> apartmentIds() {
        List<String> ids = new ArrayList<>();
        forEach(arr(bootstrap, "objects"), object -> forEach(arr(object, "apartments"), apartment -> ids.add(String.valueOf(apartment.optInt("id")))));
        return ids;
    }

    private List<String> apartmentLabelsWithEmpty() {
        List<String> labels = new ArrayList<>();
        labels.add("Не выбрано");
        labels.addAll(apartmentLabels());
        return labels;
    }

    private List<String> apartmentIdsWithEmpty() {
        List<String> ids = new ArrayList<>();
        ids.add("");
        ids.addAll(apartmentIds());
        return ids;
    }

    private List<String> leaseLabels() {
        List<String> labels = new ArrayList<>();
        forEach(arr(bootstrap, "leases"), lease -> labels.add(lease.optString("object") + " · " + lease.optString("apartment") + " · " + lease.optString("tenant")));
        return labels;
    }

    private List<String> leaseIds() {
        List<String> ids = new ArrayList<>();
        forEach(arr(bootstrap, "leases"), lease -> ids.add(String.valueOf(lease.optInt("id"))));
        return ids;
    }

    private List<String> serviceLabels() {
        List<String> labels = new ArrayList<>();
        forEach(arr(bootstrap, "services"), service -> labels.add(service.optString("object") + " · " + service.optString("name")));
        return labels;
    }

    private List<String> serviceIds() {
        List<String> ids = new ArrayList<>();
        forEach(arr(bootstrap, "services"), service -> ids.add(String.valueOf(service.optInt("id"))));
        return ids;
    }

    private List<String> meterLabels() {
        List<String> labels = new ArrayList<>();
        forEach(arr(bootstrap, "meters"), meter -> labels.add(meter.optString("object") + " · " + meter.optString("name")));
        return labels;
    }

    private List<String> meterIds() {
        List<String> ids = new ArrayList<>();
        forEach(arr(bootstrap, "meters"), meter -> ids.add(String.valueOf(meter.optInt("id"))));
        return ids;
    }

    private JSONObject obj(JSONObject source, String key) {
        JSONObject value = source == null ? null : source.optJSONObject(key);
        return value == null ? new JSONObject() : value;
    }

    private JSONArray arr(JSONObject source, String key) {
        JSONArray value = source == null ? null : source.optJSONArray(key);
        return value == null ? new JSONArray() : value;
    }

    private int len(JSONObject source, String key) {
        return arr(source, key).length();
    }

    private void forEach(JSONArray array, JsonItem item) {
        for (int i = 0; i < array.length(); i++) {
            JSONObject object = array.optJSONObject(i);
            if (object != null) item.run(object);
        }
    }

    private interface JsonItem {
        void run(JSONObject item);
    }

    private String joinNonEmpty(String... values) {
        StringBuilder builder = new StringBuilder();
        for (String value : values) {
            if (value == null || value.trim().isEmpty() || "null".equals(value)) continue;
            if (builder.length() > 0) builder.append(" · ");
            builder.append(value.trim());
        }
        return builder.toString();
    }

    private String money(double value) {
        return String.format(Locale.forLanguageTag("ru-RU"), "%,.0f ₽", value);
    }

    private double number(EditText input) {
        String value = input.getText().toString().trim().replace(",", ".");
        if (value.isEmpty()) return 0;
        return Double.parseDouble(value);
    }

    private Object emptyToNull(EditText input) {
        String value = input.getText().toString().trim();
        if (value.isEmpty()) return JSONObject.NULL;
        return value;
    }

    private int statusColor(String status) {
        if ("overdue".equals(status) || "suspicious".equals(status)) return red;
        if ("partial".equals(status) || "deferred".equals(status) || "issued".equals(status)) return orange;
        if ("paid".equals(status) || "paid_ahead".equals(status) || "accepted".equals(status)) return green;
        return blue;
    }

    private String today() {
        java.text.SimpleDateFormat format = new java.text.SimpleDateFormat("yyyy-MM-dd", Locale.US);
        return format.format(new java.util.Date());
    }

    private String firstDay() {
        java.text.SimpleDateFormat format = new java.text.SimpleDateFormat("yyyy-MM-01", Locale.US);
        return format.format(new java.util.Date());
    }

    private void toast(String message) {
        Toast.makeText(this, message == null ? "" : message, Toast.LENGTH_SHORT).show();
    }

    private void requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQUEST_NOTIFICATIONS);
        }
    }
}
