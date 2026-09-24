<?php

namespace App\Http\Controllers;

use App\Models\Vehicle;
use Carbon\CarbonImmutable;
use Illuminate\Http\JsonResponse;
use Inertia\Inertia;
use Inertia\Response;

class AnalyticsController extends Controller
{
    public function index(): Response
    {
        return Inertia::render('Analytics');
    }

    /** GET /api/analytics/overlay — every vehicle indexed to 100 at first observation. */
    public function overlay(): JsonResponse
    {
        $vehicles = Vehicle::whereNotNull('current_price')->get();
        $series   = [];

        foreach ($vehicles as $v) {
            $rows = $v->prices()
                ->orderBy('recorded_at')
                ->get(['price', 'recorded_at']);

            if ($rows->isEmpty()) {
                continue;
            }

            $baseline = $rows->first()->price ?: 1;
            $bucketSec = 60;

            // Bucket to 1-minute intervals, keep the last price per bucket.
            $bucketed = [];
            foreach ($rows as $row) {
                $bt = intdiv($row->recorded_at->timestamp, $bucketSec) * $bucketSec;
                $bucketed[$bt] = round(($row->price / $baseline) * 100, 3);
            }

            $points = [];
            foreach ($bucketed as $time => $value) {
                $points[] = ['time' => $time, 'value' => $value];
            }

            $series[] = [
                'wp_id'  => $v->wp_product_id,
                'name'   => $v->name,
                'points' => $points,
            ];
        }

        return response()->json(['series' => $series]);
    }

    /** GET /api/analytics/leaderboards — every card computed from the DB. */
    public function leaderboards(): JsonResponse
    {
        $vehicles = Vehicle::whereNotNull('current_price')->get();
        $rows     = [];

        foreach ($vehicles as $v) {
            $velocity15 = $v->velocity(15);
            $velocity60 = $v->velocity(60);
            $delta10    = $v->deltaSince(10);

            $total15 = $velocity15 !== null ? (int) round($velocity15 * 15) : null;
            $rate10  = $delta10 !== null ? round($delta10 / 10, 2) : null;

            $bids15      = $this->countBids($v, 15);
            $bidsRate15  = $bids15 !== null ? round($bids15 / 15, 2) : null;
            $pctGain     = $this->pctGain($v);
            $biggestBid  = $this->biggestBid($v, 30);

            $rows[] = [
                'wp_id'          => $v->wp_product_id,
                'name'           => $v->name,
                'current_price'  => $v->current_price,
                'velocity_15m'   => $velocity15,
                'velocity_60m'   => $velocity60,
                'total_15m'      => $total15,
                'delta_10m'      => $delta10,
                'rate_10m'       => $rate10,
                'acceleration'   => ($velocity15 !== null && $velocity60 !== null)
                    ? round($velocity15 - $velocity60, 2)
                    : null,
                'bids_15m'       => $bids15,
                'bids_rate_15m'  => $bidsRate15,
                'pct_gain'       => $pctGain,
                'biggest_bid'    => $biggestBid['amount'] ?? null,
                'biggest_bid_at' => $biggestBid['at'] ?? null,
            ];
        }

        $rank = function (
            array $rows,
            callable $score,
            string $dir = 'desc',
            ?callable $secondary = null,
        ) {
            $filtered = array_values(array_filter($rows, fn ($r) => $score($r) !== null));

            usort($filtered, function ($a, $b) use ($score, $dir) {
                $cmp = $score($a) <=> $score($b);
                return $dir === 'asc' ? $cmp : -$cmp;
            });

            return array_map(function ($r) use ($score, $secondary) {
                $item = [
                    'wp_id' => $r['wp_id'],
                    'name'  => $r['name'],
                    'value' => $score($r),
                ];
                if ($secondary !== null) {
                    $item['secondary'] = $secondary($r);
                }
                return $item;
            }, $filtered);
        };

        $windowMinutes = 15;

        return response()->json([
            'window_minutes'   => $windowMinutes,
            'fastest_climbers' => $rank(
                $rows,
                fn ($r) => $r['velocity_15m'],
                'desc',
                fn ($r) => $r['total_15m'],
            ),
            'highest_value'    => $rank($rows, fn ($r) => $r['current_price']),
            'biggest_delta'    => $rank(
                $rows,
                fn ($r) => $r['delta_10m'],
                'desc',
                fn ($r) => $r['rate_10m'],
            ),
            'hottest'          => $rank(
                $rows,
                fn ($r) => $r['bids_15m'],
                'desc',
                fn ($r) => $r['bids_rate_15m'],
            ),
            'accelerating'     => $rank(
                $rows,
                fn ($r) => $r['acceleration'],
                'desc',
                fn ($r) => $r['velocity_15m'],
            ),
            'biggest_gainer'   => $rank($rows, fn ($r) => $r['pct_gain']),
            'coldest'          => $rank(
                $rows,
                fn ($r) => $r['velocity_15m'],
                'asc',
                fn ($r) => $r['total_15m'],
            ),
            'biggest_bid'      => $rank(
                $rows,
                fn ($r) => $r['biggest_bid'],
                'desc',
                fn ($r) => $r['biggest_bid_at'],
            ),
        ]);
    }

    /** Count price increases in the last N minutes. */
    protected function countBids(Vehicle $v, int $minutes): ?int
    {
        $rows = $v->prices()
            ->where('recorded_at', '>=', now()->subMinutes($minutes))
            ->orderBy('recorded_at')
            ->get(['price']);

        if ($rows->count() < 2) {
            return null;
        }

        $count = 0;
        $prev  = null;
        foreach ($rows as $r) {
            if ($prev !== null && $r->price > $prev) {
                $count++;
            }
            $prev = $r->price;
        }

        return $count;
    }

    /** Percentage gain from the earliest observed price to now. */
    protected function pctGain(Vehicle $v): ?float
    {
        $first = $v->prices()->orderBy('recorded_at')->value('price');

        if (!$first || !$v->current_price) {
            return null;
        }

        return round((($v->current_price - $first) / $first) * 100, 2);
    }

    /** Largest single jump in the last N minutes. */
    protected function biggestBid(Vehicle $v, int $minutes): ?array
    {
        $rows = $v->prices()
            ->where('recorded_at', '>=', now()->subMinutes($minutes))
            ->orderBy('recorded_at')
            ->get(['price', 'recorded_at']);

        if ($rows->count() < 2) {
            return null;
        }

        $maxDelta = 0;
        $maxAt    = null;
        $prev     = null;

        foreach ($rows as $r) {
            if ($prev !== null) {
                $delta = $r->price - $prev->price;
                if ($delta > $maxDelta) {
                    $maxDelta = $delta;
                    $maxAt    = $r->recorded_at->timestamp;
                }
            }
            $prev = $r;
        }

        return $maxDelta > 0
            ? ['amount' => $maxDelta, 'at' => $maxAt]
            : null;
    }
}