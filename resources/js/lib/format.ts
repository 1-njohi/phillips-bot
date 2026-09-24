export function fmt(n: number | null | undefined): string {
    return n == null ? '—' : Number(n).toLocaleString();
}

export function timeLeft(seconds: number | null | undefined): string {
    if (seconds == null) return '—';
    if (seconds <= 0) return 'closed';
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}m ${s.toString().padStart(2, '0')}s`;
}
export function stateClass(state: string): string {
    return (
        {
            discovered: 'bg-slate-700 text-slate-200',
            watching: 'bg-sky-900 text-sky-300',
            armed: 'bg-amber-900 text-amber-300',
            firing: 'bg-rose-900 text-rose-300 animate-pulse',
            done: 'bg-emerald-900 text-emerald-300',
            priced_out: 'bg-fuchsia-900 text-fuchsia-300',
            errored: 'bg-red-900 text-red-300',
        }[state] ?? 'bg-slate-700 text-slate-200'
    );
}

export function outcomeClass(outcome: string): string {
    return (
        {
            pending: 'text-slate-400',
            would_win: 'text-emerald-400',
            would_lose: 'text-rose-400',
            priced_out: 'text-fuchsia-400',
        }[outcome] ?? 'text-slate-400'
    );
}