<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { Head, Link } from "@inertiajs/vue3";

interface HealthSnapshot {
    total_open: number;
    stale_polls: number;
    errored: number;
    queue_depth: number;
    clock_offset: number;
    checked_at: string;
}

const health = ref<HealthSnapshot | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);
let timer: number | null = null;

async function load() {
    loading.value = true;
    try {
        const r = await fetch("/api/admin/health", {
            credentials: "same-origin",
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        health.value = await r.json();
        error.value = null;
    } catch (e: any) {
        error.value = e?.message ?? "unknown";
    } finally {
        loading.value = false;
    }
}

function schedule() {
    if (timer) window.clearTimeout(timer);
    timer = window.setTimeout(async () => {
        await load();
        schedule();
    }, 5000);
}

onMounted(async () => {
    await load();
    schedule();
});

onBeforeUnmount(() => {
    if (timer) window.clearTimeout(timer);
});

function healthClass(n: number): string {
    return n > 0 ? "text-amber-400" : "text-muted-foreground";
}

function fmtTime(iso: string | undefined): string {
    if (!iso) return "—";
    return new Date(iso).toLocaleTimeString();
}
</script>

<template>
    <Head title="System Health" />

    <div class="mx-auto max-w-5xl space-y-5 p-6">
        <header class="flex items-center justify-between">
            <div>
                <Link
                    href="/"
                    class="text-xs text-muted-foreground hover:text-foreground"
                >
                    ← Back to dashboard
                </Link>
                <h1 class="mt-1 text-2xl font-semibold tracking-tight">
                    System Health
                </h1>
                <p class="text-xs text-muted-foreground">
                    Poller, queue, and time-sync status
                </p>
            </div>
            <div class="flex items-center gap-3">
                <span class="text-xs text-muted-foreground">
                    Checked {{ fmtTime(health?.checked_at) }}
                </span>
                <button
                    class="rounded border px-3 py-1.5 text-sm hover:bg-muted"
                    :disabled="loading"
                    @click="load"
                >
                    {{ loading ? "Checking…" : "Refresh" }}
                </button>
            </div>
        </header>

        <p
            v-if="error"
            class="rounded border border-rose-800 bg-rose-950/40 px-3 py-2 text-sm text-rose-300"
        >
            {{ error }}
        </p>

        <section v-if="health" class="grid grid-cols-2 gap-3 md:grid-cols-5">
            <div class="rounded border p-3">
                <div
                    class="text-xs uppercase tracking-wide text-muted-foreground"
                >
                    Live auctions
                </div>
                <div class="font-mono text-lg">{{ health.total_open }}</div>
            </div>
            <div class="rounded border p-3">
                <div
                    class="text-xs uppercase tracking-wide text-muted-foreground"
                >
                    Haven't updated
                </div>
                <div
                    class="font-mono text-lg"
                    :class="healthClass(health.stale_polls)"
                >
                    {{ health.stale_polls }}
                </div>
            </div>
            <div class="rounded border p-3">
                <div
                    class="text-xs uppercase tracking-wide text-muted-foreground"
                >
                    Had a problem
                </div>
                <div
                    class="font-mono text-lg"
                    :class="healthClass(health.errored)"
                >
                    {{ health.errored }}
                </div>
            </div>
            <div class="rounded border p-3">
                <div
                    class="text-xs uppercase tracking-wide text-muted-foreground"
                >
                    In progress
                </div>
                <div class="font-mono text-lg">{{ health.queue_depth }}</div>
            </div>
            <div class="rounded border p-3">
                <div
                    class="text-xs uppercase tracking-wide text-muted-foreground"
                >
                    Time sync
                </div>
                <div class="font-mono text-lg">{{ health.clock_offset }}s</div>
            </div>
        </section>

        <div
            v-else-if="loading"
            class="rounded border p-6 text-center text-sm text-muted-foreground"
        >
            Loading…
        </div>
    </div>
</template>
