<script setup lang="ts">
import { Link } from "@inertiajs/vue3";
import { onBeforeUnmount, onMounted, ref } from "vue";
import type { BudgetAlert } from "@/composables/useBudgetAlerts";

defineProps<{
    alerts: BudgetAlert[];
}>();

const emit = defineEmits<{
    dismiss: [id: string];
    dismissAll: [];
}>();

const now = ref(Date.now());
let tick: number | null = null;

onMounted(() => {
    tick = window.setInterval(() => {
        now.value = Date.now();
    }, 10_000);
});

onBeforeUnmount(() => {
    if (tick) window.clearInterval(tick);
});

function fmt(n: number): string {
    return n.toLocaleString();
}

function relative(iso: string): string {
    const then = new Date(iso).getTime();
    const diffSec = Math.max(0, Math.floor((now.value - then) / 1000));
    if (diffSec < 60) return `${diffSec}s ago`;
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
    return `${Math.floor(diffSec / 3600)}h ago`;
}
</script>

<template>
    <Teleport to="body">
        <div
            class="pointer-events-none fixed right-4 top-4 z-[60] flex w-full max-w-sm flex-col gap-2"
            aria-live="polite"
            aria-atomic="false"
        >
            <div
                v-for="alert in alerts"
                :key="alert.id"
                class="pointer-events-auto rounded-lg border border-rose-700 bg-rose-950/95 shadow-2xl ring-1 ring-rose-900/50 backdrop-blur"
                role="alert"
            >
                <div class="flex items-start gap-3 p-3">
                    <div class="text-2xl leading-none">⚠</div>
                    <div class="min-w-0 flex-1">
                        <div
                            class="text-sm font-semibold tracking-tight text-rose-100"
                        >
                            Over budget
                        </div>
                        <div class="mt-0.5 truncate text-sm text-rose-200/90">
                            {{ alert.name }}
                        </div>
                        <div class="mt-1 font-mono text-xs text-rose-300">
                            {{ fmt(alert.current_price) }}
                            <span class="text-rose-500">/</span>
                            budget {{ fmt(alert.watch_price) }}
                            <span class="ml-1 text-rose-400">
                                (+{{ fmt(alert.over_by) }})
                            </span>
                        </div>
                        <div class="mt-2 flex items-center gap-2">
                            <Link
                                :href="`/vehicles/${alert.wp_id}`"
                                class="rounded border border-rose-600 px-2 py-0.5 text-xs text-rose-200 hover:bg-rose-900/50"
                            >
                                View →
                            </Link>
                            <span class="text-xs text-rose-400/70">
                                {{ relative(alert.alerted_at) }}
                            </span>
                        </div>
                    </div>
                    <button
                        class="shrink-0 rounded p-1 text-rose-300 hover:bg-rose-900/50 hover:text-rose-100"
                        title="Dismiss"
                        aria-label="Dismiss"
                        @click="emit('dismiss', alert.id)"
                    >
                        <svg
                            xmlns="http://www.w3.org/2000/svg"
                            viewBox="0 0 20 20"
                            fill="currentColor"
                            class="h-4 w-4"
                        >
                            <path
                                fill-rule="evenodd"
                                d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                                clip-rule="evenodd"
                            />
                        </svg>
                    </button>
                </div>
            </div>
            <button
                v-if="alerts.length > 1"
                class="pointer-events-auto self-end rounded border border-rose-700 bg-rose-900/80 px-3 py-1.5 text-xs text-rose-200 hover:bg-rose-800/80"
                @click="emit('dismissAll')"
            >
                Dismiss all ({{ alerts.length }})
            </button>
        </div>
    </Teleport>
</template>
