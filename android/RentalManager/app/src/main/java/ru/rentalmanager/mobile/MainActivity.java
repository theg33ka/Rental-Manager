package ru.rentalmanager.mobile;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.MotionEvent;
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
import java.util.Calendar;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

public class MainActivity extends Activity {
    private static final int REQUEST_NOTIFICATIONS = 7102;
    private static final long CACHE_TTL_MS = 60_000L;
    private static final long CONNECTION_CHECK_MIN_MS = 2_000L;
    private static final long RECONNECT_INTERVAL_MS = 10_000L;

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
    private final int gray = Color.rgb(105, 112, 124);

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
    private long bootstrapLoadedAt = 0L;
    private long paymentsLoadedAt = 0L;
    private long servicesLoadedAt = 0L;
    private long moreLoadedAt = 0L;
    private long lastConnectionCheckAt = 0L;
    private boolean prefetchRunning = false;
    private boolean reconnectLoopRunning = false;
    private boolean lastConnectionOk = true;
    private boolean activityResumed = false;
    private Handler reconnectHandler;
    private Calendar selectedMonth = Calendar.getInstance();
    private float monthTouchStartX = 0f;

    private final Runnable reconnectRunnable = new Runnable() {
        @Override
        public void run() {
            if (!reconnectLoopRunning || reconnectHandler == null) return;
            checkServerConnection(true);
            reconnectHandler.postDelayed(this, RECONNECT_INTERVAL_MS);
        }
    };

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
        reconnectHandler = new Handler(Looper.getMainLooper());
        NotificationHelper.ensureChannels(this);
        requestNotificationPermission();
        buildShell();
        ReminderScheduler.schedule(this);
        if (!NotificationPrefs.hasCustomBaseUrl(this)) {
            showHostDialog();
        } else {
            checkServerConnection(true);
            checkAuthAndLoad();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        activityResumed = true;
        if (screenSubtitle != null) screenSubtitle.setText(api.baseUrl());
        checkServerConnection(false);
        if (!lastConnectionOk) startReconnectLoop();
    }

    @Override
    protected void onPause() {
        activityResumed = false;
        stopReconnectLoop();
        super.onPause();
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

        Button refresh = pillButton("Обновить", false);
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

    private void checkServerConnection(boolean force) {
        if (!NotificationPrefs.hasCustomBaseUrl(this)) return;
        long now = System.currentTimeMillis();
        if (!force && now - lastConnectionCheckAt < CONNECTION_CHECK_MIN_MS) return;
        lastConnectionCheckAt = now;
        new Thread(() -> {
            try {
                api.getJson("/healthz");
                runOnUiThread(() -> setConnectionStatus(true, "связь есть"));
            } catch (Exception ex) {
                runOnUiThread(() -> setConnectionStatus(false, "связи нет"));
            }
        }, "rental-health").start();
    }

    private void setConnectionStatus(boolean ok, String status) {
        if (screenSubtitle == null) return;
        lastConnectionOk = ok;
        screenSubtitle.setText((ok ? "● " : "● ") + status + " · " + api.baseUrl());
        screenSubtitle.setTextColor(ok ? green : red);
        if (ok) {
            stopReconnectLoop();
        } else if (activityResumed) {
            startReconnectLoop();
        }
    }

    private void startReconnectLoop() {
        if (reconnectLoopRunning || reconnectHandler == null || !NotificationPrefs.hasCustomBaseUrl(this)) return;
        reconnectLoopRunning = true;
        reconnectHandler.postDelayed(reconnectRunnable, RECONNECT_INTERVAL_MS);
    }

    private void stopReconnectLoop() {
        reconnectLoopRunning = false;
        if (reconnectHandler != null) reconnectHandler.removeCallbacks(reconnectRunnable);
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
        if (!force && currentTabReady()) {
            renderCurrentTab();
            if (currentTabStale()) {
                refreshCurrentTab(false);
            }
            return;
        }
        refreshCurrentTab(true);
    }

    private boolean currentTabReady() {
        if ("dashboard".equals(currentTab) || "tenants".equals(currentTab)) return bootstrapLoadedAt > 0;
        if ("payments".equals(currentTab)) return bootstrapLoadedAt > 0 && paymentsLoadedAt > 0;
        if ("services".equals(currentTab)) return bootstrapLoadedAt > 0 && servicesLoadedAt > 0;
        return bootstrapLoadedAt > 0 && moreLoadedAt > 0;
    }

    private boolean currentTabStale() {
        long now = System.currentTimeMillis();
        if (bootstrapLoadedAt <= 0 || now - bootstrapLoadedAt > CACHE_TTL_MS) return true;
        if ("payments".equals(currentTab)) return paymentsLoadedAt <= 0 || now - paymentsLoadedAt > CACHE_TTL_MS;
        if ("services".equals(currentTab)) return servicesLoadedAt <= 0 || now - servicesLoadedAt > CACHE_TTL_MS;
        if ("more".equals(currentTab)) return moreLoadedAt <= 0 || now - moreLoadedAt > CACHE_TTL_MS;
        return false;
    }

    private void renderCurrentTab() {
        if ("dashboard".equals(currentTab)) renderDashboard();
        else if ("tenants".equals(currentTab)) renderTenants();
        else if ("payments".equals(currentTab)) renderPayments();
        else if ("services".equals(currentTab)) renderServices();
        else renderMore();
    }

    private void refreshCurrentTab(boolean visible) {
        if (visible && !currentTabReady()) showLoadingCard();
        if ("dashboard".equals(currentTab)) {
            runApi("Обновляю дашборд", visible, () -> {
                loadBootstrap();
                return bootstrap;
            }, value -> {
                renderDashboard();
                prefetchSecondaryData();
            });
        } else if ("tenants".equals(currentTab)) {
            runApi("Обновляю жильцов", visible, () -> {
                loadBootstrap();
                return bootstrap;
            }, value -> renderTenants());
        } else if ("payments".equals(currentTab)) {
            runApi("Обновляю оплаты", visible, () -> {
                loadBootstrap();
                loadPayments();
                return null;
            }, value -> renderPayments());
        } else if ("services".equals(currentTab)) {
            runApi("Обновляю учёт", visible, () -> {
                loadBootstrap();
                loadServices();
                return null;
            }, value -> renderServices());
        } else {
            runApi("Обновляю настройки", visible, () -> {
                loadBootstrap();
                loadMoreData();
                return null;
            }, value -> renderMore());
        }
    }

    private void loadBootstrap() throws Exception {
        bootstrap = api.getJson("/api/bootstrap");
        bootstrapLoadedAt = System.currentTimeMillis();
    }

    private void loadPayments() throws Exception {
        rentCharges = api.getArray("/api/rent-charges");
        suspiciousReceipts = api.getArray("/api/payment-receipts/suspicious");
        paymentsLoadedAt = System.currentTimeMillis();
    }

    private void loadServices() throws Exception {
        utilityBills = api.getArray("/api/utility-bills");
        utilityTimeline = api.getArray("/api/utilities/timeline");
        expenses = api.getArray("/api/expenses");
        tariffs = api.getArray("/api/tariffs");
        servicesLoadedAt = System.currentTimeMillis();
    }

    private void loadMoreData() throws Exception {
        messageTargets = api.getArray("/api/messages/targets");
        moreLoadedAt = System.currentTimeMillis();
    }

    private void prefetchSecondaryData() {
        if (prefetchRunning) return;
        prefetchRunning = true;
        new Thread(() -> {
            try {
                if (paymentsLoadedAt <= 0 || System.currentTimeMillis() - paymentsLoadedAt > CACHE_TTL_MS) loadPayments();
                if (servicesLoadedAt <= 0 || System.currentTimeMillis() - servicesLoadedAt > CACHE_TTL_MS) loadServices();
                if (moreLoadedAt <= 0 || System.currentTimeMillis() - moreLoadedAt > CACHE_TTL_MS) loadMoreData();
            } catch (Exception ignored) {
                // Фоновая предзагрузка не должна ломать открытый экран. Ей и так стыдно.
            } finally {
                prefetchRunning = false;
            }
        }, "rental-prefetch").start();
    }

    private void invalidateAllCaches() {
        bootstrapLoadedAt = 0L;
        paymentsLoadedAt = 0L;
        servicesLoadedAt = 0L;
        moreLoadedAt = 0L;
    }

    private void showLoadingCard() {
        content.removeAllViews();
        LinearLayout card = card();
        card.addView(label("Загружаю", 22, text, true));
        card.addView(label("Один короткий запрос к серверу. Если долго - связь опять решила подумать.", 14, muted, false));
        content.addView(card);
    }

    private void renderDashboard() {
        screenTitle.setText("Пульт");
        content.removeAllViews();
        JSONObject dashboard = obj(bootstrap, "dashboard");
        if (showSection("dashboard_hero")) {
            addHero("Дела по аренде", "Сводка по платежам, отчётам, счётчикам и коммунальным задачам.");
        }

        if (showSection("dashboard_progress")) {
            addMonthProgressCard(dashboard);
        }

        if (showSection("dashboard_metrics")) {
            LinearLayout grid = new LinearLayout(this);
            grid.setOrientation(LinearLayout.VERTICAL);
            content.addView(grid);
            addMetricRow(grid, "Просрочка аренды", len(dashboard, "rent_overdue"), "Коммуналка", len(dashboard, "utility_overdue"));
            addMetricRow(grid, "Сегодня оплата", len(dashboard, "rent_today"), "Отчёты", len(dashboard, "monthly_reports"));
            addMetricRow(grid, "Чеки проверить", len(dashboard, "suspicious_receipts"), "Счётчики", len(dashboard, "stale_readings"));
        }

        JSONArray reports = arr(dashboard, "monthly_reports");
        if (showSection("dashboard_reports") && reports.length() > 0) {
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

        if (showSection("dashboard_attention")) {
            content.addView(section("Требует внимания"));
            addGroupedAttentionCards(dashboard);
        }
        if (content.getChildCount() < 5) {
            LinearLayout ok = card();
            ok.addView(label("Критичных задач нет", 19, text, true));
            ok.addView(label("Все обязательные действия на текущий момент закрыты.", 14, muted, false));
            content.addView(ok);
        }
    }

    private void addMonthProgressCard(JSONObject dashboard) {
        MonthScore score = buildMonthScore(dashboard);
        LinearLayout card = card();
        card.setPadding(dp(14), dp(12), dp(14), dp(12));

        TextView month = label(monthTitle(selectedMonth), 21, text, true);
        month.setGravity(Gravity.CENTER);
        card.addView(month);
        TextView hint = label("Свайп влево или вправо меняет месяц", 12, muted, false);
        hint.setGravity(Gravity.CENTER);
        card.addView(hint);

        MonthProgressView chart = new MonthProgressView(this);
        chart.setScore(score);
        card.addView(chart, new LinearLayout.LayoutParams(-1, dp(138)));

        LinearLayout legend = row();
        legend.addView(legendItem("Выполнено", green), new LinearLayout.LayoutParams(0, -2, 1));
        legend.addView(legendItem("В работе", orange), new LinearLayout.LayoutParams(0, -2, 1));
        legend.addView(legendItem("Долги", red), new LinearLayout.LayoutParams(0, -2, 1));
        legend.addView(legendItem("Будущие", gray), new LinearLayout.LayoutParams(0, -2, 1));
        card.addView(legend);

        card.setOnTouchListener((view, event) -> {
            if (event.getAction() == MotionEvent.ACTION_DOWN) {
                monthTouchStartX = event.getX();
                return true;
            }
            if (event.getAction() == MotionEvent.ACTION_UP) {
                float dx = event.getX() - monthTouchStartX;
                if (Math.abs(dx) > dp(48)) {
                    shiftSelectedMonth(dx < 0 ? 1 : -1);
                    renderDashboard();
                }
                return true;
            }
            return true;
        });
        content.addView(card);
    }

    private TextView legendItem(String title, int color) {
        TextView view = label("● " + title, 11, color, true);
        view.setGravity(Gravity.CENTER);
        return view;
    }

    private void shiftSelectedMonth(int delta) {
        selectedMonth.add(Calendar.MONTH, delta);
    }

    private MonthScore buildMonthScore(JSONObject dashboard) {
        MonthScore score = new MonthScore();
        boolean detailedData = rentCharges.length() > 0 || utilityBills.length() > 0;

        forEach(rentCharges, charge -> {
            if (!sameSelectedMonth(firstNonEmpty(charge.optString("period_start"), charge.optString("due_date")))) return;
            addTaskByStatus(score, charge.optString("status"), charge.optString("due_date"), true);
        });

        forEach(utilityBills, bill -> {
            if (!sameSelectedMonth(firstNonEmpty(bill.optString("period_end"), bill.optString("due_date")))) return;
            addTaskByStatus(score, bill.optBoolean("provider_paid") ? "paid" : "issued", bill.optString("due_date"), false);
            JSONArray lines = arr(bill, "lines");
            forEach(lines, line -> addTaskByStatus(score, line.optString("status"), line.optString("due_date"), true));
        });

        forEach(arr(dashboard, "monthly_reports"), report -> {
            if (report.optInt("year") == selectedMonth.get(Calendar.YEAR)
                && report.optInt("month") == selectedMonth.get(Calendar.MONTH) + 1) {
                addTaskByStatus(score, report.optString("severity", "open"), null, false);
            }
        });

        if (sameSelectedMonth(today())) {
            forEach(arr(dashboard, "stale_readings"), item -> score.warning++);
            forEach(arr(dashboard, "provider_debts"), item -> addTaskByStatus(score, "issued", item.optString("due_date"), false));
        }

        if (!detailedData) {
            collectScoreFromDashboard(score, dashboard, "rent_overdue", true);
            collectScoreFromDashboard(score, dashboard, "rent_partial", true);
            collectScoreFromDashboard(score, dashboard, "rent_today", false);
            collectScoreFromDashboard(score, dashboard, "rent_deferred", false);
            collectScoreFromDashboard(score, dashboard, "utility_overdue", true);
            collectScoreFromDashboard(score, dashboard, "utility_partial", true);
            collectScoreFromDashboard(score, dashboard, "utility_issued", false);
            collectScoreFromDashboard(score, dashboard, "manual_debts", true);
            collectScoreFromDashboard(score, dashboard, "suspicious_receipts", true);
        }

        return score;
    }

    private void collectScoreFromDashboard(MonthScore score, JSONObject dashboard, String key, boolean critical) {
        forEach(arr(dashboard, key), item -> {
            String date = firstNonEmpty(item.optString("period_start"), item.optString("bill_period_end"), item.optString("period_end"), item.optString("due_date"));
            if (!sameSelectedMonth(date)) return;
            addTaskByStatus(score, critical ? "overdue" : item.optString("status", "issued"), item.optString("due_date"), critical);
        });
    }

    private void addTaskByStatus(MonthScore score, String status, String dueDate, boolean debtCanBeCritical) {
        if (isFutureDate(dueDate)) {
            score.upcoming++;
        } else if (isDoneStatus(status)) {
            score.done++;
        } else if (debtCanBeCritical && isCriticalStatus(status)) {
            score.critical++;
        } else {
            score.warning++;
        }
    }

    private void addGroupedAttentionCards(JSONObject dashboard) {
        Map<String, IssueGroup> groups = new LinkedHashMap<>();
        collectAttention(groups, dashboard, "rent_overdue", "Просрочена аренда", red, "payments");
        collectAttention(groups, dashboard, "rent_partial", "Частичная аренда", orange, "payments");
        collectAttention(groups, dashboard, "rent_today", "Срок оплаты сегодня", orange, "payments");
        collectAttention(groups, dashboard, "rent_deferred", "Отсрочка", orange, "payments");
        collectAttention(groups, dashboard, "utility_overdue", "Долг по коммуналке", red, "services");
        collectAttention(groups, dashboard, "utility_partial", "Частичная коммуналка", orange, "services");
        collectAttention(groups, dashboard, "utility_issued", "Коммуналка к оплате", orange, "services");
        collectAttention(groups, dashboard, "manual_debts", "Ручной долг", orange, "payments");
        collectAttention(groups, dashboard, "stale_readings", "Нет показаний", orange, "services");
        collectAttention(groups, dashboard, "provider_debts", "Поставщик не оплачен", orange, "services");
        collectAttention(groups, dashboard, "suspicious_receipts", "Чек на проверку", red, "payments");

        for (IssueGroup group : groups.values()) {
            LinearLayout card = cardWithAccent(group.color);
            card.setPadding(dp(14), dp(10), dp(14), dp(10));
            card.addView(label(group.title, 17, text, true));
            if (!group.tenant.isEmpty()) {
                card.addView(label(group.tenant, 12, muted, false));
            }
            for (String line : group.lines) {
                card.addView(label(line, 13, muted, false));
            }
            Button open = secondaryButton("Открыть");
            open.setOnClickListener(v -> {
                currentTab = group.targetTab;
                buildBottomNav();
                loadCurrentTab(false);
            });
            card.addView(open, new LinearLayout.LayoutParams(-1, dp(42)));
            content.addView(card);
        }
    }

    private void collectAttention(Map<String, IssueGroup> groups, JSONObject dashboard, String key, String title, int color, String targetTab) {
        forEach(arr(dashboard, key), item -> {
            int effectiveColor = isFutureDate(item.optString("due_date")) ? gray : color;
            String groupKey = attentionGroupKey(item, key);
            IssueGroup group = groups.get(groupKey);
            if (group == null) {
                group = new IssueGroup();
                group.title = attentionGroupTitle(item, title);
                group.tenant = item.optString("tenant", "");
                group.targetTab = targetTab;
                group.color = effectiveColor;
                groups.put(groupKey, group);
            }
            if (severityRank(effectiveColor) > severityRank(group.color)) group.color = effectiveColor;
            if ("payments".equals(targetTab)) group.targetTab = "payments";
            String issueKey = key + ":" + item.optInt("id", item.hashCode());
            if (group.seen.add(issueKey)) {
                group.lines.add(attentionLine(key, title, item));
            }
        });
    }

    private String attentionGroupKey(JSONObject item, String fallback) {
        String object = item.optString("object", "").trim();
        String apartment = item.optString("apartment", "").trim();
        if (!apartment.isEmpty()) return object + "|" + apartment;
        String service = item.optString("service", "").trim();
        if (!service.isEmpty()) return object + "|" + service;
        return fallback + "|" + item.optInt("id", item.hashCode());
    }

    private String attentionGroupTitle(JSONObject item, String fallback) {
        String where = joinNonEmpty(item.optString("object"), item.optString("apartment"));
        if (!where.isEmpty()) return where;
        String service = joinNonEmpty(item.optString("object"), item.optString("service"));
        return service.isEmpty() ? fallback : service;
    }

    private String attentionLine(String key, String title, JSONObject item) {
        String period = periodMonth(item);
        String amount = item.has("debt") ? money(item.optDouble("debt")) : item.has("total_cost") ? money(item.optDouble("total_cost")) : "";
        String due = item.optString("due_date", "");
        if (key.startsWith("rent")) {
            return joinNonEmpty(title + (period.isEmpty() ? "" : " за " + period), amount.isEmpty() ? "" : "долг " + amount, due.isEmpty() ? "" : "срок " + due);
        }
        if (key.startsWith("utility")) {
            return joinNonEmpty(title + (item.optString("service").isEmpty() ? "" : " · " + item.optString("service")) + (period.isEmpty() ? "" : " за " + period), amount.isEmpty() ? "" : "долг " + amount, due.isEmpty() ? "" : "срок " + due);
        }
        if ("manual_debts".equals(key)) {
            return joinNonEmpty(item.optString("title", title) + (period.isEmpty() ? "" : " за " + period), amount.isEmpty() ? "" : "долг " + amount, due.isEmpty() ? "" : "срок " + due);
        }
        if ("stale_readings".equals(key)) {
            String last = item.optString("last_date", "");
            String days = item.has("days") && !item.isNull("days") ? item.optInt("days") + " дн." : "";
            return joinNonEmpty(title + " · " + item.optString("service"), last.isEmpty() ? "нет последнего показания" : "последнее " + last, days);
        }
        if ("provider_debts".equals(key)) {
            return joinNonEmpty(title + " · " + item.optString("service") + (period.isEmpty() ? "" : " за " + period), item.has("total_cost") ? money(item.optDouble("total_cost")) : "", due.isEmpty() ? "" : "срок " + due);
        }
        return joinNonEmpty(title, item.optString("tenant"), amount);
    }

    private void renderTenants() {
        screenTitle.setText("Жильцы");
        content.removeAllViews();
        if (showSection("tenants_hero")) {
            addHero("Заселение и квартиры", "Карточки вместо таблиц. Да, таблицы тоже умеем, но пальцем по ним грустно.");
        }
        if (showSection("tenants_onboard")) {
            Button add = primaryButton("Заселить жильца");
            add.setOnClickListener(v -> showOnboardDialog(null));
            content.addView(add, new LinearLayout.LayoutParams(-1, dp(52)));
        }

        if (showSection("tenants_active_leases")) {
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
        }

        if (showSection("tenants_apartments")) {
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
        if (showSection("payments_hero")) {
            addHero("Аренда и платежи", "Долги, отсрочки, ручные оплаты и чеки. Деньги любят порядок, кто бы спорил.");
        }
        if (showSection("payments_actions")) {
            LinearLayout actions = row();
            actions.addView(smallButton("Сгенерировать", v -> runApi("Генерирую", () -> api.postJson("/api/rent-charges/generate", new JSONObject()), value -> loadCurrentTab(true))), new LinearLayout.LayoutParams(0, dp(44), 1));
            actions.addView(smallButton("Ручная оплата", v -> showManualPaymentDialog()), new LinearLayout.LayoutParams(0, dp(44), 1));
            content.addView(actions);
        }

        if (showSection("payments_rent")) {
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
        }

        if (showSection("payments_suspicious") && suspiciousReceipts.length() > 0) {
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
        if (showSection("services_hero")) {
            addHero("Коммуналка, счётчики, расходы", "Всё, что обычно расползается по вкладкам, собрано в один рабочий экран.");
        }
        if (showSection("services_modes")) {
            LinearLayout tabs = row();
            tabs.addView(modeButton("Коммуналка", "utilities"), new LinearLayout.LayoutParams(0, dp(44), 1));
            tabs.addView(modeButton("Счётчики", "meters"), new LinearLayout.LayoutParams(0, dp(44), 1));
            tabs.addView(modeButton("Тарифы", "tariffs"), new LinearLayout.LayoutParams(0, dp(44), 1));
            tabs.addView(modeButton("Расходы", "expenses"), new LinearLayout.LayoutParams(0, dp(44), 1));
            content.addView(tabs);
        }
        if ("meters".equals(servicesMode)) renderMeters();
        else if ("tariffs".equals(servicesMode)) renderTariffs();
        else if ("expenses".equals(servicesMode)) renderExpenses();
        else renderUtilities();
    }

    private void renderUtilities() {
        if (showSection("services_utility_calculate")) {
            Button calculate = primaryButton("Рассчитать коммуналку");
            calculate.setOnClickListener(v -> showUtilityCalcDialog());
            content.addView(calculate, new LinearLayout.LayoutParams(-1, dp(52)));
        }
        if (!showSection("services_utility_bills")) return;
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
        if (showSection("services_meter_add")) {
            Button add = primaryButton("Добавить показание");
            add.setOnClickListener(v -> showReadingDialog());
            content.addView(add, new LinearLayout.LayoutParams(-1, dp(52)));
        }
        if (!showSection("services_meter_list")) return;
        content.addView(section("Счётчики"));
        forEach(arr(bootstrap, "meters"), meter -> {
            LinearLayout card = card();
            card.addView(label(meter.optString("object") + " · " + meter.optString("name"), 17, text, true));
            card.addView(label(meter.optString("scope") + " · последнее " + meter.optString("last_reading_date", "нет"), 13, muted, false));
            content.addView(card);
        });
    }

    private void renderTariffs() {
        if (showSection("services_tariff_add")) {
            Button add = primaryButton("Добавить тариф");
            add.setOnClickListener(v -> showTariffDialog());
            content.addView(add, new LinearLayout.LayoutParams(-1, dp(52)));
        }
        if (!showSection("services_tariff_list")) return;
        content.addView(section("Тарифы"));
        forEach(tariffs, tariff -> {
            LinearLayout card = card();
            card.addView(label(tariff.optString("service") + " · " + tariff.optString("name"), 17, text, true));
            card.addView(label("с " + tariff.optString("starts_on") + " · " + tariff.optString("tiers"), 13, muted, false));
            content.addView(card);
        });
    }

    private void renderExpenses() {
        if (showSection("services_expense_add")) {
            Button add = primaryButton("Добавить расход");
            add.setOnClickListener(v -> showExpenseDialog());
            content.addView(add, new LinearLayout.LayoutParams(-1, dp(52)));
        }
        if (!showSection("services_expense_list")) return;
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
        if (showSection("more_hero")) {
            addHero("Отчёты, сообщения, настройки", "Редкие, но важные действия. Прячем не потому что стыдно, а потому что каждый день они не нужны.");
        }

        LinearLayout ui = card();
        ui.addView(label("Видимость экранов", 19, text, true));
        ui.addView(label("Выбери, какие блоки показывать на каждой странице. Спрячем лишнее, пусть не шумит.", 14, muted, false));
        ui.addView(primaryButton("Настроить страницы", v -> showPageSectionsDialog()));
        content.addView(ui);

        if (showSection("more_notifications")) {
        LinearLayout notif = card();
        notif.addView(label("Пуш-уведомления", 19, text, true));
        notif.addView(label("Частота, тихие часы, типы событий и постоянный debt-alert.", 14, muted, false));
        notif.addView(primaryButton("Настроить", v -> startActivity(new Intent(this, NotificationSettingsActivity.class))));
        content.addView(notif);
        }

        if (showSection("more_reports")) {
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
        }

        if (showSection("more_messages")) {
        LinearLayout messages = card();
        messages.addView(label("Сообщения жильцам", 19, text, true));
        messages.addView(label("Превью и отправка через Telegram-бота.", 14, muted, false));
        messages.addView(primaryButton("Открыть получателей", v -> showMessageTargets()));
        messages.addView(secondaryButton("Прогнать напоминания", v -> runApi("Напоминания", () -> api.postJson("/api/reminders/run", new JSONObject()), value -> toast("Напоминания обработаны"))));
        content.addView(messages);
        }

        if (showSection("more_server_settings")) {
        LinearLayout settings = card();
        settings.addView(label("Настройки сервера", 19, text, true));
        settings.addView(secondaryButton("Сменить хост", v -> showHostDialog()));
        settings.addView(secondaryButton("Открыть", v -> showServerSettingsDialog()));
        settings.addView(secondaryButton("Экспорт базы", v -> download("/api/admin/database-export", "rental-manager-db.json")));
        settings.addView(secondaryButton("Выйти из PIN-сессии", v -> logout()));
        content.addView(settings);
        }
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

    private boolean showSection(String key) {
        return NotificationPrefs.prefs(this).getBoolean("ui_" + key, true);
    }

    private void showPageSectionsDialog() {
        LinearLayout form = dialogForm();
        List<CheckBox> boxes = new ArrayList<>();
        addSectionGroup(form, boxes, "Дом", new String[][]{
            {"dashboard_hero", "Верхняя карточка"},
            {"dashboard_metrics", "Счётчики состояния"},
            {"dashboard_reports", "Месячные отчёты"},
            {"dashboard_attention", "Требует внимания"}
        });
        addSectionGroup(form, boxes, "Жильцы", new String[][]{
            {"tenants_hero", "Верхняя карточка"},
            {"tenants_onboard", "Кнопка заселения"},
            {"tenants_active_leases", "Активные договоры"},
            {"tenants_apartments", "Квартиры"}
        });
        addSectionGroup(form, boxes, "Оплаты", new String[][]{
            {"payments_hero", "Верхняя карточка"},
            {"payments_actions", "Быстрые действия"},
            {"payments_rent", "Арендные начисления"},
            {"payments_suspicious", "Чеки на проверку"}
        });
        addSectionGroup(form, boxes, "Учёт", new String[][]{
            {"services_hero", "Верхняя карточка"},
            {"services_modes", "Переключатель подразделов"},
            {"services_utility_calculate", "Кнопка расчёта коммуналки"},
            {"services_utility_bills", "Счета коммуналки"},
            {"services_meter_add", "Кнопка показания"},
            {"services_meter_list", "Список счётчиков"},
            {"services_tariff_add", "Кнопка тарифа"},
            {"services_tariff_list", "Список тарифов"},
            {"services_expense_add", "Кнопка расхода"},
            {"services_expense_list", "Список расходов"}
        });
        addSectionGroup(form, boxes, "Ещё", new String[][]{
            {"more_hero", "Верхняя карточка"},
            {"more_notifications", "Пуш-уведомления"},
            {"more_reports", "Excel-отчёты"},
            {"more_messages", "Сообщения"},
            {"more_server_settings", "Настройки сервера"}
        });
        new AlertDialog.Builder(this)
            .setTitle("Что показывать")
            .setView(wrapDialog(form))
            .setPositiveButton("Сохранить", (dialog, which) -> {
                SharedPreferences.Editor editor = NotificationPrefs.prefs(this).edit();
                for (CheckBox box : boxes) {
                    editor.putBoolean("ui_" + box.getTag().toString(), box.isChecked());
                }
                editor.apply();
                renderCurrentTab();
            })
            .setNegativeButton("Отмена", null)
            .show();
    }

    private void addSectionGroup(LinearLayout form, List<CheckBox> boxes, String title, String[][] items) {
        form.addView(section(title));
        for (String[] item : items) {
            CheckBox box = checkbox(item[1]);
            box.setTag(item[0]);
            box.setChecked(showSection(item[0]));
            boxes.add(box);
            form.addView(box);
        }
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
                invalidateAllCaches();
                screenSubtitle.setText(api.baseUrl());
                checkServerConnection(true);
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
        runApi(loading, true, job, done);
    }

    private void runApi(String loading, boolean notify, Job job, Done done) {
        if (notify) toast(loading + "...");
        new Thread(() -> {
            try {
                Object result = job.run();
                runOnUiThread(() -> {
                    setConnectionStatus(true, "связь есть");
                    done.run(result);
                });
            } catch (ApiClient.ApiException ex) {
                runOnUiThread(() -> {
                    if (ex.statusCode == 401 || ex.statusCode == 403) showLogin();
                    else setConnectionStatus(false, "ошибка " + ex.statusCode);
                    toast(ex.getMessage());
                });
            } catch (Exception ex) {
                runOnUiThread(() -> {
                    setConnectionStatus(false, "связи нет");
                    toast(ex.getMessage() == null ? "Ошибка" : ex.getMessage());
                });
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
