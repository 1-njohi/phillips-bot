<?php

namespace App\Console\Commands;

use App\Models\PriceHistory;
use App\Models\Vehicle;
use App\Services\SimulatedAuctionApi;
use Illuminate\Console\Command;

class AuctionResetCycle extends Command
{
    protected $signature   = 'auction:reset-cycle {--keep-history : Keep price_history rows}';
    protected $description = 'Reset the simulation cycle to start now.';

    public function handle(): int
    {
        $epoch = SimulatedAuctionApi::resetCycle();

        Vehicle::query()->update([
            'state'                => 'watching',
            'current_price'        => null,
            'last_price_change_at' => null,
            'last_polled_at'       => null,
        ]);

        if (!$this->option('keep-history')) {
            PriceHistory::query()->truncate();
        }

        $this->info("Cycle reset. Epoch = {$epoch} (" . now()->toDateTimeString() . ")");
        $this->line('Vehicles reset. Poll chains will restart on the next dispatch.');

        return self::SUCCESS;
    }
}