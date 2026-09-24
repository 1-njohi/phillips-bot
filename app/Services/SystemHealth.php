<?php

namespace App\Services;

use App\Models\Vehicle;
use Illuminate\Support\Facades\Queue;

class SystemHealth
{
    public function snapshot(): array
    {
        $open = Vehicle::whereNotNull('finish_time')
            ->where('finish_time', '>', now())
            ->where('state', '!=', 'done');

        $staleCount = (clone $open)
            ->where(function ($q) {
                $q->whereNull('last_polled_at')
                  ->orWhere('last_polled_at', '<', now()->subMinutes(5));
            })
            ->count();

        return [
            'total_open'   => $open->count(),
            'stale_polls'  => $staleCount,
            'errored'      => Vehicle::where('state', 'errored')->count(),
            'queue_depth'  => Queue::size(),
            'clock_offset' => AuctionClock::offset(),
            'checked_at'   => now()->toIso8601String(),
        ];
    }
}