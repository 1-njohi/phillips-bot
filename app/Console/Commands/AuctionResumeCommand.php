<?php

namespace App\Console\Commands;

use App\Jobs\PollVehicleJob;
use App\Models\Vehicle;
use Illuminate\Console\Command;

class AuctionResumeCommand extends Command
{
    protected $signature   = 'auction:resume';
    protected $description = 'Re-dispatch poll chains for stale vehicles.';

    public function handle(): int
    {
        $stale = Vehicle::whereIn('state', ['watching', 'armed'])
            ->where(function ($q) {
                $q->whereNull('last_polled_at')
                  ->orWhere('last_polled_at', '<', now()->subMinutes(6));
            })
            ->pluck('id');

        $stale->each(fn ($id) => PollVehicleJob::dispatch($id));

        if ($stale->isNotEmpty()) {
            $this->info("Resumed {$stale->count()} vehicle(s).");
        }

        return self::SUCCESS;
    }
}