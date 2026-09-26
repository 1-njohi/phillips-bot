<script setup lang="ts">
import { Head, router, useForm } from "@inertiajs/vue3";
import { ref } from "vue";

interface Account {
    id: number;
    label: string;
    status: "unverified" | "valid" | "invalid" | "expired";
    has_cookie: boolean;
    has_nonce: boolean;
    cookie_captured_at: string | null;
    deposit_paid: boolean;
    live_arms_count: number;
    arms_limit: number;
}

interface ArmedBid {
    id: number;
    max_amount: number;
    status: string;
    live_status: string;
    last_bid: number | null;
    bid_count: number;
    account: { id: number; label: string; status: string };
    vehicle: {
        id: number;
        wp_id: number;
        name: string;
        current_price: number | null;
        finish_time: string | null;
    };
}

interface UnarmedVehicle {
    id: number;
    wp_id: number;
    name: string;
    current_price: number | null;
    finish_time: string | null;
}

const props = defineProps<{
    accounts: Account[];
    armedBids: ArmedBid[];
    unarmedVehicles: UnarmedVehicle[];
    increment: number;
}>();

/* ─── Account modal ─────────────────────────────────────────── */

const accountModal = ref<{ open: boolean; editId: number | null }>({
    open: false,
    editId: null,
});

const accountForm = useForm({
    label: "",
    cookie: "",
    nonce: "",
});

function openNewAccount() {
    accountForm.reset();
    accountForm.clearErrors();
    accountModal.value = { open: true, editId: null };
}

function openEditAccount(a: Account) {
    accountForm.reset();
    accountForm.clearErrors();
    accountForm.label = a.label;
    accountForm.cookie = ""; // never pre-fill secrets
    accountForm.nonce = "";
    accountModal.value = { open: true, editId: a.id };
}

function closeAccountModal() {
    accountModal.value = { open: false, editId: null };
}

function submitAccount() {
    const id = accountModal.value.editId;
    if (id) {
        accountForm.patch(`/accounts/${id}`, {
            preserveScroll: true,
            onSuccess: closeAccountModal,
        });
    } else {
        accountForm.post("/accounts", {
            preserveScroll: true,
            onSuccess: closeAccountModal,
        });
    }
}

function markAccountStatus(a: Account, status: Account["status"]) {
    router.patch(
        `/accounts/${a.id}`,
        { status },
        { preserveScroll: true }
    );
}

function toggleDeposit(a: Account) {
    router.patch(
        `/accounts/${a.id}`,
        { deposit_paid: !a.deposit_paid },
        { preserveScroll: true }
    );
}

function deleteAccount(a: Account) {
    if (!confirm(`Delete "${a.label}"? This cannot be undone.`)) return;
    router.delete(`/accounts/${a.id}`, { preserveScroll: true });
}

/* ─── Arm modal ─────────────────────────────────────────────── */

const armModal = ref(false);
const armForm = useForm({
    account_id: null as number | null,
    vehicle_id: null as number | null,
    max_amount: "" as string | number,
});

function openArmModal() {
    armForm.reset();
    armForm.clearErrors();
    armModal.value = true;
}

function closeArmModal() {
    armModal.value = false;
}

function submitArm() {
    armForm
        .transform((d) => ({
            ...d,
            max_amount: Number(d.max_amount),
        }))
        .post("/arming", {
            preserveScroll: true,
            onSuccess: closeArmModal,
        });
}

/* ─── Inline max edit ───────────────────────────────────────── */

const editingMax = ref<{ id: number | null; draft: string }>({
    id: null,
    draft: "",
});

function startEditMax(ab: ArmedBid) {
    editingMax.value = { id: ab.id, draft: String(ab.max_amount) };
}

function cancelEditMax() {
    editingMax.value = { id: null, draft: "" };
}

function saveMax(ab: ArmedBid) {
    const n = Number(editingMax.value.draft.replace(/[^\d]/g, ""));
    if (!n || n === ab.max_amount) {
        cancelEditMax();
        return;
    }
    router.patch(
        `/arming/${ab.id}`,
        { max_amount: n },
        {
            preserveScroll: true,
            onSuccess: cancelEditMax,
        }
    );
}

function disarm(ab: ArmedBid) {
    if (!confirm(`Disarm ${ab.vehicle.name}?`)) return;
    router.delete(`/arming/${ab.id}`, { preserveScroll: true });
}

/* ─── Formatting ────────────────────────────────────────────── */

function fmt(n: number | null | undefined): string {
    return n == null ? "—" : Number(n).toLocaleString();
}

function timeLeft(iso: string | null): string {
    if (!iso) return "—";
    const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / -1000));
    if (s <= 0) return "closed";
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return `${h}h ${m}m`;
}

function statusColor(s: string): string {
    return {
        valid: "text-emerald-400",
        unverified: "text-amber-400",
        invalid: "text-rose-400",
        expired: "text-rose-400",
    }[s] ?? "text-muted-foreground";
}

function liveColor(s: string): string {
    return {
        winning: "text-emerald-400",
        outbid: "text-amber-400",
        priced_out: "text-rose-400",
        not_bidding: "text-muted-foreground",
        unknown: "text-muted-foreground",
    }[s] ?? "text-muted-foreground";
}

function accountAvailableForArm(a: Account): boolean {
    return a.status === "valid" && a.live_arms_count < a.arms_limit;
}
const validating = ref<number | null>(null);

function validateAccount(a: Account) {
    validating.value = a.id;
    router.post(`/accounts/${a.id}/validate`, {}, {
        preserveScroll: true,
        onFinish: () => {
            setTimeout(() => { validating.value = null; }, 3000);
        },
    });
}
</script>

<template>
    <Head title="Arming" />

    <div class="mx-auto max-w-[1200px] space-y-6 p-6">
        <header class="flex items-center justify-between">
            <div>
                <h1 class="text-2xl font-semibold tracking-tight">Arming</h1>
                <p class="text-xs text-muted-foreground">
                    Configure accounts and set a max bid per vehicle.
                </p>
            </div>
            <div class="flex items-center gap-2">
                <button
                    class="rounded border px-3 py-1.5 text-sm hover:bg-muted"
                    @click="openNewAccount"
                >
                    + Account
                </button>
                <button
                    class="rounded bg-sky-600 px-3 py-1.5 text-sm text-white hover:bg-sky-500"
                    @click="openArmModal"
                >
                    + Arm vehicle
                </button>
            </div>
        </header>

        <!-- Accounts -->
        <section>
            <h2 class="mb-2 text-sm uppercase tracking-wide text-muted-foreground">
                Accounts
            </h2>
            <div class="overflow-hidden rounded border">
                <table class="w-full text-sm">
                    <thead class="bg-muted text-muted-foreground">
                        <tr>
                            <th class="p-2 text-left">Label</th>
                            <th class="p-2 text-left">Status</th>
                            <th class="p-2 text-left">Cookie</th>
                            <th class="p-2 text-center">Deposit</th>
                            <th class="p-2 text-center">Arms</th>
                            <th class="p-2 text-right">Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr
                            v-for="a in accounts"
                            :key="a.id"
                            class="border-t"
                        >
                            <td class="p-2">{{ a.label }}</td>
                            <td class="p-2" :class="statusColor(a.status)">
                                ● {{ a.status }}
                            </td>
                            <td class="p-2 text-muted-foreground">
                                <span v-if="!a.has_cookie">—</span>
                                <span v-else-if="!a.cookie_captured_at">set</span>
                                <span v-else>
                                    {{ new Date(a.cookie_captured_at).toLocaleString() }}
                                </span>
                            </td>
                            <td class="p-2 text-center">
                                <input
                                    type="checkbox"
                                    :checked="a.deposit_paid"
                                    @change="toggleDeposit(a)"
                                />
                            </td>
                            <td class="p-2 text-center font-mono">
                                {{ a.live_arms_count }}/{{ a.arms_limit }}
                            </td>
                            <td class="p-2 text-right">
                                <button
                                    class="mr-2 text-xs text-sky-400 hover:underline"
                                    :disabled="validating === a.id"
                                    @click="validateAccount(a)"
                                >
                                    {{ validating === a.id ? "validating…" : "validate" }}
                                </button>
                                <button
                                    v-if="a.status !== 'valid'"
                                    class="mr-2 text-xs text-emerald-400 hover:underline"
                                    @click="markAccountStatus(a, 'valid')"
                                >
                                    mark valid
                                </button>
                                <button
                                    v-if="a.status !== 'unverified'"
                                    class="mr-2 text-xs text-amber-400 hover:underline"
                                    @click="markAccountStatus(a, 'unverified')"
                                >
                                    unverify
                                </button>
                                <button
                                    class="mr-2 text-xs text-sky-400 hover:underline"
                                    @click="openEditAccount(a)"
                                >
                                    edit
                                </button>
                                <button
                                    class="text-xs text-rose-400 hover:underline"
                                    @click="deleteAccount(a)"
                                >
                                    delete
                                </button>
                            </td>
                        </tr>
                        <tr v-if="!accounts.length">
                            <td colspan="6" class="p-6 text-center text-muted-foreground">
                                No accounts yet. Add one to get started.
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </section>

        <!-- Armed bids -->
        <section>
            <h2 class="mb-2 text-sm uppercase tracking-wide text-muted-foreground">
                Armed vehicles
            </h2>
            <div class="overflow-hidden rounded border">
                <table class="w-full text-sm">
                    <thead class="bg-muted text-muted-foreground">
                        <tr>
                            <th class="p-2 text-left">Vehicle</th>
                            <th class="p-2 text-right">Current</th>
                            <th class="p-2 text-left">Account</th>
                            <th class="p-2 text-right">Max</th>
                            <th class="p-2 text-right">Last bid</th>
                            <th class="p-2 text-center">Bids</th>
                            <th class="p-2 text-left">Status</th>
                            <th class="p-2 text-right">Closes in</th>
                            <th class="p-2 text-right"></th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr
                            v-for="ab in armedBids"
                            :key="ab.id"
                            class="border-t"
                        >
                            <td class="p-2">
                                <a
                                    :href="`/vehicles/${ab.vehicle.wp_id}`"
                                    class="hover:text-sky-400"
                                >
                                    {{ ab.vehicle.name }}
                                </a>
                            </td>
                            <td class="p-2 text-right font-mono">
                                {{ fmt(ab.vehicle.current_price) }}
                            </td>
                            <td class="p-2">
                                {{ ab.account.label }}
                            </td>
                            <td class="p-2 text-right font-mono">
                                <input
                                    v-if="editingMax.id === ab.id"
                                    v-model="editingMax.draft"
                                    type="text"
                                    inputmode="numeric"
                                    class="w-28 rounded border bg-background px-1 py-0.5 text-right font-mono"
                                    @keydown.enter.prevent="saveMax(ab)"
                                    @keydown.escape="cancelEditMax"
                                    @blur="saveMax(ab)"
                                />
                                <button
                                    v-else
                                    class="hover:text-sky-400"
                                    @click="startEditMax(ab)"
                                >
                                    {{ fmt(ab.max_amount) }}
                                </button>
                            </td>
                            <td class="p-2 text-right font-mono">
                                {{ fmt(ab.last_bid) }}
                            </td>
                            <td class="p-2 text-center font-mono text-muted-foreground">
                                {{ ab.bid_count }}
                            </td>
                            <td class="p-2" :class="liveColor(ab.live_status)">
                                ● {{ ab.live_status }}
                            </td>
                            <td class="p-2 text-right font-mono text-muted-foreground">
                                {{ timeLeft(ab.vehicle.finish_time) }}
                            </td>
                            <td class="p-2 text-right">
                                <button
                                    class="text-xs text-rose-400 hover:underline"
                                    @click="disarm(ab)"
                                >
                                    disarm
                                </button>
                            </td>
                        </tr>
                        <tr v-if="!armedBids.length">
                            <td colspan="9" class="p-6 text-center text-muted-foreground">
                                Nothing armed yet.
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </section>
    </div>

    <!-- Account modal -->
    <Teleport to="body">
        <div
            v-if="accountModal.open"
            class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
            @click.self="closeAccountModal"
        >
            <div class="w-full max-w-md rounded border bg-background p-4 shadow-xl">
                <h3 class="mb-3 text-lg font-semibold">
                    {{ accountModal.editId ? "Edit account" : "Add account" }}
                </h3>

                <div class="space-y-3">
                    <div>
                        <label class="mb-1 block text-xs uppercase tracking-wide text-muted-foreground">
                            Label
                        </label>
                        <input
                            v-model="accountForm.label"
                            type="text"
                            class="w-full rounded border bg-background px-2 py-1.5 text-sm"
                            placeholder="e.g. Ernest"
                        />
                        <p v-if="accountForm.errors.label" class="mt-1 text-xs text-rose-400">
                            {{ accountForm.errors.label }}
                        </p>
                    </div>

                    <div>
                        <label class="mb-1 block text-xs uppercase tracking-wide text-muted-foreground">
                            Cookie
                            <span v-if="accountModal.editId" class="ml-1 text-muted-foreground/60">
                                (leave blank to keep existing)
                            </span>
                        </label>
                        <textarea
                            v-model="accountForm.cookie"
                            rows="3"
                            class="w-full rounded border bg-background px-2 py-1.5 font-mono text-xs"
                            placeholder="wordpress_logged_in_...=...; woocommerce_cart_hash=..."
                        />
                        <p v-if="accountForm.errors.cookie" class="mt-1 text-xs text-rose-400">
                            {{ accountForm.errors.cookie }}
                        </p>
                    </div>

                    <div>
                        <label class="mb-1 block text-xs uppercase tracking-wide text-muted-foreground">
                            Nonce
                            <span v-if="accountModal.editId" class="ml-1 text-muted-foreground/60">
                                (leave blank to keep existing)
                            </span>
                        </label>
                        <input
                            v-model="accountForm.nonce"
                            type="text"
                            class="w-full rounded border bg-background px-2 py-1.5 font-mono text-xs"
                            placeholder="86d2bd16fe"
                        />
                        <p v-if="accountForm.errors.nonce" class="mt-1 text-xs text-rose-400">
                            {{ accountForm.errors.nonce }}
                        </p>
                    </div>
                </div>

                <div class="mt-4 flex justify-end gap-2">
                    <button
                        class="rounded border px-3 py-1.5 text-sm"
                        @click="closeAccountModal"
                    >
                        Cancel
                    </button>
                    <button
                        class="rounded bg-sky-600 px-3 py-1.5 text-sm text-white hover:bg-sky-500 disabled:opacity-50"
                        :disabled="accountForm.processing"
                        @click="submitAccount"
                    >
                        {{ accountModal.editId ? "Save" : "Add" }}
                    </button>
                </div>
            </div>
        </div>
    </Teleport>

    <!-- Arm modal -->
    <Teleport to="body">
        <div
            v-if="armModal"
            class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
            @click.self="closeArmModal"
        >
            <div class="w-full max-w-md rounded border bg-background p-4 shadow-xl">
                <h3 class="mb-3 text-lg font-semibold">Arm a vehicle</h3>

                <div class="space-y-3">
                    <div>
                        <label class="mb-1 block text-xs uppercase tracking-wide text-muted-foreground">
                            Vehicle
                        </label>
                        <select
                            v-model.number="armForm.vehicle_id"
                            class="w-full rounded border bg-background px-2 py-1.5 text-sm"
                        >
                            <option :value="null">Select…</option>
                            <option
                                v-for="v in unarmedVehicles"
                                :key="v.id"
                                :value="v.id"
                            >
                                {{ v.name }} — {{ fmt(v.current_price) }}
                            </option>
                        </select>
                        <p v-if="armForm.errors.vehicle_id" class="mt-1 text-xs text-rose-400">
                            {{ armForm.errors.vehicle_id }}
                        </p>
                    </div>

                    <div>
                        <label class="mb-1 block text-xs uppercase tracking-wide text-muted-foreground">
                            Account
                        </label>
                        <select
                            v-model.number="armForm.account_id"
                            class="w-full rounded border bg-background px-2 py-1.5 text-sm"
                        >
                            <option :value="null">Select…</option>
                            <option
                                v-for="a in accounts"
                                :key="a.id"
                                :value="a.id"
                                :disabled="!accountAvailableForArm(a)"
                            >
                                {{ a.label }}
                                ({{ a.live_arms_count }}/{{ a.arms_limit }})
                                <template v-if="!accountAvailableForArm(a)">
                                    — {{ a.status === "valid" ? "full" : a.status }}
                                </template>
                            </option>
                        </select>
                        <p v-if="armForm.errors.account_id" class="mt-1 text-xs text-rose-400">
                            {{ armForm.errors.account_id }}
                        </p>
                    </div>

                    <div>
                        <label class="mb-1 block text-xs uppercase tracking-wide text-muted-foreground">
                            Max bid ({{ props.increment.toLocaleString() }} steps)
                        </label>
                        <input
                            v-model="armForm.max_amount"
                            type="text"
                            inputmode="numeric"
                            class="w-full rounded border bg-background px-2 py-1.5 text-right font-mono text-sm"
                            placeholder="e.g. 1500000"
                        />
                        <p v-if="armForm.errors.max_amount" class="mt-1 text-xs text-rose-400">
                            {{ armForm.errors.max_amount }}
                        </p>
                    </div>
                </div>

                <div class="mt-4 flex justify-end gap-2">
                    <button
                        class="rounded border px-3 py-1.5 text-sm"
                        @click="closeArmModal"
                    >
                        Cancel
                    </button>
                    <button
                        class="rounded bg-sky-600 px-3 py-1.5 text-sm text-white hover:bg-sky-500 disabled:opacity-50"
                        :disabled="armForm.processing || !armForm.account_id || !armForm.vehicle_id"
                        @click="submitArm"
                    >
                        Arm
                    </button>
                </div>
            </div>
        </div>
    </Teleport>
</template>