<?php

namespace App\Jobs;

use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Foundation\Queue\Queueable;
use Illuminate\Foundation\Bus\Dispatchable;
use Illuminate\Queue\InteractsWithQueue;
use Illuminate\Queue\SerializesModels;
use App\Models\ArmedBid;

class TriggerAllSnipersJob implements ShouldQueue
{
    use Dispatchable, InteractsWithQueue, Queueable, SerializesModels;

    public function handle(): void
    {
        $lead = (int) config('auction.endgame_lead');

        $armed = ArmedBid::with(['account', 'vehicle'])
            ->where('status', 'armed')
            ->whereHas('vehicle', function ($q) use ($lead) {
                $q->where('finish_time', '>', now())
                  ->where('finish_time', '<=', now()->addSeconds($lead + 60));
            })
            ->whereHas('account', fn ($q) => $q->where('status', 'valid'))
            ->get();

        foreach ($armed as $ab) {
            $delay = random_int(0, 30);
            TriggerSniperJob::dispatch($ab->id)->delay(now()->addSeconds($delay));
        }
    }
}