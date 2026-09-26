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
        $client = AuctionClient::fromConfig();
        $rows = $client->roster();

        if (empty($rows)) {
            Log::warning('Roster fetch returned no products.');
            return;
        }

        // One finish-time probe per category. On this auctioneer's
        // site, all products in a category close at the same time.
        // Three categories → three page fetches per sync, not 100.
        $finishByCategory = [];
        foreach ($rows as $row) {
            $categoryId = $row['categories'][0] ?? null;
            if (!$categoryId || !$row['slug'] || isset($finishByCategory[$categoryId])) {
                continue;
            }

            $finishByCategory[$categoryId] = $client->finishTimeForSlug($row['slug']);
            Log::info("Finish probe: category {$categoryId} → " . ($finishByCategory[$categoryId] ?? 'null'));

            // Be polite: 300ms between page fetches.
            usleep(300_000);
        }

        $new = 0;
        $resolved = 0;

        foreach ($rows as $row) {
            $categoryId = $row['categories'][0] ?? null;
            $finishTs = $finishByCategory[$categoryId] ?? null;

            if ($finishTs !== null) {
                $resolved++;
            }

            $vehicle = Vehicle::updateOrCreate(
                ['wp_product_id' => $row['id']],
                [
                    'name' => $row['name'],
                    'slug' => $row['slug'],
                    'categories' => $row['categories'],
                    'finish_time' => $finishTs
                        ? \Carbon\CarbonImmutable::createFromTimestamp($finishTs)
                        : \Carbon\Carbon::now()->addMinutes(908),
                    'wp_modified_at' => $row['modified'],
                ]
            );

            if ($vehicle->wasRecentlyCreated) {
                $new++;
                PollVehicleJob::dispatch($vehicle->id);
            }
        }

        Log::info("Roster fetch: {$new} new, {$resolved} with finish times, " . count($rows) . ' total.');
    }
}