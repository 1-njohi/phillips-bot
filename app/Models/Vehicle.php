<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\HasMany;

class Vehicle extends Model
{
    protected $guarded = [];

    protected $casts = [
        'categories' => 'array',
        'finish_time' => 'datetime',
        'last_polled_at' => 'datetime',
        'last_price_change_at' => 'datetime',
        'watched' => 'boolean',
        'budget_alerted_at' => 'datetime',
    ];

    public function prices(): HasMany
    {
        return $this->hasMany(PriceHistory::class);
    }

    public function secondsToFinish(): ?int
    {
        return $this->finish_time
            ? now()->diffInSeconds($this->finish_time, false)
            : null;
    }

    public function deltaSince(int $minutes = 10): ?int
    {
        $oldest = $this->prices()
            ->where('recorded_at', '>=', now()->subMinutes($minutes))
            ->orderBy('recorded_at')
            ->value('price');

        if ($oldest === null || $this->current_price === null)
            return null;

        return $this->current_price - $oldest;
    }

    /** KES per minute over the given window. Null if not enough data. */
    public function velocity(int $minutes = 15): ?float
    {
        $rows = $this->prices()
            ->where('recorded_at', '>=', now()->subMinutes($minutes))
            ->orderBy('recorded_at')
            ->get(['price', 'recorded_at']);

        if ($rows->count() < 2)
            return null;

        $first = $rows->first();
        $last = $rows->last();
        $seconds = $last->recorded_at->diffInSeconds($first->recorded_at);

        if ($seconds < 1)
            return null;

        return round(($last->price - $first->price) / ($seconds / 60), 2);
    }

    public function isQuiet(int $minutes = 5): bool
    {
        if (!$this->last_price_change_at)
            return false;
        return $this->last_price_change_at->lt(now()->subMinutes($minutes));
    }

    public function projectedClose(): ?int
    {
        $v = $this->velocity(15);
        $left = $this->secondsToFinish();

        if ($v === null || $this->current_price === null || $left === null)
            return null;
        if ($left <= 0)
            return $this->current_price;

        return (int) round($this->current_price + $v * ($left / 60));
    }

    /** Loose score: high velocity + close to close = high pressure. */
    public function timePressure(): float
    {
        $v = $this->velocity(15) ?? 0;
        $left = $this->secondsToFinish() ?? 0;

        if ($left <= 0)
            return 0;

        return round($v * 60 / max(1, $left / 60), 2);
    }

    /** Last N prices in chronological order. */
    public function sparkline(int $points = 20): array
    {
        return $this->prices()
            ->orderBy('recorded_at', 'desc')
            ->limit($points)
            ->pluck('price')
            ->reverse()
            ->values()
            ->all();
    }

    public function isOpen(): bool
    {
        return $this->finish_time && $this->finish_time->isFuture()
            && $this->state !== 'done';
    }
}