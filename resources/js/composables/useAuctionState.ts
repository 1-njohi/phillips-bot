import { onBeforeUnmount, onMounted, ref } from "vue";
import type { DashboardState } from "@/types/auction";

export function useAuctionState() {
    const state = ref<DashboardState>({
        vehicles: [],
        events: [],
        health: {
            total_open: 0,
            stale_polls: 0,
            errored: 0,
            queue_depth: 0,
            clock_offset: 0,
        },
        clock_offset: 0,
        server_time: "",
    });
    const loading = ref(false);
    const error = ref<string | null>(null);
    let timer: number | null = null;

    async function tick() {
        try {
            const res = await fetch("/api/state", {
                credentials: "same-origin",
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            state.value = await res.json();
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
        const hot = state.value.vehicles.some(
            (v) => v.seconds_left !== null && v.seconds_left < 300
        );
        timer = window.setTimeout(tick, hot ? 2000 : 5000);
    }

    async function refreshRoster() {
        await fetch("/api/roster/refresh", {
            method: "POST",
            headers: { "X-CSRF-TOKEN": csrf() },
            credentials: "same-origin",
        });
        await tick();
    }

    function csrf(): string {
        const m = document.querySelector('meta[name="csrf-token"]');
        return m ? (m as HTMLMetaElement).content : "";
    }

    async function toggleWatch(v: { id: number }) {
        await fetch(`/api/vehicles/${v.id}/watch`, {
            method: "PATCH",
            headers: { "X-CSRF-TOKEN": csrf() },
            credentials: "same-origin",
        });
        await tick();
    }

    async function setWatchPrice(v: { id: number }, price: number | null) {
        await fetch(`/api/vehicles/${v.id}/watch-price`, {
            method: "PATCH",
            headers: {
                "Content-Type": "application/json",
                "X-CSRF-TOKEN": csrf(),
            },
            credentials: "same-origin",
            body: JSON.stringify({ watch_price: price }),
        });
        await tick();
    }

    onMounted(() => {
        loading.value = true;
        tick();
    });

    onBeforeUnmount(() => {
        if (timer) window.clearTimeout(timer);
    });

    return {
        state,
        loading,
        error,
        refreshRoster,
        toggleWatch,
        setWatchPrice,
    };
}