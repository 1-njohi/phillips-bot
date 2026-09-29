<?php

namespace App\Http\Controllers;

use App\Jobs\FetchRosterJob;
use App\Models\PriceHistory;
use App\Models\Vehicle;
use App\Services\AuctionClock;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Inertia\Inertia;
use Inertia\Response;

class DashboardController extends Controller
{
    public function index(): Response
    {
        return Inertia::render('Dashboard');
    }

    public function state(): JsonResponse
    {
        $vehicles = Vehicle::query()
        ->orderBy('created_at', 'ASC')
        ->take(50)
            ->get()
            ->map(fn(Vehicle $v) => [
                'id' => $v->id,
                'wp_id' => $v->wp_product_id,
                'name' => $v->name,
                'state' => $v->state,
                'watched' => (bool) $v->watched,
                'watch_price' => $v->watch_price,
                'budget_alerted_at' => $v->budget_alerted_at?->toIso8601String(),
                'finish_time' => $v->finish_time?->toIso8601String(),
                'seconds_left' => $v->secondsToFinish(),
                'current_price' => $v->current_price,
                'delta_10m' => $v->deltaSince(10),
                'velocity_15m' => $v->velocity(15),
                'velocity_60m' => $v->velocity(60),
                'projected_close' => $v->projectedClose(),
                'time_pressure' => $v->timePressure(),
                'is_quiet' => $v->isQuiet(5),
                'last_price_change_at' => $v->last_price_change_at?->diffForHumans(),
                'last_polled_at' => $v->last_polled_at?->diffForHumans(),
                'sparkline' => $v->sparkline(20),
            ]);

        return response()->json([
            'vehicles' => $vehicles,
            'events' => $this->events(),
            'health' => $this->health(),
            'clock_offset' => AuctionClock::offset(),
            'server_time' => now()->toIso8601String(),
        ]);
    }

    public function history(Vehicle $vehicle): JsonResponse
    {
        $points = $vehicle->prices()
            ->orderBy('recorded_at')
            ->get(['price', 'recorded_at'])
            ->map(fn($p) => ['t' => $p->recorded_at->toIso8601String(), 'p' => $p->price]);

        return response()->json([
            'vehicle' => ['id' => $vehicle->id, 'name' => $vehicle->name, 'wp_id' => $vehicle->wp_product_id],
            'points' => $points,
        ]);
    }

    public function csv(Vehicle $vehicle)
    {
        $rows = $vehicle->prices()->orderBy('recorded_at')->get(['recorded_at', 'price']);

        $out = "timestamp,price\n";
        foreach ($rows as $r) {
            $out .= $r->recorded_at->toIso8601String() . ',' . $r->price . "\n";
        }

        return response($out, 200, [
            'Content-Type' => 'text/csv',
            'Content-Disposition' => 'attachment; filename="vehicle-' . $vehicle->wp_product_id . '.csv"',
        ]);
    }

    public function refreshRoster(): JsonResponse
    {
        FetchRosterJob::dispatch();
        return response()->json(['ok' => true]);
    }

    public function toggleWatch(Vehicle $vehicle): JsonResponse
    {
        $vehicle->update(['watched' => !$vehicle->watched]);
        return response()->json(['watched' => $vehicle->watched]);
    }

    public function setWatchPrice(Request $request, Vehicle $vehicle): JsonResponse
    {
        $data = $request->validate([
            'watch_price' => ['nullable', 'integer', 'min:0'],
        ]);

        // Reset the alert when the budget changes so a new crossing can fire.
        $vehicle->update([
            'watch_price' => $data['watch_price'],
            'budget_alerted_at' => null,
        ]);

        return response()->json(['watch_price' => $vehicle->watch_price]);
    }

    /** Event feed derived from recent price_history. Nothing is stored. */
    protected function events(): array
    {
        $events = [];

        // Jumps + threshold crossings from the last 20 minutes.
        $rows = PriceHistory::with('vehicle:id,name,wp_product_id')
            ->where('recorded_at', '>=', now()->subMinutes(120))
            ->orderBy('recorded_at')
            ->get();

        foreach ($rows->groupBy('vehicle_id') as $group) {
            $prev = null;
            foreach ($group as $row) {
                $vehicle = $row->vehicle;
                if (!$vehicle)
                    continue;

                if ($prev !== null) {
                    $delta = $row->price - $prev->price;
                    $increment = (int) config('auction.increment');

                    if ($delta >= $increment * 5) {
                        $events[] = [
                            'at' => $row->recorded_at->toIso8601String(),
                            'wp_id' => $vehicle->wp_product_id,
                            'vehicle' => $vehicle->name,
                            'type' => 'jump',
                            'severity' => 'info',
                            'message' => sprintf(
                                '%s jumped +%s in one tick',
                                $vehicle->name,
                                number_format($delta),
                            ),
                        ];
                    }

                    $before = intdiv($prev->price, 500_000);
                    $after = intdiv($row->price, 500_000);

                    if ($after > $before) {
                        $boundary = $after * 500_000;
                        $events[] = [
                            'at' => $row->recorded_at->toIso8601String(),
                            'wp_id' => $vehicle->wp_product_id,
                            'vehicle' => $vehicle->name,
                            'type' => 'threshold',
                            'severity' => 'good',
                            'message' => sprintf(
                                '%s crossed %s',
                                $vehicle->name,
                                number_format($boundary),
                            ),
                        ];
                    }
                }

                $prev = $row;
            }
        }

        // Stall events for open vehicles.
        $open = Vehicle::whereNotNull('finish_time')
            ->where('finish_time', '>', now())
            ->where('state', '!=', 'done')
            ->get();

        foreach ($open as $v) {
            if ($v->isQuiet(5)) {
                $events[] = [
                    'at' => now()->toIso8601String(),
                    'wp_id' => $v->wp_product_id,
                    'vehicle' => $v->name,
                    'type' => 'stall',
                    'severity' => 'warn',
                    'message' => $v->name . ' quiet for 5+ minutes',
                ];
            }
        }

        usort($events, fn($a, $b) => strcmp($b['at'], $a['at']));

        return array_slice($events, 0, 30);
    }

    protected function health(): array
    {
        return app(\App\Services\SystemHealth::class)->snapshot();
    }

    public function toggleWatchByWpId(int $wpId): JsonResponse
    {
        $vehicle = Vehicle::where('wp_product_id', $wpId)->firstOrFail();
        $vehicle->update(['watched' => !$vehicle->watched]);

        return response()->json(['watched' => $vehicle->watched]);
    }

    public function setWatchPriceByWpId(Request $request, int $wpId): JsonResponse
    {
        $data = $request->validate([
            'watch_price' => ['nullable', 'integer', 'min:0'],
        ]);

        $vehicle = Vehicle::where('wp_product_id', $wpId)->firstOrFail();
        $vehicle->update([
            'watch_price' => $data['watch_price'],
            'budget_alerted_at' => null,
        ]);

        return response()->json(['watch_price' => $vehicle->watch_price]);
    }
}