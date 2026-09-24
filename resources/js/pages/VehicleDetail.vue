<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { Head, Link } from "@inertiajs/vue3";
import {
    AreaSeries,
    BarSeries,
    CandlestickSeries,
    ColorType,
    createChart,
    HistogramSeries,
    LineSeries,
    type IChartApi,
    type IPriceLine,
    type ISeriesApi,
    type UTCTimestamp,
} from "lightweight-charts";

type ChartType = "candlestick" | "line" | "area" | "bar";
type BudgetStatus = "unset" | "healthy" | "tight" | "close" | "over";

interface Candle {
    time: number;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
    bid_count: number;
}

interface VehicleMeta {
    wp_product_id: number;
    name: string;
    category: string;
    base_price: number;
    current_price: number;
    bid_count: number;
    increment: number;
    started_at: number;
    close_at: number;
    seconds_left: number;
    status: string;
    watched: boolean;
    watch_price: number | null;
    budget_alerted_at: string | null;
}

const props = defineProps<{ wpId: number }>();

const container = ref<HTMLDivElement | null>(null);
const vehicle = ref<VehicleMeta | null>(null);
const candles = ref<Candle[]>([]);
const loading = ref(true);
const error = ref<string | null>(null);

const chartType = ref<ChartType>("candlestick");
const watchPriceDraft = ref<string>("");
const watchSaving = ref(false);
const budgetInputFocused = ref(false);

const chartTypes: { value: ChartType; label: string }[] = [
    { value: "candlestick", label: "Candles" },
    { value: "line", label: "Line" },
    { value: "area", label: "Area" },
    { value: "bar", label: "Bars" },
];

let chart: IChartApi | null = null;
let priceSeries: ISeriesApi<any> | null = null;
let volumeSeries: ISeriesApi<"Histogram"> | null = null;
let watchLine: IPriceLine | null = null;
let timer: number | null = null;
let firstRender = true;

function csrf(): string {
    const m = document.querySelector<HTMLMetaElement>(
        'meta[name="csrf-token"]'
    );
    return m ? m.content : "";
}

async function load() {
    try {
        const r = await fetch(`/api/vehicles/${props.wpId}/chart`, {
            credentials: "same-origin",
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        vehicle.value = data.vehicle;
        candles.value = data.candles;

        // Sync the input from the server only when the user isn't actively
        // editing it and no save is in flight. Otherwise a slow typist gets
        // their draft overwritten on the next poll.
        if (!watchSaving.value && !budgetInputFocused.value) {
            watchPriceDraft.value =
                data.vehicle?.watch_price != null
                    ? String(data.vehicle.watch_price)
                    : "";
        }

        if (!chart) initChart();
        renderChart();
        renderWatchLine();

        error.value = null;
    } catch (e: any) {
        error.value = e?.message ?? "unknown";
    } finally {
        loading.value = false;
        schedule();
    }
}

function schedule() {
    if (timer) window.clearTimeout(timer);
    timer = window.setTimeout(load, 5000);
}

function initChart() {
    if (!container.value) return;

    chart = createChart(container.value, {
        layout: {
            background: { type: ColorType.Solid, color: "transparent" },
            textColor: "#94a3b8",
            fontFamily: "ui-monospace, SFMono-Regular, monospace",
            fontSize: 11,
        },
        grid: {
            vertLines: { color: "rgba(148, 163, 184, 0.08)" },
            horzLines: { color: "rgba(148, 163, 184, 0.08)" },
        },
        rightPriceScale: {
            borderColor: "rgba(148, 163, 184, 0.2)",
            scaleMargins: { top: 0.1, bottom: 0.25 },
        },
        timeScale: {
            borderColor: "rgba(148, 163, 184, 0.2)",
            timeVisible: true,
            secondsVisible: false,
        },
        crosshair: {
            mode: 1,
            vertLine: { color: "#64748b", style: 3 },
            horzLine: { color: "#64748b", style: 3 },
        },
        autoSize: true,
    });

    volumeSeries = chart.addSeries(HistogramSeries, {
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
    });

    chart.priceScale("volume").applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
    });

    createPriceSeries();
}

function createPriceSeries() {
    if (!chart) return;

    if (priceSeries) {
        chart.removeSeries(priceSeries);
        priceSeries = null;
        watchLine = null;
    }

    switch (chartType.value) {
        case "line":
            priceSeries = chart.addSeries(LineSeries, {
                color: "#38bdf8",
                lineWidth: 2,
            });
            break;

        case "area":
            priceSeries = chart.addSeries(AreaSeries, {
                lineColor: "#38bdf8",
                topColor: "rgba(56, 189, 248, 0.35)",
                bottomColor: "rgba(56, 189, 248, 0)",
                lineWidth: 2,
            });
            break;

        case "bar":
            priceSeries = chart.addSeries(BarSeries, {
                upColor: "#10b981",
                downColor: "#ef4444",
            });
            break;

        case "candlestick":
        default:
            priceSeries = chart.addSeries(CandlestickSeries, {
                upColor: "#10b981",
                downColor: "#ef4444",
                borderUpColor: "#10b981",
                borderDownColor: "#ef4444",
                wickUpColor: "#10b981",
                wickDownColor: "#ef4444",
            });
            break;
    }
}

function renderChart() {
    if (!chart || !priceSeries || !candles.value.length) return;

    const time = (s: number) => s as UTCTimestamp;

    switch (chartType.value) {
        case "line":
        case "area":
            priceSeries.setData(
                candles.value.map((c) => ({
                    time: time(c.time),
                    value: c.close,
                }))
            );
            break;

        case "bar":
        case "candlestick":
        default:
            priceSeries.setData(
                candles.value.map((c) => ({
                    time: time(c.time),
                    open: c.open,
                    high: c.high,
                    low: c.low,
                    close: c.close,
                }))
            );
            break;
    }

    volumeSeries?.setData(
        candles.value.map((c) => ({
            time: time(c.time),
            value: c.volume,
            color:
                c.close >= c.open
                    ? "rgba(16, 185, 129, 0.35)"
                    : "rgba(239, 68, 68, 0.35)",
        }))
    );

    if (firstRender) {
        chart.timeScale().fitContent();
        firstRender = false;
    }
}

function renderWatchLine() {
    if (!priceSeries) return;

    if (watchLine) {
        try {
            priceSeries.removePriceLine(watchLine);
        } catch {
            /* series was swapped out; nothing to remove */
        }
        watchLine = null;
    }

    const wp = vehicle.value?.watch_price;
    if (wp == null || wp <= 0) return;

    watchLine = priceSeries.createPriceLine({
        price: wp,
        color: "#f59e0b",
        lineWidth: 1,
        lineStyle: 3,
        axisLabelVisible: true,
        title: "your max",
    });
}

function setChartType(type: ChartType) {
    if (type === chartType.value) return;
    chartType.value = type;
    createPriceSeries();
    renderChart();
    renderWatchLine();
}

async function toggleWatched() {
    await fetch(`/api/wp-vehicles/${props.wpId}/watch`, {
        method: "PATCH",
        headers: { "X-CSRF-TOKEN": csrf() },
        credentials: "same-origin",
    });
    await load();
}

function parseBudget(input: unknown): number | null {
    if (input === null || input === undefined) return null;
    const digits = String(input).replace(/[^\d]/g, "");
    if (digits === "") return null;
    const n = parseInt(digits, 10);
    return Number.isNaN(n) ? null : n;
}

function onBudgetFocus() {
    budgetInputFocused.value = true;
}

function onBudgetBlur() {
    budgetInputFocused.value = false;
    saveWatchPrice();
}

async function saveWatchPrice() {
    const value = parseBudget(watchPriceDraft.value);

    watchSaving.value = true;
    try {
        await fetch(`/api/wp-vehicles/${props.wpId}/watch-price`, {
            method: "PATCH",
            headers: {
                "Content-Type": "application/json",
                "X-CSRF-TOKEN": csrf(),
            },
            credentials: "same-origin",
            body: JSON.stringify({ watch_price: value }),
        });
        await load();
    } finally {
        watchSaving.value = false;
    }
}

onMounted(load);

onBeforeUnmount(() => {
    if (timer) window.clearTimeout(timer);
    chart?.remove();
    chart = null;
    priceSeries = null;
    volumeSeries = null;
    watchLine = null;
});

const recent = computed(() => [...candles.value].reverse().slice(0, 20));

const changePct = computed(() => {
    if (!candles.value.length) return 0;
    const first = candles.value[0].open;
    const last = candles.value[candles.value.length - 1].close;
    if (!first) return 0;
    return ((last - first) / first) * 100;
});

const dayHigh = computed(() =>
    candles.value.length ? Math.max(...candles.value.map((c) => c.high)) : null
);
const dayLow = computed(() =>
    candles.value.length ? Math.min(...candles.value.map((c) => c.low)) : null
);

/* ---------- Budget ---------- */

const budgetStatus = computed<BudgetStatus>(() => {
    const wp = vehicle.value?.watch_price;
    const cp = vehicle.value?.current_price;
    if (wp == null || wp <= 0 || cp == null) return "unset";
    if (cp > wp) return "over";
    const headroom = wp - cp;
    const pct = headroom / wp;
    if (pct >= 0.3) return "healthy";
    if (pct >= 0.1) return "tight";
    return "close";
});

const budgetHeadroom = computed<number>(() => {
    const wp = vehicle.value?.watch_price;
    const cp = vehicle.value?.current_price;
    if (wp == null || cp == null) return 0;
    return Math.max(0, wp - cp);
});

const budgetMessage = computed<string>(() => {
    const s = budgetStatus.value;
    if (s === "unset")
        return "Set a budget to track how much room you have left.";
    if (s === "over") {
        const over =
            (vehicle.value?.current_price ?? 0) -
            (vehicle.value?.watch_price ?? 0);
        return `Over budget by ${fmt(over)}. You would not win at this price.`;
    }
    if (s === "close")
        return `Very close to your limit. Only ${fmt(budgetHeadroom.value)} of room left.`;
    if (s === "tight")
        return `${fmt(budgetHeadroom.value)} of room before you hit your budget.`;
    return `${fmt(budgetHeadroom.value)} of room. Comfortable.`;
});

const crossedWatch = computed(() => budgetStatus.value === "over");

/* ---------- Formatting helpers ---------- */

function fmt(n: number | null | undefined): string {
    return n == null ? "—" : Number(n).toLocaleString();
}

function timeLeft(s: number | null): string {
    if (s == null) return "—";
    if (s <= 0) return "closed";
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return `${h}h ${m}m`;
}

function fmtTime(unix: number): string {
    return new Date(unix * 1000).toLocaleTimeString();
}
</script>

<template>
    <Head :title="vehicle?.name ?? 'Vehicle'" />

    <div class="mx-auto max-w-7xl space-y-5 p-6">
        <header class="flex items-start justify-between gap-4">
            <div class="min-w-0">
                <Link
                    href="/"
                    class="text-xs text-muted-foreground hover:text-foreground"
                >
                    ← Back to auctions
                </Link>
                <h1 class="mt-1 truncate text-2xl font-semibold tracking-tight">
                    {{ vehicle?.name ?? "Loading…" }}
                </h1>
                <p class="text-xs text-muted-foreground">
                    Auction #{{ wpId }} · {{ vehicle?.status ?? "" }}
                </p>
            </div>

            <div class="flex items-center gap-4">
                <button
                    class="rounded border px-2.5 py-1 text-xs transition-colors"
                    :class="
                        vehicle?.watched
                            ? 'border-amber-500 text-amber-400'
                            : 'text-muted-foreground hover:text-foreground'
                    "
                    @click="toggleWatched"
                >
                    {{ vehicle?.watched ? "★ Starred" : "☆ Star this" }}
                </button>

                <div class="text-right">
                    <div
                        class="font-mono text-3xl font-semibold tracking-tight"
                        :class="{ 'text-amber-400': crossedWatch }"
                    >
                        {{ fmt(vehicle?.current_price) }}
                    </div>
                    <div
                        class="font-mono text-sm"
                        :class="
                            changePct >= 0
                                ? 'text-emerald-400'
                                : 'text-rose-400'
                        "
                    >
                        {{ changePct >= 0 ? "+" : ""
                        }}{{ changePct.toFixed(2) }}%
                    </div>
                </div>
            </div>
        </header>

        <p
            v-if="error"
            class="rounded border border-rose-800 bg-rose-950/40 px-3 py-2 text-sm text-rose-300"
        >
            {{ error }}
        </p>

        <!-- Stat strip -->
        <section class="grid grid-cols-2 gap-3 md:grid-cols-5">
            <div class="rounded border p-3">
                <div
                    class="text-xs uppercase tracking-wide text-muted-foreground"
                >
                    Started at
                </div>
                <div class="font-mono text-lg">
                    {{ fmt(vehicle?.base_price) }}
                </div>
            </div>
            <div class="rounded border p-3">
                <div
                    class="text-xs uppercase tracking-wide text-muted-foreground"
                >
                    Highest so far
                </div>
                <div class="font-mono text-lg text-emerald-400">
                    {{ fmt(dayHigh) }}
                </div>
            </div>
            <div class="rounded border p-3">
                <div
                    class="text-xs uppercase tracking-wide text-muted-foreground"
                >
                    Lowest so far
                </div>
                <div class="font-mono text-lg text-rose-400">
                    {{ fmt(dayLow) }}
                </div>
            </div>
            <div class="rounded border p-3">
                <div
                    class="text-xs uppercase tracking-wide text-muted-foreground"
                >
                    Bids placed
                </div>
                <div class="font-mono text-lg">
                    {{ vehicle?.bid_count ?? "—" }}
                </div>
            </div>
            <div class="rounded border p-3">
                <div
                    class="text-xs uppercase tracking-wide text-muted-foreground"
                >
                    Closes in
                </div>
                <div class="font-mono text-lg">
                    {{ timeLeft(vehicle?.seconds_left ?? null) }}
                </div>
            </div>
        </section>

        <!-- Budget strip -->
        <section
            class="rounded border p-3"
            :class="{
                'border-rose-700 bg-rose-950/30': budgetStatus === 'over',
                'border-amber-700 bg-amber-950/20':
                    budgetStatus === 'close' || budgetStatus === 'tight',
                'border-emerald-800 bg-emerald-950/10':
                    budgetStatus === 'healthy',
                'border-dashed': budgetStatus === 'unset',
            }"
        >
            <div
                class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
            >
                <div class="flex items-start gap-3">
                    <div
                        class="text-2xl leading-none"
                        :class="{
                            'text-rose-400': budgetStatus === 'over',
                            'text-amber-400':
                                budgetStatus === 'close' ||
                                budgetStatus === 'tight',
                            'text-emerald-400': budgetStatus === 'healthy',
                            'text-muted-foreground': budgetStatus === 'unset',
                        }"
                    >
                        {{
                            budgetStatus === "over"
                                ? "✕"
                                : budgetStatus === "unset"
                                  ? "○"
                                  : "✓"
                        }}
                    </div>
                    <div class="min-w-0">
                        <div
                            class="text-xs uppercase tracking-wide text-muted-foreground"
                        >
                            Your budget
                        </div>
                        <div class="font-mono text-lg">
                            {{
                                vehicle?.watch_price != null
                                    ? fmt(vehicle.watch_price)
                                    : "—"
                            }}
                        </div>
                        <p
                            class="mt-1 max-w-prose text-xs text-muted-foreground"
                        >
                            {{ budgetMessage }}
                        </p>
                    </div>
                </div>

                <div class="flex items-center gap-2 sm:self-start">
                    <input
                        v-model="watchPriceDraft"
                        type="text"
                        inputmode="numeric"
                        autocomplete="off"
                        placeholder="my max"
                        class="w-36 rounded border bg-background px-2 py-1.5 text-right font-mono text-sm"
                        :class="{ 'border-amber-500': crossedWatch }"
                        @focus="onBudgetFocus"
                        @blur="onBudgetBlur"
                        @keydown.enter.prevent="onBudgetBlur"
                    />
                </div>
            </div>
        </section>

        <!-- Chart -->
        <section class="rounded border p-3">
            <div class="mb-3 flex items-center justify-between">
                <div
                    class="text-xs uppercase tracking-wide text-muted-foreground"
                >
                    Price over time
                </div>
                <div class="flex gap-1 rounded border p-0.5">
                    <button
                        v-for="t in chartTypes"
                        :key="t.value"
                        class="rounded px-2.5 py-1 text-xs transition-colors"
                        :class="
                            chartType === t.value
                                ? 'bg-sky-600 text-white'
                                : 'text-muted-foreground hover:text-foreground'
                        "
                        @click="setChartType(t.value)"
                    >
                        {{ t.label }}
                    </button>
                </div>
            </div>

            <div ref="container" class="h-[440px] w-full"></div>

            <div
                v-if="loading && !candles.length"
                class="text-center text-sm text-muted-foreground"
            >
                Loading…
            </div>
            <div
                v-else-if="!candles.length"
                class="text-center text-sm text-muted-foreground"
            >
                No price history yet.
            </div>
        </section>

        <!-- Recent price table -->
        <section v-if="recent.length" class="overflow-hidden rounded border">
            <table class="w-full text-sm">
                <thead class="bg-muted text-muted-foreground">
                    <tr>
                        <th class="p-2 text-left">Time</th>
                        <th class="p-2 text-right">Open</th>
                        <th class="p-2 text-right">High</th>
                        <th class="p-2 text-right">Low</th>
                        <th class="p-2 text-right">Close</th>
                        <th class="p-2 text-right">Total moved</th>
                        <th class="p-2 text-right">Bids</th>
                    </tr>
                </thead>
                <tbody>
                    <tr v-for="c in recent" :key="c.time" class="border-t">
                        <td class="p-2 font-mono text-muted-foreground">
                            {{ fmtTime(c.time) }}
                        </td>
                        <td class="p-2 text-right font-mono">
                            {{ fmt(c.open) }}
                        </td>
                        <td class="p-2 text-right font-mono text-emerald-400">
                            {{ fmt(c.high) }}
                        </td>
                        <td class="p-2 text-right font-mono text-rose-400">
                            {{ fmt(c.low) }}
                        </td>
                        <td class="p-2 text-right font-mono text-sky-300">
                            {{ fmt(c.close) }}
                        </td>
                        <td
                            class="p-2 text-right font-mono text-muted-foreground"
                        >
                            {{ fmt(c.volume) }}
                        </td>
                        <td class="p-2 text-right text-muted-foreground">
                            {{ c.bid_count }}
                        </td>
                    </tr>
                </tbody>
            </table>
        </section>
    </div>
</template>