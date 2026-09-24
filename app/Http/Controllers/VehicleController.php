<?php

namespace App\Http\Controllers;

use App\Models\Vehicle;
use Carbon\CarbonImmutable;
use Illuminate\Http\JsonResponse;
use Illuminate\Support\Collection;
use Inertia\Inertia;
use Inertia\Response;

class VehicleController extends Controller
{
    /** GET /vehicles/{wpId} — the detail page shell. */
    public function detail(int $wpId): Response
    {
        return Inertia::render('VehicleDetail', ['wpId' => $wpId]);
    }

    /** GET /api/vehicles/{wpId}/chart — OHLCV series + metadata, all from the DB. */
    public function chart(int $wpId): JsonResponse
    {
        $vehicle = Vehicle::where('wp_product_id', $wpId)->first();

        if (!$vehicle) {
            return response()->json(['error' => 'not_found'], 404);
        }

        $history = $vehicle->prices()
            ->orderBy('recorded_at')
            ->get(['price', 'recorded_at']);

        $candles = $this->candlesFromHistory($history, 60);
        $basePrice = $history->first()?->price ?? $vehicle->current_price ?? 0;
        $bidCount = $this->countPriceIncreases($history);
        $secondsLeft = $vehicle->finish_time
            ? (int) max(0, now()->diffInSeconds($vehicle->finish_time, false))
            : 0;

        return response()->json([
            'vehicle' => [
                'wp_product_id' => $vehicle->wp_product_id,
                'name' => $vehicle->name,
                'category' => $vehicle->categories[0] ?? null,
                'base_price' => $basePrice,
                'current_price' => $vehicle->current_price,
                'bid_count' => $bidCount,
                'increment' => 5000,
                'started_at' => $history->first()?->recorded_at?->timestamp,
                'close_at' => $vehicle->finish_time?->timestamp,
                'seconds_left' => $secondsLeft,
                'status' => $secondsLeft > 0 ? 'open' : 'closed',
                'watched' => (bool) $vehicle->watched,
                'watch_price' => $vehicle->watch_price,
                'budget_alerted_at' => $vehicle->budget_alerted_at?->toIso8601String(),
            ],
            'candles' => $candles,
        ]);
    }

    /**
     * Bucket price_history rows into OHLCV candles.
     *
     * @param  Collection<int, \App\Models\PriceHistory>  $rows  ordered by recorded_at asc
     * @return array<int, array{time:int, open:int, high:int, low:int, close:int, volume:int, bid_count:int}>
     */
    protected function candlesFromHistory(Collection $rows, int $bucketSeconds = 60): array
    {
        if ($rows->isEmpty()) {
            return [];
        }

        $candles = [];
        $bucket = null;
        $prevPrice = null;

        foreach ($rows as $row) {
            $delta = $prevPrice === null ? 0 : $row->price - $prevPrice;

            $ts = $row->recorded_at->timestamp;
            $bucketTime = intdiv($ts, $bucketSeconds) * $bucketSeconds;

            if ($bucket === null || $bucket['time'] !== $bucketTime) {
                if ($bucket !== null) {
                    $candles[] = $bucket;
                }

                $bucket = [
                    'time' => $bucketTime,
                    'open' => $row->price,
                    'high' => $row->price,
                    'low' => $row->price,
                    'close' => $row->price,
                    'volume' => $delta > 0 ? $delta : 0,
                    'bid_count' => $delta > 0 ? 1 : 0,
                ];
            } else {
                if ($delta > 0) {
                    $bucket['volume'] += $delta;
                    $bucket['bid_count'] += 1;
                }
                $bucket['high'] = max($bucket['high'], $row->price);
                $bucket['low'] = min($bucket['low'], $row->price);
                $bucket['close'] = $row->price;
            }

            $prevPrice = $row->price;
        }

        if ($bucket !== null) {
            $candles[] = $bucket;
        }

        // Cap to the most recent 500 candles to keep the payload bounded.
        return array_slice($candles, -500);
    }

    /** A "bid" is a price increase between two consecutive observations. */
    protected function countPriceIncreases(Collection $rows): int
    {
        $count = 0;
        $prev = null;

        foreach ($rows as $row) {
            if ($prev !== null && $row->price > $prev) {
                $count++;
            }
            $prev = $row->price;
        }

        return $count;
    }
}