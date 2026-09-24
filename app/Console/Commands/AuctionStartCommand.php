<?php

namespace App\Console\Commands;

use App\Jobs\FetchRosterJob;
use App\Jobs\PollVehicleJob;
use App\Models\Vehicle;
use Illuminate\Console\Command;

class AuctionStartCommand extends Command
{
    protected $signature   = 'auction:start {--vehicle= : Poll one vehicle by local ID}';
    protected $description = 'Fetch roster and (re)start poll chains.';

    public function handle(): int
    {
        if ($id = $this->option('vehicle')) {
            PollVehicleJob::dispatch((int) $id);
            $this->info("Dispatched poll for vehicle {$id}.");
            return self::SUCCESS;
        }

        FetchRosterJob::dispatch();
        $this->info('Roster fetch dispatched.');

        Vehicle::whereIn('state', ['discovered', 'watching', 'armed'])
            ->pluck('id')
            ->each(fn ($id) => PollVehicleJob::dispatch($id));

        return self::SUCCESS;
    }
}