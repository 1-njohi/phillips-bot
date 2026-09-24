import { computed, ref, watch } from "vue";
import type { Ref } from "vue";
import type { VehicleRow } from "@/types/auction";

export interface BudgetAlert {
    id: string;
    wp_id: number;
    name: string;
    current_price: number;
    watch_price: number;
    over_by: number;
    alerted_at: string;
}

const DISMISSED_KEY = "auction.dismissedBudgetAlerts";
const SOUND_KEY = "auction.budgetSoundEnabled";

/* ---------- Sound (Web Audio, no asset file) ---------- */

let audioCtx: AudioContext | null = null;
let audioResumeArmed = false;

function getAudioContext(): AudioContext | null {
    if (typeof window === "undefined") return null;
    const Ctor =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext?: typeof AudioContext })
            .webkitAudioContext;
    if (!Ctor) return null;
    if (!audioCtx) audioCtx = new Ctor();
    return audioCtx;
}

/**
 * Browser autoplay policy blocks audio until the user interacts with the
 * page. Arm a one-time listener so the context is ready the first time
 * they click anywhere.
 */
function armAudioResume() {
    if (audioResumeArmed || typeof window === "undefined") return;
    audioResumeArmed = true;

    const resume = () => {
        const ctx = getAudioContext();
        if (ctx && ctx.state === "suspended") {
            ctx.resume().catch(() => {});
        }
        document.removeEventListener("click", resume);
        document.removeEventListener("keydown", resume);
    };

    document.addEventListener("click", resume);
    document.addEventListener("keydown", resume);
}

function playAlertChime() {
    const ctx = getAudioContext();
    if (!ctx || ctx.state !== "running") return;

    const now = ctx.currentTime;
    const notes = [660, 880, 1100];

    notes.forEach((freq, i) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain);
        gain.connect(ctx.destination);

        osc.type = "sine";
        osc.frequency.value = freq;

        const start = now + i * 0.12;
        gain.gain.setValueAtTime(0, start);
        gain.gain.linearRampToValueAtTime(0.14, start + 0.02);
        gain.gain.linearRampToValueAtTime(0, start + 0.1);

        osc.start(start);
        osc.stop(start + 0.15);
    });
}

/* ---------- Persistence ---------- */

function loadDismissed(): Set<string> {
    if (typeof window === "undefined") return new Set();
    try {
        const raw = window.localStorage.getItem(DISMISSED_KEY);
        if (!raw) return new Set();
        const arr = JSON.parse(raw) as string[];
        // Cap to most recent 500 to keep storage bounded.
        return new Set(arr.slice(-500));
    } catch {
        return new Set();
    }
}

function saveDismissed(set: Set<string>) {
    if (typeof window === "undefined") return;
    const arr = Array.from(set).slice(-500);
    try {
        window.localStorage.setItem(DISMISSED_KEY, JSON.stringify(arr));
    } catch {
        /* storage full; ignore */
    }
}

function loadSoundPref(): boolean {
    if (typeof window === "undefined") return true;
    return window.localStorage.getItem(SOUND_KEY) !== "0";
}

/* ---------- The composable ---------- */

export function useBudgetAlerts(vehicles: Ref<VehicleRow[]>) {
    armAudioResume();

    const dismissed = ref<Set<string>>(loadDismissed());
    const seenThisSession = ref<Set<string>>(new Set());
    const alerts = ref<BudgetAlert[]>([]);
    const soundEnabled = ref(loadSoundPref());

    /**
     * Stable fingerprint for a specific "crossing event" of a specific vehicle.
     * Changes only when the server re-arms the vehicle (new budget, or price
     * dropped back under and crossed again).
     */
    function fingerprint(v: VehicleRow): string | null {
        if (!v.budget_alerted_at) return null;
        if (v.watch_price == null) return null;
        return `${v.wp_id}:${v.budget_alerted_at}`;
    }

    const fingerprintKey = computed(() =>
        vehicles.value.map(fingerprint).filter(Boolean).join("|")
    );

    function sync() {
        const next: BudgetAlert[] = [];
        let firedNew = false;

        for (const v of vehicles.value) {
            const fp = fingerprint(v);
            if (!fp) continue;
            if (dismissed.value.has(fp)) continue;

            next.push({
                id: fp,
                wp_id: v.wp_id,
                name: v.name,
                current_price: v.current_price ?? 0,
                watch_price: v.watch_price ?? 0,
                over_by: Math.max(
                    0,
                    (v.current_price ?? 0) - (v.watch_price ?? 0)
                ),
                alerted_at: v.budget_alerted_at!,
            });

            if (!seenThisSession.value.has(fp)) {
                seenThisSession.value.add(fp);
                firedNew = true;
            }
        }

        alerts.value = next;

        if (firedNew && soundEnabled.value) {
            playAlertChime();
        }
    }

    watch(fingerprintKey, sync, { immediate: true });

    function dismiss(id: string) {
        dismissed.value.add(id);
        saveDismissed(dismissed.value);
        alerts.value = alerts.value.filter((a) => a.id !== id);
    }

    function dismissAll() {
        for (const a of alerts.value) {
            dismissed.value.add(a.id);
        }
        saveDismissed(dismissed.value);
        alerts.value = [];
    }

    function setSoundEnabled(on: boolean) {
        soundEnabled.value = on;
        if (typeof window !== "undefined") {
            window.localStorage.setItem(SOUND_KEY, on ? "1" : "0");
        }
        if (on) {
            // Feedback so the user knows it worked.
            const ctx = getAudioContext();
            if (ctx && ctx.state === "suspended") ctx.resume().catch(() => {});
            setTimeout(playAlertChime, 50);
        }
    }

    return {
        alerts,
        dismiss,
        dismissAll,
        soundEnabled,
        setSoundEnabled,
    };
}
