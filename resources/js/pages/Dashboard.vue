<script setup lang="ts">
import {
    computed,
    nextTick,
    onBeforeUnmount,
    onMounted,
    ref,
} from "vue";
import { Head, Link } from "@inertiajs/vue3";
import BudgetAlertToasts from "@/components/BudgetAlertToasts.vue";
import { useAuctionState } from "@/composables/useAuctionState";
import { useBudgetAlerts } from "@/composables/useBudgetAlerts";
import { fmt, timeLeft } from "@/lib/format";
import type { VehicleRow } from "@/types/auction";

/*
 * NOTE: the composable needs to expose pausePolling / resumePolling.
 * See the notes after this file for a sketch of that change.
 */
const {
    state,
    error,
    refreshRoster,
    toggleWatch,
    setWatchPrice,
    pausePolling,
    resumePolling,
} = useAuctionState();

/* ---------- Budget alerts (toasts + sound) ---------- */

const vehicles = computed(() => state.value.vehicles);
const { alerts, dismiss, dismissAll, soundEnabled, setSoundEnabled } =
    useBudgetAlerts(vehicles);

/* ---------- Sort + filter ---------- */

type SortKey = "price" | "delta" | "finish" | "velocity" | "headroom";
type FilterKey = "all" | "watched" | "moving" | "quiet";
type BudgetStatus = "unset" | "healthy" | "tight" | "close" | "over";

const sortKey = ref<SortKey>("finish");
const sortDir = ref<"asc" | "desc">("asc");
const filter = ref<FilterKey>("all");

const filterLabels: Record<FilterKey, string> = {
    all: "All",
    watched: "Starred",
    moving: "Moving",
    quiet: "Quiet",
};
const filterKeys: FilterKey[] = ["all", "watched", "moving", "quiet"];

const sortLabels: Record<SortKey, string> = {
    finish: "Closes in",
    price: "Current price",
    delta: "Last 10 min",
    velocity: "Moving at",
    headroom: "Budget room",
};

/* ---------- Show details toggle (persisted) ---------- */

const STORAGE_KEY = "auction.roster.showDetails";

const showDetails = ref(
    typeof window !== "undefined" &&
        window.localStorage.getItem(STORAGE_KEY) === "1",
);

function setShowDetails(v: boolean) {
    showDetails.value = v;
    if (typeof window !== "undefined") {
        window.localStorage.setItem(STORAGE_KEY, v ? "1" : "0");
    }
}

/* ---------- Budget calculation ---------- */

function budgetHeadroom(v: VehicleRow): number {
    if (v.watch_price == null || v.current_price == null) return 0;
    return Math.max(0, v.watch_price - v.current_price);
}

function budgetStatus(v: VehicleRow): BudgetStatus {
    if (v.watch_price == null || v.watch_price <= 0) return "unset";
    if (v.current_price == null) return "unset";
    if (v.current_price > v.watch_price) return "over";

    const headroom = v.watch_price - v.current_price;
    const pct = headroom / v.watch_price;
    if (pct >= 0.3) return "healthy";
    if (pct >= 0.1) return "tight";
    return "close";
}

function budgetBadgeClass(s: BudgetStatus): string {
    return {
        unset: "text-muted-foreground underline decoration-dotted hover:text-foreground",
        healthy: "text-emerald-400",
        tight: "text-amber-400",
        close: "text-amber-400",
        over: "text-rose-400",
    }[s];
}

function budgetLabel(v: VehicleRow): string {
    const s = budgetStatus(v);
    if (s === "unset") return "set a budget";
    if (s === "over") return "✕ over budget";
    if (s === "close") return "⚠ close to max";
    if (s === "tight") return `⚠ ${fmt(budgetHeadroom(v))} room`;
    return `✓ ${fmt(budgetHeadroom(v))} room`;
}

/* ---------- Rows: filter + sort ---------- */

function filterAndSort(source: VehicleRow[]): VehicleRow[] {
    let list = [...source];

    if (filter.value === "watched") list = list.filter((v) => v.watched);
    if (filter.value === "moving")
        list = list.filter((v) => (v.velocity_15m ?? 0) > 0);
    if (filter.value === "quiet") list = list.filter((v) => v.is_quiet);

    const getters: Record<SortKey, (v: VehicleRow) => number> = {
        price: (v) => v.current_price ?? 0,
        delta: (v) => v.delta_10m ?? -Infinity,
        finish: (v) => v.seconds_left ?? Infinity,
        velocity: (v) => v.velocity_15m ?? -Infinity,
        headroom: (v) => budgetHeadroom(v),
    };

    const get = getters[sortKey.value];
    list.sort((a, b) => {
        const av = get(a);
        const bv = get(b);
        const d = av < bv ? -1 : av > bv ? 1 : 0;
        return sortDir.value === "asc" ? d : -d;
    });

    return list;
}

const liveRows = computed(() => filterAndSort(state.value.vehicles));

/* ---------- Freeze ---------- */

/*
 * While frozen we pin the ORDER (a list of ids), but still render each
 * row from the current state. That way:
 *  - rows never jump around, even if a stray poll lands,
 *  - starring / setting a budget still shows up immediately.
 */
const frozen = ref(false);
const frozenIds = ref<VehicleRow["id"][]>([]);
const frozenAt = ref<Date | null>(null);

function pinOrder() {
    frozenIds.value = liveRows.value.map((v) => v.id);
}

function setFrozen(next: boolean) {
    if (next === frozen.value) return;
    frozen.value = next;

    if (next) {
        pinOrder();
        frozenAt.value = new Date();
        pausePolling();
    } else {
        frozenIds.value = [];
        frozenAt.value = null;
        resumePolling();
    }
}

const rows = computed<VehicleRow[]>(() => {
    if (!frozen.value) return liveRows.value;

    const byId = new Map(state.value.vehicles.map((v) => [v.id, v]));
    return frozenIds.value
        .map((id) => byId.get(id))
        .filter((v): v is VehicleRow => v !== undefined);
});

function setFilter(f: FilterKey) {
    filter.value = f;
    if (frozen.value) pinOrder();
}

function setSort(k: SortKey) {
    if (sortKey.value === k) {
        sortDir.value = sortDir.value === "asc" ? "desc" : "asc";
    } else {
        sortKey.value = k;
        sortDir.value = k === "finish" ? "asc" : "desc";
    }
    if (frozen.value) pinOrder();
}

function toggleSortDir() {
    sortDir.value = sortDir.value === "asc" ? "desc" : "asc";
    if (frozen.value) pinOrder();
}

function sortArrow(k: SortKey): string {
    if (sortKey.value !== k) return "";
    return sortDir.value === "asc" ? " ↑" : " ↓";
}

/* ---------- Budget popover / bottom sheet ---------- */

const POPOVER_WIDTH = 240;
const MOBILE_BREAKPOINT = 640;

interface PopoverState {
    vehicle: VehicleRow;
    draft: string;
    x: number;
    y: number;
    sheet: boolean;
}

const popover = ref<PopoverState | null>(null);
const popoverInput = ref<HTMLInputElement | null>(null);
const popoverInputFocused = ref(false);

function openBudgetPopover(v: VehicleRow, event: MouseEvent) {
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    const sheet = window.innerWidth < MOBILE_BREAKPOINT;

    let x = rect.left;
    if (x + POPOVER_WIDTH > window.innerWidth - 8) {
        x = window.innerWidth - POPOVER_WIDTH - 8;
    }
    if (x < 8) x = 8;

    let y = rect.bottom + 8;
    if (y + 140 > window.innerHeight - 8) {
        y = rect.top - 140 - 8;
    }
    if (y < 8) y = 8;

    popoverInputFocused.value = false;
    popover.value = {
        vehicle: v,
        draft: v.watch_price != null ? String(v.watch_price) : "",
        x,
        y,
        sheet,
    };

    nextTick(() => {
        popoverInput.value?.focus();
        popoverInput.value?.select();
    });
}

function closePopover() {
    popoverInputFocused.value = false;
    popover.value = null;
}

function parseBudget(input: unknown): number | null {
    if (input === null || input === undefined) return null;
    const digits = String(input).replace(/[^\d]/g, "");
    if (digits === "") return null;
    const n = parseInt(digits, 10);
    return Number.isNaN(n) ? null : n;
}

async function saveBudget() {
    if (!popover.value) return;

    const value = parseBudget(popover.value.draft);
    const v = popover.value.vehicle;

    closePopover();
    await setWatchPrice(v, value);
}

async function clearBudget() {
    if (!popover.value) return;
    const v = popover.value.vehicle;
    closePopover();
    await setWatchPrice(v, null);
}

/* ---------- Global listeners ---------- */

function onDocumentClick(e: MouseEvent) {
    if (!popover.value) return;
    // Never close while the user is actively typing in the popover.
    if (popoverInputFocused.value) return;

    const target = e.target as HTMLElement;
    if (target.closest("[data-budget-popover]")) return;
    if (target.closest("[data-budget-badge]")) return;
    closePopover();
}

function onEscape(e: KeyboardEvent) {
    if (e.key === "Escape" && popover.value) closePopover();
}

function onScroll() {
    if (!popover.value) return;
    // The mobile keyboard and poll-triggered re-renders can fire scroll
    // events. Don't close the popover while the user is typing.
    if (popoverInputFocused.value) return;
    // The bottom sheet is fixed to the screen, so scrolling doesn't
    // detach it from anything.
    if (popover.value.sheet) return;
    closePopover();
}

onMounted(() => {
    document.addEventListener("click", onDocumentClick);
    document.addEventListener("keydown", onEscape);
    window.addEventListener("scroll", onScroll, true);
});

onBeforeUnmount(() => {
    document.removeEventListener("click", onDocumentClick);
    document.removeEventListener("keydown", onEscape);
    window.removeEventListener("scroll", onScroll, true);
    // Don't leave polling paused if the user navigates away while frozen.
    if (frozen.value) resumePolling();
});

/* ---------- Misc helpers ---------- */

function velocityClass(v: number | null): string {
    if (v == null || v === 0) return "text-muted-foreground";
    return v > 0 ? "text-emerald-400" : "text-rose-400";
}

function velocityText(v: number | null): string {
    if (v == null) return "—";
    if (v === 0) return "no change";
    const perHour = Math.round(v * 60);
    return `${perHour > 0 ? "+" : ""}${perHour.toLocaleString()}/hr`;
}

function deltaText(v: number | null): string {
    if (v == null) return "—";
    return (v > 0 ? "+" : "") + fmt(v);
}

function severityClass(s: string): string {
    return (
        {
            info: "text-sky-400",
            good: "text-emerald-400",
            warn: "text-amber-400",
        }[s] ?? "text-muted-foreground"
    );
}

function eventTypeLabel(t: string): string {
    return (
        {
            jump: "new bid",
            threshold: "milestone",
            stall: "no bids",
        }[t] ?? t
    );
}

function healthClass(n: number): string {
    return n > 0 ? "text-amber-400" : "text-muted-foreground";
}

function sparkPath(prices: number[], width = 80, height = 24): string {
    if (prices.length < 2) return "";
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const range = max - min || 1;
    const stepX = width / (prices.length - 1);

    return prices
        .map((p, i) => {
            const x = i * stepX;
            const y = height - ((p - min) / range) * height;
            return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
        })
        .join(" ");
}
</script>

<template>
    <Head title="Auction Monitor" />

    <div>
        <div class="mx-auto max-w-[1400px] space-y-4 p-3 sm:space-y-5 sm:p-6">
            <!-- Header -->
            <header
                class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
            >
                <div>
                    <h1
                        class="text-xl font-semibold tracking-tight sm:text-2xl"
                    >
                        Auction Monitor
                    </h1>
                    <p class="text-xs text-muted-foreground">
                        Watch the prices move. No bids are ever placed for you.
                    </p>
                </div>
                <div class="flex flex-wrap items-center gap-2">
                    <button
                        class="min-h-9 rounded border px-2.5 py-1.5 text-sm hover:bg-muted"
                        :title="
                            soundEnabled
                                ? 'Mute budget alerts'
                                : 'Unmute budget alerts'
                        "
                        :aria-label="
                            soundEnabled
                                ? 'Mute budget alerts'
                                : 'Unmute budget alerts'
                        "
                        @click="setSoundEnabled(!soundEnabled)"
                    >
                        {{ soundEnabled ? "🔊" : "🔇" }}
                    </button>
                    <Link
                        href="/analytics"
                        class="inline-flex min-h-9 items-center rounded border px-3 py-1.5 text-sm hover:bg-muted"
                    >
                        Insights →
                    </Link>
                    <button
                        class="min-h-9 rounded border px-3 py-1.5 text-sm"
                        :class="
                            frozen
                                ? 'border-amber-400 bg-amber-500/20 text-amber-300 hover:bg-amber-500/30'
                                : 'hover:bg-muted'
                        "
                        :aria-pressed="frozen"
                        :title="
                            frozen
                                ? 'Resume live updates'
                                : 'Pause updates so the list stays put'
                        "
                        @click="setFrozen(!frozen)"
                    >
                        {{ frozen ? "▶ Unfreeze" : "❄ Freeze" }}
                    </button>
                    <button
                        class="min-h-9 rounded bg-sky-600 px-3 py-1.5 text-sm text-white hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-40"
                        :disabled="frozen"
                        :title="frozen ? 'Unfreeze to refresh' : undefined"
                        @click="refreshRoster"
                    >
                        Refresh auctions
                    </button>
                </div>
            </header>

            <!-- Frozen banner -->
            <div
                v-if="frozen"
                class="sticky top-0 z-30 flex items-center justify-between gap-3 rounded border border-amber-500/60 bg-amber-950/80 px-3 py-2 text-sm text-amber-200 backdrop-blur"
                role="status"
            >
                <span>
                    ❄ Frozen<span v-if="frozenAt">
                        since {{ frozenAt.toLocaleTimeString() }}</span
                    >
                    — prices are paused. You can still star and set budgets (You will be notified here when the budget is exceeded by other bidders).
                </span>
                <button
                    class="shrink-0 rounded border border-amber-400 px-2.5 py-1 text-xs hover:bg-amber-500/20"
                    @click="setFrozen(false)"
                >
                    Unfreeze
                </button>
            </div>

            <p
                v-if="error"
                class="rounded border border-rose-800 bg-rose-950/40 px-3 py-2 text-sm text-rose-300"
            >
                {{ error }}
            </p>

            <!-- System status -->
            <section
                class="grid grid-cols-2 gap-2 sm:grid-cols-3 sm:gap-3 md:grid-cols-5"
            >
                <div class="rounded border p-2.5 sm:p-3">
                    <div class="text-xs uppercase tracking-wide text-muted-foreground">
                        Live auctions
                    </div>
                    <div class="font-mono text-lg">
                        {{ state.health.total_open }}
                    </div>
                </div>
                <div class="rounded border p-2.5 sm:p-3">
                    <div class="text-xs uppercase tracking-wide text-muted-foreground">
                        Haven't updated
                    </div>
                    <div
                        class="font-mono text-lg"
                        :class="healthClass(state.health.stale_polls)"
                    >
                        {{ state.health.stale_polls }}
                    </div>
                </div>
                <div class="rounded border p-2.5 sm:p-3">
                    <div class="text-xs uppercase tracking-wide text-muted-foreground">
                        Had a problem
                    </div>
                    <div
                        class="font-mono text-lg"
                        :class="healthClass(state.health.errored)"
                    >
                        {{ state.health.errored }}
                    </div>
                </div>
                <div class="rounded border p-2.5 sm:p-3">
                    <div class="text-xs uppercase tracking-wide text-muted-foreground">
                        In progress
                    </div>
                    <div class="font-mono text-lg">
                        {{ state.health.queue_depth }}
                    </div>
                </div>
                <div class="rounded border p-2.5 sm:p-3">
                    <div class="text-xs uppercase tracking-wide text-muted-foreground">
                        Time sync
                    </div>
                    <div class="font-mono text-lg">
                        {{ state.health.clock_offset }}s
                    </div>
                </div>
            </section>

            <!-- Two-column -->
            <div class="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_340px]">
                <!-- Roster -->
                <section class="min-w-0 lg:min-h-[520px]">
                    <div class="mb-2 flex flex-col gap-2">
                        <div class="flex items-center justify-between gap-2">
                            <h2
                                class="text-sm uppercase tracking-wide text-muted-foreground"
                            >
                                Auctions
                            </h2>
                            <button
                                class="min-h-8 rounded border px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground"
                                @click="setShowDetails(!showDetails)"
                            >
                                {{ showDetails ? "Hide details" : "Show details" }}
                            </button>
                        </div>

                        <div class="flex flex-wrap items-center gap-2">
                            <div class="flex gap-1 rounded border p-0.5">
                                <button
                                    v-for="f in filterKeys"
                                    :key="f"
                                    class="min-h-8 rounded px-2.5 py-0.5 text-xs"
                                    :class="
                                        filter === f
                                            ? 'bg-sky-600 text-white'
                                            : 'text-muted-foreground hover:text-foreground'
                                    "
                                    @click="setFilter(f)"
                                >
                                    {{ filterLabels[f] }}
                                </button>
                            </div>

                            <!-- Sort control (mobile only; desktop sorts via table headers) -->
                            <div class="flex items-center gap-1 md:hidden">
                                <label class="sr-only" for="sort-select">
                                    Sort by
                                </label>
                                <select
                                    id="sort-select"
                                    class="min-h-8 rounded border bg-background px-2 py-1 text-base sm:text-xs"
                                    :value="sortKey"
                                    @change="
                                        setSort(
                                            ($event.target as HTMLSelectElement)
                                                .value as SortKey,
                                        )
                                    "
                                >
                                    <option
                                        v-for="(label, k) in sortLabels"
                                        :key="k"
                                        :value="k"
                                    >
                                        {{ label }}
                                    </option>
                                </select>
                                <button
                                    class="min-h-8 rounded border px-2 py-1 text-xs"
                                    :aria-label="
                                        sortDir === 'asc'
                                            ? 'Ascending'
                                            : 'Descending'
                                    "
                                    @click="toggleSortDir"
                                >
                                    {{ sortDir === "asc" ? "↑" : "↓" }}
                                </button>
                            </div>
                        </div>
                    </div>

                    <!-- Mobile: card list -->
                    <div class="space-y-2 md:hidden">
                        <div
                            v-for="v in rows"
                            :key="v.id"
                            class="rounded border p-3"
                            :class="{ 'bg-amber-950/20': v.is_quiet }"
                        >
                            <div class="flex items-start justify-between gap-2">
                                <div class="flex min-w-0 items-start gap-2">
                                    <button
                                        class="-ml-1 flex h-9 w-9 shrink-0 items-center justify-center text-xl leading-none"
                                        :class="
                                            v.watched
                                                ? 'text-amber-400'
                                                : 'text-muted-foreground/40'
                                        "
                                        :aria-label="
                                            v.watched
                                                ? 'Remove star'
                                                : 'Star this auction'
                                        "
                                        :aria-pressed="!!v.watched"
                                        @click="toggleWatch(v)"
                                    >
                                        ★
                                    </button>
                                    <Link
                                        :href="`/vehicles/${v.wp_id}`"
                                        class="min-w-0 pt-1.5 text-sm font-medium leading-snug hover:text-sky-400"
                                    >
                                        {{ v.name }}
                                    </Link>
                                </div>
                                <Link
                                    :href="`/vehicles/${v.wp_id}`"
                                    class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded border border-sky-400 text-sky-400 hover:bg-muted"
                                    aria-label="Open auction"
                                >
                                    →
                                </Link>
                            </div>

                            <div class="mt-2 flex items-end justify-between gap-3">
                                <div>
                                    <div class="font-mono text-lg leading-none">
                                        {{ fmt(v.current_price) }}
                                    </div>
                                    <div
                                        class="mt-1 font-mono text-xs text-muted-foreground"
                                    >
                                        {{ deltaText(v.delta_10m) }} · 10 min
                                    </div>
                                </div>
                                <svg
                                    v-if="v.sparkline.length > 1"
                                    viewBox="0 0 80 24"
                                    preserveAspectRatio="none"
                                    class="h-6 w-20 shrink-0 text-sky-400"
                                >
                                    <path
                                        :d="sparkPath(v.sparkline)"
                                        fill="none"
                                        stroke="currentColor"
                                        stroke-width="1.5"
                                    />
                                </svg>
                                <div class="text-right">
                                    <div
                                        class="text-[10px] uppercase tracking-wide text-muted-foreground"
                                    >
                                        Closes in
                                    </div>
                                    <div class="font-mono text-sm">
                                        {{ timeLeft(v.seconds_left) }}
                                    </div>
                                </div>
                            </div>

                            <div
                                v-if="showDetails"
                                class="mt-2 grid grid-cols-2 gap-2 border-t pt-2 text-xs"
                            >
                                <div>
                                    <div class="text-muted-foreground">
                                        Moving at
                                    </div>
                                    <div
                                        class="font-mono"
                                        :class="velocityClass(v.velocity_15m)"
                                    >
                                        {{ velocityText(v.velocity_15m) }}
                                    </div>
                                </div>
                                <div>
                                    <div class="text-muted-foreground">
                                        Likely to end
                                    </div>
                                    <div class="font-mono">
                                        {{ fmt(v.projected_close) }}
                                    </div>
                                </div>
                            </div>

                            <div class="mt-2 border-t pt-2">
                                <button
                                    data-budget-badge
                                    class="min-h-8 w-full text-left text-sm"
                                    :class="budgetBadgeClass(budgetStatus(v))"
                                    @click.stop="openBudgetPopover(v, $event)"
                                >
                                    {{ budgetLabel(v) }}
                                </button>
                            </div>
                        </div>

                        <div
                            v-if="!rows.length"
                            class="rounded border p-6 text-center text-sm text-muted-foreground"
                        >
                            {{
                                filter === "all"
                                    ? "Nothing here yet — hit Refresh to load the auctions."
                                    : "No auctions match this filter."
                            }}
                        </div>
                    </div>

                    <!-- Desktop / tablet: table -->
                    <div class="hidden overflow-x-auto rounded border md:block">
                        <table class="w-full min-w-[760px] text-sm">
                            <thead class="bg-muted text-muted-foreground">
                                <tr>
                                    <th v-if="showDetails" class="w-8 p-2"></th>
                                    <th class="p-2 text-left">Vehicle</th>
                                    <th
                                        class="cursor-pointer p-2 text-right hover:text-foreground"
                                        @click="setSort('price')"
                                    >
                                        Current{{ sortArrow("price") }}
                                    </th>
                                    <th class="p-2 text-right">Chart</th>
                                    <th
                                        v-if="showDetails"
                                        class="cursor-pointer p-2 text-right hover:text-foreground"
                                        @click="setSort('velocity')"
                                    >
                                        Moving at{{ sortArrow("velocity") }}
                                    </th>
                                    <th
                                        class="cursor-pointer p-2 text-right hover:text-foreground"
                                        @click="setSort('delta')"
                                    >
                                        Last 10 min{{ sortArrow("delta") }}
                                    </th>
                                    <th v-if="showDetails" class="p-2 text-right">
                                        Likely to end
                                    </th>
                                    <th
                                        class="cursor-pointer p-2 text-right hover:text-foreground"
                                        @click="setSort('finish')"
                                    >
                                        Closes in{{ sortArrow("finish") }}
                                    </th>
                                    <th
                                        class="cursor-pointer p-2 text-right hover:text-foreground"
                                        @click="setSort('headroom')"
                                    >
                                        Budget{{ sortArrow("headroom") }}
                                    </th>
                                    <th class="p-2 text-right"></th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr
                                    v-for="v in rows"
                                    :key="v.id"
                                    class="border-t hover:bg-muted/40"
                                    :class="{ 'bg-amber-950/20': v.is_quiet }"
                                >
                                    <td v-if="showDetails" class="p-2 text-center">
                                        <button
                                            class="text-lg leading-none"
                                            :class="
                                                v.watched
                                                    ? 'text-amber-400'
                                                    : 'text-muted-foreground/40'
                                            "
                                            :title="
                                                v.watched
                                                    ? 'Remove star'
                                                    : 'Star this auction'
                                            "
                                            @click="toggleWatch(v)"
                                        >
                                            ★
                                        </button>
                                    </td>
                                    <td class="p-2">
                                        <Link
                                            :href="`/vehicles/${v.wp_id}`"
                                            class="hover:text-sky-400"
                                        >
                                            {{ v.name }}
                                        </Link>
                                    </td>
                                    <td class="p-2 text-right font-mono">
                                        {{ fmt(v.current_price) }}
                                    </td>
                                    <td class="p-2">
                                        <svg
                                            v-if="v.sparkline.length > 1"
                                            viewBox="0 0 80 24"
                                            preserveAspectRatio="none"
                                            class="h-6 w-20 text-sky-400"
                                        >
                                            <path
                                                :d="sparkPath(v.sparkline)"
                                                fill="none"
                                                stroke="currentColor"
                                                stroke-width="1.5"
                                            />
                                        </svg>
                                    </td>
                                    <td
                                        v-if="showDetails"
                                        class="p-2 text-right font-mono"
                                        :class="velocityClass(v.velocity_15m)"
                                    >
                                        {{ velocityText(v.velocity_15m) }}
                                    </td>
                                    <td
                                        class="p-2 text-right font-mono text-muted-foreground"
                                    >
                                        {{ deltaText(v.delta_10m) }}
                                    </td>
                                    <td
                                        v-if="showDetails"
                                        class="p-2 text-right font-mono text-muted-foreground"
                                    >
                                        {{ fmt(v.projected_close) }}
                                    </td>
                                    <td class="p-2 text-right font-mono">
                                        {{ timeLeft(v.seconds_left) }}
                                    </td>
                                    <td class="p-2 text-right">
                                        <button
                                            data-budget-badge
                                            class="text-xs"
                                            :class="budgetBadgeClass(budgetStatus(v))"
                                            @click.stop="openBudgetPopover(v, $event)"
                                        >
                                            {{ budgetLabel(v) }}
                                        </button>
                                    </td>
                                    <td class="p-2 text-right">
                                        <Link
                                            :href="`/vehicles/${v.wp_id}`"
                                            class="rounded border border-sky-400 px-2 py-0.5 text-sm text-sky-400 hover:bg-muted hover:text-sky-300"
                                        >
                                            →
                                        </Link>
                                    </td>
                                </tr>
                                <tr v-if="!rows.length">
                                    <td
                                        :colspan="showDetails ? 10 : 7"
                                        class="p-6 text-center text-muted-foreground"
                                    >
                                        {{
                                            filter === "all"
                                                ? "Nothing here yet — hit Refresh to load the auctions."
                                                : "No auctions match this filter."
                                        }}
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </section>

                <!-- Event feed -->
                <aside class="min-w-0 lg:relative">
                    <div
                        class="flex flex-col lg:absolute lg:inset-x-0 lg:top-0 lg:bottom-12"
                    >
                        <h2
                            class="mb-2 shrink-0 text-sm uppercase tracking-wide text-muted-foreground"
                        >
                            What's happening
                        </h2>

                        <div
                            class="nice-scrollbar max-h-80 overflow-y-auto rounded border lg:max-h-none lg:min-h-0 lg:flex-1"
                        >
                            <ul v-if="state.events.length" class="divide-y">
                                <li
                                    v-for="(e, i) in state.events"
                                    :key="i"
                                    class="p-3 text-sm"
                                >
                                    <div
                                        class="flex items-baseline justify-between gap-2"
                                    >
                                        <span
                                            class="font-mono text-xs uppercase tracking-wider"
                                            :class="severityClass(e.severity)"
                                        >
                                            {{ eventTypeLabel(e.type) }}
                                        </span>
                                        <span class="text-xs text-muted-foreground">
                                            {{ new Date(e.at).toLocaleTimeString() }}
                                        </span>
                                    </div>
                                    <div class="mt-1 text-foreground/90">
                                        {{ e.message }}
                                    </div>
                                </li>
                            </ul>
                            <div
                                v-else
                                class="p-6 text-center text-xs text-muted-foreground"
                            >
                                Nothing has happened yet.
                            </div>
                        </div>
                    </div>
                </aside>
            </div>
        </div>

        <!-- Budget popover (desktop) / bottom sheet (mobile) -->
        <Teleport to="body">
            <div
                v-if="popover && popover.sheet"
                class="fixed inset-0 z-40 bg-black/50"
                @click.stop="closePopover"
            ></div>

            <div
                v-if="popover"
                data-budget-popover
                class="fixed z-50 border bg-background shadow-xl"
                :class="
                    popover.sheet
                        ? 'inset-x-0 bottom-0 rounded-t-xl p-4 pb-[max(1rem,env(safe-area-inset-bottom))]'
                        : 'w-60 rounded p-3'
                "
                :style="
                    popover.sheet
                        ? undefined
                        : { top: popover.y + 'px', left: popover.x + 'px' }
                "
                @click.stop
            >
                <div
                    class="mb-2 text-xs uppercase tracking-wide text-muted-foreground"
                >
                    My max budget
                    <span
                        v-if="popover.sheet"
                        class="mt-0.5 block normal-case tracking-normal text-foreground/80"
                    >
                        {{ popover.vehicle.name }}
                    </span>
                </div>
                <!-- text-base (16px) stops iOS from zooming the page on focus -->
                <input
                    ref="popoverInput"
                    v-model="popover.draft"
                    type="text"
                    inputmode="numeric"
                    autocomplete="off"
                    placeholder="e.g. 2000000"
                    class="w-full rounded border bg-background px-2 py-1.5 text-right font-mono text-base sm:py-1 sm:text-sm"
                    @focus="popoverInputFocused = true"
                    @blur="popoverInputFocused = false"
                    @keydown.enter="saveBudget"
                    @keydown.escape="closePopover"
                />
                <div class="mt-3 flex items-center justify-between gap-2">
                    <button
                        class="min-h-9 rounded bg-sky-600 px-4 py-1 text-sm text-white hover:bg-sky-500 sm:min-h-0 sm:px-2.5 sm:text-xs"
                        @click="saveBudget"
                    >
                        Save
                    </button>
                    <button
                        v-if="popover.vehicle.watch_price"
                        class="min-h-9 px-2 text-sm text-rose-400 hover:underline sm:min-h-0 sm:text-xs"
                        @click="clearBudget"
                    >
                        clear
                    </button>
                    <button
                        v-if="popover.sheet"
                        class="min-h-9 px-2 text-sm text-muted-foreground"
                        @click="closePopover"
                    >
                        Cancel
                    </button>
                </div>
            </div>
        </Teleport>

        <!-- Budget alert toasts -->
        <BudgetAlertToasts
            :alerts="alerts"
            @dismiss="dismiss"
            @dismiss-all="dismissAll"
        />
    </div>
</template>

<style scoped>
.nice-scrollbar {
    scrollbar-width: thin;
    scrollbar-color: rgba(148, 163, 184, 0.35) transparent;
}

.nice-scrollbar::-webkit-scrollbar {
    width: 8px;
}

.nice-scrollbar::-webkit-scrollbar-track {
    background: transparent;
}

.nice-scrollbar::-webkit-scrollbar-thumb {
    background-color: rgba(148, 163, 184, 0.35);
    border-radius: 9999px;
    border: 2px solid transparent;
    background-clip: padding-box;
}

.nice-scrollbar::-webkit-scrollbar-thumb:hover {
    background-color: rgba(148, 163, 184, 0.6);
    background-clip: padding-box;
}
</style>