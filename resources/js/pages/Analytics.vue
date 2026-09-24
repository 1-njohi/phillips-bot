<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { Head, Link } from "@inertiajs/vue3";
import {
    ColorType,
    LineSeries,
    createChart,
    type IChartApi,
    type ISeriesApi,
    type UTCTimestamp,
} from "lightweight-charts";

interface Series {
    wp_id: number;
    name: string;
    points: { time: number; value: number }[];
}

interface Leader {
    wp_id: number;
    name: string;
    value: number | null;
    secondary?: number | null;
}

interface Leaderboards {
    window_minutes: number;
    fastest_climbers: Leader[];
    highest_value: Leader[];
    biggest_delta: Leader[];
    hottest: Leader[];
    accelerating: Leader[];
    biggest_gainer: Leader[];
    coldest: Leader[];
    biggest_bid: Leader[];
}

type BoardKey =
    | "fastest_climbers"
    | "highest_value"
    | "biggest_delta"
    | "hottest"
    | "accelerating"
    | "biggest_gainer"
    | "coldest"
    | "biggest_bid";

const BOARD_KEYS: BoardKey[] = [
    "fastest_climbers",
    "highest_value",
    "biggest_delta",
    "hottest",
    "accelerating",
    "biggest_gainer",
    "coldest",
    "biggest_bid",
];

interface Shift {
    direction: "up" | "down";
    amount: number;
}

interface RenderItem {
    wp_id: number;
    name: string;
    right: string;
    rightSub: string | null;
    rightClass: string;
}

interface RenderCard {
    key: BoardKey;
    title: string;
    window: string | null;
    items: RenderItem[];
}

type TopN = 3 | 5 | 10 | 15 | "all";

const COLORS = [
    "#38bdf8",
    "#34d399",
    "#fbbf24",
    "#f472b6",
    "#a78bfa",
    "#f87171",
    "#22d3ee",
    "#facc15",
    "#4ade80",
    "#fb923c",
];

const TOP_N_OPTIONS: TopN[] = [3, 5, 10, 15, "all"];

const container = ref<HTMLDivElement | null>(null);
const series = ref<Series[]>([]);
const boards = ref<Leaderboards | null>(null);
const error = ref<string | null>(null);
const loading = ref(true);
const topN = ref<TopN>(10);

const previousRanks = ref<Record<BoardKey, Record<number, number>>>(
    Object.fromEntries(BOARD_KEYS.map((k) => [k, {}])) as Record<
        BoardKey,
        Record<number, number>
    >
);

const shifts = ref<Record<BoardKey, Record<number, Shift>>>(
    Object.fromEntries(BOARD_KEYS.map((k) => [k, {}])) as Record<
        BoardKey,
        Record<number, Shift>
    >
);

let chart: IChartApi | null = null;
let lineSeries: ISeriesApi<"Line">[] = [];
let timer: number | null = null;
let hasLoadedOnce = false;

async function load() {
    try {
        const [ov, lb] = await Promise.all([
            fetch("/api/analytics/overlay", {
                credentials: "same-origin",
            }).then((r) => r.json()),
            fetch("/api/analytics/leaderboards", {
                credentials: "same-origin",
            }).then((r) => r.json()),
        ]);

        series.value = ov.series ?? [];
        boards.value = lb;

        computeShifts();
        renderChart();
        error.value = null;
    } catch (e: any) {
        error.value = e?.message ?? "unknown";
    } finally {
        loading.value = false;
        hasLoadedOnce = true;
        schedule();
    }
}

function computeShifts() {
    if (!boards.value) return;

    for (const key of BOARD_KEYS) {
        const list = boards.value[key] ?? [];
        const prev = previousRanks.value[key];
        const next: Record<number, number> = {};

        list.forEach((entry, index) => {
            next[entry.wp_id] = index;

            if (!hasLoadedOnce || prev[entry.wp_id] === undefined) return;

            const delta = prev[entry.wp_id] - index;

            if (delta !== 0) {
                shifts.value[key][entry.wp_id] = {
                    direction: delta > 0 ? "up" : "down",
                    amount: Math.abs(delta),
                };
            }
        });

        previousRanks.value[key] = next;
    }
}

function shiftDirection(key: BoardKey, wpId: number): "up" | "down" | null {
    return shifts.value[key][wpId]?.direction ?? null;
}

function shiftAmount(key: BoardKey, wpId: number): number {
    return shifts.value[key][wpId]?.amount ?? 0;
}

function rowClass(key: BoardKey, wpId: number): string {
    const dir = shiftDirection(key, wpId);
    if (dir === "up") return "border-l-2 border-emerald-500 bg-white/[0.05]";
    if (dir === "down") return "border-l-2 border-rose-500 bg-white/[0.05]";
    return "border-l-2 border-transparent";
}

function brighten(baseClass: string): string {
    const map: Record<string, string> = {
        "text-emerald-400": "text-emerald-300",
        "text-rose-400": "text-rose-300",
        "text-sky-400": "text-sky-300",
        "text-amber-400": "text-amber-300",
        "text-sky-300": "text-sky-200",
    };
    return map[baseClass] ?? baseClass;
}

function valueClass(key: BoardKey, wpId: number, baseClass: string): string {
    return shiftDirection(key, wpId) ? brighten(baseClass) : baseClass;
}

function subClass(key: BoardKey, wpId: number): string {
    return shiftDirection(key, wpId)
        ? "text-slate-300"
        : "text-muted-foreground";
}

function setTopN(n: TopN) {
    topN.value = n;
}

function schedule() {
    if (timer) window.clearTimeout(timer);
    timer = window.setTimeout(load, 8000);
}

function renderChart() {
    if (!container.value) return;

    if (!chart) {
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
            },
            timeScale: {
                borderColor: "rgba(148, 163, 184, 0.2)",
                timeVisible: true,
            },
            crosshair: {
                mode: 1,
                vertLine: { color: "#64748b", style: 3 },
                horzLine: { color: "#64748b", style: 3 },
            },
            autoSize: true,
        });
    }

    lineSeries.forEach((s) => chart!.removeSeries(s));
    lineSeries = [];

    series.value.forEach((s, i) => {
        const line = chart!.addSeries(LineSeries, {
            color: COLORS[i % COLORS.length],
            lineWidth: 2,
        });
        line.setData(
            s.points.map((p) => ({
                time: p.time as UTCTimestamp,
                value: p.value,
            }))
        );
        lineSeries.push(line);
    });

    chart.timeScale().fitContent();
}

onMounted(load);

onBeforeUnmount(() => {
    if (timer) window.clearTimeout(timer);
    chart?.remove();
    chart = null;
    lineSeries = [];
});

function fmt(n: number | null | undefined): string {
    return n == null ? "—" : Number(n).toLocaleString();
}

function fmtSigned(n: number | null | undefined): string {
    if (n == null) return "—";
    const v = Math.round(n);
    return (v > 0 ? "+" : "") + v.toLocaleString();
}

/** Per-minute value from the API, shown per hour. */
function fmtPerHour(n: number | null | undefined): string {
    if (n == null) return "—";
    const v = Math.round(n * 60);
    return (v > 0 ? "+" : "") + v.toLocaleString();
}

function fmtPct(n: number | null | undefined): string {
    if (n == null) return "—";
    return `${n > 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function fmtTime(ts: number | null | undefined): string {
    if (ts == null) return "—";
    return new Date(ts * 1000).toLocaleTimeString();
}

const cards = computed<RenderCard[]>(() => {
    if (!boards.value) return [];
    const b = boards.value;
    const wm = b.window_minutes;
    const d10 = Math.min(10, wm);

    const slice = (arr: Leader[]) =>
        topN.value === "all" ? arr : arr.slice(0, topN.value as number);

    return [
        {
            key: "fastest_climbers",
            title: "Rising fastest",
            window: `last ${wm} min`,
            items: slice(b.fastest_climbers ?? []).map((l) => ({
                wp_id: l.wp_id,
                name: l.name,
                right: fmtSigned(l.secondary),
                rightSub: `${fmtPerHour(l.value)}/hr`,
                rightClass: "text-emerald-400",
            })),
        },
        {
            key: "highest_value",
            title: "Highest price",
            window: null,
            items: slice(b.highest_value ?? []).map((l) => ({
                wp_id: l.wp_id,
                name: l.name,
                right: fmt(l.value),
                rightSub: null,
                rightClass: "text-foreground",
            })),
        },
        {
            key: "biggest_delta",
            title: "Biggest jump",
            window: `last ${d10} min`,
            items: slice(b.biggest_delta ?? []).map((l) => ({
                wp_id: l.wp_id,
                name: l.name,
                right: fmtSigned(l.value),
                rightSub: `${fmtPerHour(l.secondary)}/hr`,
                rightClass: "text-emerald-400",
            })),
        },
        {
            key: "hottest",
            title: "Most competitive",
            window: `last ${wm} min`,
            items: slice(b.hottest ?? []).map((l) => ({
                wp_id: l.wp_id,
                name: l.name,
                right: `${fmt(l.value)} bids`,
                rightSub: `${
                    l.secondary != null ? (l.secondary * 60).toFixed(1) : "—"
                }/hr`,
                rightClass: "text-sky-400",
            })),
        },
        {
            key: "accelerating",
            title: "Heating up",
            window: "vs the past hour",
            items: slice(b.accelerating ?? []).map((l) => ({
                wp_id: l.wp_id,
                name: l.name,
                right: `${fmtPerHour(l.value)}/hr`,
                rightSub:
                    l.secondary != null
                        ? `${fmtPerHour(l.secondary)}/hr now`
                        : null,
                rightClass:
                    (l.value ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400",
            })),
        },
        {
            key: "biggest_gainer",
            title: "Up the most",
            window: "since auction opened",
            items: slice(b.biggest_gainer ?? []).map((l) => ({
                wp_id: l.wp_id,
                name: l.name,
                right: fmtPct(l.value),
                rightSub: null,
                rightClass: "text-emerald-400",
            })),
        },
        {
            key: "coldest",
            title: "Quietest",
            window: `last ${wm} min`,
            items: slice(b.coldest ?? []).map((l) => ({
                wp_id: l.wp_id,
                name: l.name,
                right: fmtSigned(l.secondary),
                rightSub: `${fmtPerHour(l.value)}/hr`,
                rightClass: "text-sky-300",
            })),
        },
        {
            key: "biggest_bid",
            title: "Largest single bid",
            window: "last 30 min",
            items: slice(b.biggest_bid ?? []).map((l) => ({
                wp_id: l.wp_id,
                name: l.name,
                right: `+${fmt(l.value)}`,
                rightSub: fmtTime(l.secondary),
                rightClass: "text-amber-400",
            })),
        },
    ];
});
</script>

<template>
    <Head title="Insights" />

    <div class="mx-auto max-w-[1400px] space-y-5 p-6">
        <header class="flex flex-wrap items-start justify-between gap-3">
            <div>
                <Link
                    href="/"
                    class="text-xs text-muted-foreground hover:text-foreground"
                >
                    ← Back to auctions
                </Link>
                <h1 class="mt-1 text-2xl font-semibold tracking-tight">
                    Insights
                </h1>
                <p class="text-xs text-muted-foreground">
                    How the auctions are moving, side by side
                </p>
            </div>

            <div class="flex items-center gap-2">
                <span
                    class="text-xs uppercase tracking-wide text-muted-foreground"
                >
                    Show top
                </span>
                <div class="flex gap-0.5 rounded border p-0.5">
                    <button
                        v-for="n in TOP_N_OPTIONS"
                        :key="String(n)"
                        class="rounded px-2.5 py-1 text-xs transition-colors"
                        :class="
                            topN === n
                                ? 'bg-sky-600 text-white'
                                : 'text-muted-foreground hover:text-foreground'
                        "
                        @click="setTopN(n)"
                    >
                        {{ n === "all" ? "All" : n }}
                    </button>
                </div>
            </div>
        </header>

        <p
            v-if="error"
            class="rounded border border-rose-800 bg-rose-950/40 px-3 py-2 text-sm text-rose-300"
        >
            {{ error }}
        </p>

        <section
            v-if="boards"
            class="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4"
        >
            <div
                v-for="card in cards"
                :key="card.key"
                class="rounded border p-4"
            >
                <div
                    class="text-xs uppercase tracking-wide text-muted-foreground"
                >
                    {{ card.title }}
                    <span
                        v-if="card.window"
                        class="ml-1 text-muted-foreground/70"
                    >
                        ({{ card.window }})
                    </span>
                </div>

                <ol v-if="card.items.length" class="mt-2 space-y-1 text-sm">
                    <li
                        v-for="item in card.items"
                        :key="item.wp_id"
                        class="flex items-baseline justify-between gap-2 rounded-sm py-0.5 pl-2 pr-1 transition-colors"
                        :class="rowClass(card.key, item.wp_id)"
                    >
                        <span class="flex min-w-0 items-baseline gap-1.5">
                            <Link
                                :href="`/vehicles/${item.wp_id}`"
                                class="truncate hover:text-sky-400"
                            >
                                {{ item.name }}
                            </Link>
                            <span
                                v-if="
                                    shiftDirection(card.key, item.wp_id) ===
                                    'up'
                                "
                                class="shrink-0 font-mono text-[10px] font-semibold text-emerald-300"
                                :title="`Up ${shiftAmount(
                                    card.key,
                                    item.wp_id
                                )} places`"
                            >
                                ▲{{ shiftAmount(card.key, item.wp_id) }}
                            </span>
                            <span
                                v-else-if="
                                    shiftDirection(card.key, item.wp_id) ===
                                    'down'
                                "
                                class="shrink-0 font-mono text-[10px] font-semibold text-rose-300"
                                :title="`Down ${shiftAmount(
                                    card.key,
                                    item.wp_id
                                )} places`"
                            >
                                ▼{{ shiftAmount(card.key, item.wp_id) }}
                            </span>
                        </span>
                        <span class="whitespace-nowrap font-mono text-right">
                            <span
                                :class="
                                    valueClass(
                                        card.key,
                                        item.wp_id,
                                        item.rightClass
                                    )
                                "
                            >
                                {{ item.right }}
                            </span>
                            <span
                                v-if="item.rightSub"
                                class="ml-1 text-xs"
                                :class="subClass(card.key, item.wp_id)"
                            >
                                ({{ item.rightSub }})
                            </span>
                        </span>
                    </li>
                </ol>
                <p v-else class="mt-2 text-xs text-muted-foreground">
                    Waiting for data…
                </p>
            </div>
        </section>

        <section class="rounded border p-3">
            <div class="mb-3 flex items-center justify-between">
                <div
                    class="text-xs uppercase tracking-wide text-muted-foreground"
                >
                    Price comparison · every auction
                </div>
                <div class="flex flex-wrap gap-2 text-xs">
                    <span
                        v-for="(s, i) in series"
                        :key="s.wp_id"
                        class="flex items-center gap-1"
                    >
                        <span
                            class="inline-block h-2 w-2 rounded-full"
                            :style="{
                                backgroundColor: COLORS[i % COLORS.length],
                            }"
                        ></span>
                        <span class="text-muted-foreground">{{ s.name }}</span>
                    </span>
                </div>
            </div>

            <div ref="container" class="h-[480px] w-full"></div>

            <div
                v-if="loading && !series.length"
                class="py-6 text-center text-sm text-muted-foreground"
            >
                Loading…
            </div>
            <div
                v-else-if="!series.length"
                class="py-6 text-center text-sm text-muted-foreground"
            >
                Nothing to compare yet.
            </div>
        </section>
    </div>
</template>

<style scoped>
/* intentionally empty — anchors the SFC parser */
</style>
