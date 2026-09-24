<?php

namespace App\Services;

use Carbon\CarbonInterface;
use Illuminate\Support\Facades\Cache;

class AuctionClock
{
    public const CACHE_KEY = 'auction.clock_offset';

    public static function observe(?string $dateHeader): void
    {
        if (!$dateHeader)
            return;

        $server = strtotime($dateHeader);
        if ($server === false)
            return;

        $current = self::offset();
        $sample = $server - time();
        $next = (int) round($current * 0.7 + $sample * 0.3);

        Cache::put(self::CACHE_KEY, $next, now()->addHours(6));
    }

    public static function offset(): int
    {
        return (int) Cache::get(self::CACHE_KEY, 0);
    }

    public static function now(): CarbonInterface
    {
        return now()->addSeconds(self::offset());
    }
}