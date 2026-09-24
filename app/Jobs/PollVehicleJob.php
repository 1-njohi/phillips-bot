<?php

namespace App\Jobs;

use App\Models\PriceHistory;
use App\Models\Vehicle;
use App\Services\AuctionClient;
use App\Services\AuctionClock;
use Illuminate\Bus\Queueable;
use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Foundation\Bus\Dispatchable;
use Illuminate\Queue\InteractsWithQueue;
use Illuminate\Queue\SerializesModels;
use Illuminate\Support\Facades\Log;

class PollVehicleJob implements ShouldQueue
{
    use Dispatchable, InteractsWithQueue, Queueable, SerializesModels;

    public int $tries = 3;

    public function __construct(public int $vehicleId)
    {
    }

    public function handle(): void
    {
        $vehicle = Vehicle::find($this->vehicleId);
        if (!$vehicle)
            return;

        if (in_array($vehicle->state, ['done', 'errored'], true))
            return;

        $state = AuctionClient::fromConfig()->state(
            $vehicle->wp_product_id,
            $vehicle->finish_time?->timestamp
        );

        if (!$state) {
            $vehicle->update(['state' => 'errored']);
            Log::warning("Poll failed for vehicle {$vehicle->id}.");
            return;
        }

        $observed = (int) $state['current_price'];
        $status = $state['status'];
        $priceMoved = $vehicle->current_price !== null && $vehicle->current_price !== $observed;

        PriceHistory::create([
            'vehicle_id' => $vehicle->id,
            'price' => $observed,
            'state_text' => $status,
            'recorded_at' => now(),
        ]);

        $vehicle->update([
            'current_price' => $observed,
            'last_polled_at' => now(),
            'last_price_change_at' => $priceMoved ? now() : $vehicle->last_price_change_at,
        ]);
        $this->evaluateBudgetAlert($vehicle);
        if ($priceMoved) {
            Log::info("Price moved: vehicle={$vehicle->id} {$vehicle->getOriginal('current_price')} → {$observed}");
        }

        if ($status === 'closed') {
            $vehicle->update(['state' => 'done']);
            return;
        }

        $secondsLeft = $vehicle->finish_time
            ? AuctionClock::now()->diffInSeconds($vehicle->finish_time, false)
            : null;

        $delay = $this->cadenceFor($secondsLeft);
        self::dispatch($vehicle->id)->delay(now()->addSeconds($delay));
    }

    protected function evaluateBudgetAlert(Vehicle $vehicle): void
    {
        if ($vehicle->watch_price === null || $vehicle->current_price === null) {
            if ($vehicle->budget_alerted_at !== null) {
                $vehicle->update(['budget_alerted_at' => null]);
            }
            return;
        }

        $overBudget = $vehicle->current_price >= $vehicle->watch_price;

        if ($overBudget && $vehicle->budget_alerted_at === null) {
            $vehicle->update(['budget_alerted_at' => now()]);
            Log::info("Budget alert armed: vehicle={$vehicle->id} price={$vehicle->current_price} budget={$vehicle->watch_price}");
        } elseif (!$overBudget && $vehicle->budget_alerted_at !== null) {
            // Price moved back under budget. Rearm so a future crossing fires again.
            $vehicle->update(['budget_alerted_at' => null]);
        }
    }

    protected function cadenceFor(?int $secondsLeft): int
    {
        if ($secondsLeft === null || $secondsLeft <= 0)
            return 300;

        foreach (config('auction.cadence') as $threshold => $interval) {
            if ($secondsLeft >= $threshold) {
                return $this->jitter($interval);
            }
        }

        return $this->jitter(20);
    }

    protected function jitter(int $seconds): int
    {
        $spread = (int) round($seconds * (float) config('auction.jitter', 0.20));
        return max(1, $seconds + random_int(-$spread, $spread));
    }
}