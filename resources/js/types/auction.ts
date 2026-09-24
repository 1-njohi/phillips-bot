export type VehicleState =
    | "discovered"
    | "watching"
    | "armed"
    | "firing"
    | "done"
    | "priced_out"
    | "errored";

export interface VehicleRow {
    id: number;
    wp_id: number;
    name: string;
    state: string;
    watched: boolean;
    watch_price: number | null;
    finish_time: string | null;
    seconds_left: number | null;
    current_price: number | null;
    delta_10m: number | null;
    velocity_15m: number | null;
    velocity_60m: number | null;
    projected_close: number | null;
    time_pressure: number;
    is_quiet: boolean;
    last_price_change_at: string | null;
    last_polled_at: string | null;
    sparkline: number[];
}

export interface Health {
    total_open: number;
    stale_polls: number;
    errored: number;
    queue_depth: number;
    clock_offset: number;
}

export interface FeedEvent {
    at: string;
    wp_id: number;
    vehicle: string;
    type: "jump" | "threshold" | "stall";
    severity: "info" | "good" | "warn";
    message: string;
}

export interface DashboardState {
    vehicles: VehicleRow[];
    events: FeedEvent[];
    health: Health;
    clock_offset: number;
    server_time: string;
}

export interface Leaderboards {
    window_minutes: number;
    fastest_climbers: Leader[];
    highest_value: Leader[];
    biggest_delta: Leader[];
    closest_to_close: Leader[];
}

export interface Leader {
    wp_id: number;
    name: string;
    value: number | null;
    secondary?: number | null;
}

export interface HistoryPoint {
    t: string;
    p: number;
}

export interface DashboardState {
    vehicles: VehicleRow[];
    clock_offset: number;
    server_time: string;
}
export interface ActivityRow {
    id: number;
    vehicle: string | null;
    wp_product_id: number;
    amount: number;
    observed: number;
    price_after: number | null;
    success: boolean;
    fired_at: string | null;
}

export interface DashboardState {
    vehicles: VehicleRow[];
    activity: ActivityRow[];
    clock_offset: number;
    server_time: string;
}
