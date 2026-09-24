<?php

namespace App\Jobs;

use App\Models\Vehicle;
use App\Services\AuctionClient;
use Illuminate\Bus\Queueable;
use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Foundation\Bus\Dispatchable;
use Illuminate\Queue\InteractsWithQueue;
use Illuminate\Queue\SerializesModels;
use Illuminate\Support\Facades\Log;

class FetchRosterJob implements ShouldQueue
{
    use Dispatchable, InteractsWithQueue, Queueable, SerializesModels;

    public int $tries = 3;

    public function handle(): void
    {
        $rows = AuctionClient::fromConfig()->roster();

        if (empty($rows)) {
            Log::warning('Roster fetch returned no products.');
            return;
        }

        $new = 0;


        foreach ($rows as $row) {
            // if (!$row['finish_time']) {
            //     continue; // skip non-auction products
            // }

            $vehicle = Vehicle::updateOrCreate(
                ['wp_product_id' => $row['id']],
                [
                    'name' => $row['name'],
                    'slug' => $row['slug'],
                    'categories' => $row['categories'],
                    'finish_time' => now()-> addMinute(20),// setTimestamp($row['finish_time']),
                    'wp_modified_at' => $row['modified'],
                ]
            );

            if ($vehicle->wasRecentlyCreated) {
                $new++;
                PollVehicleJob::dispatch($vehicle->id);
            }
        }

        Log::info("Roster fetch: {$new} new, " . count($rows) . ' total.');
    }
}