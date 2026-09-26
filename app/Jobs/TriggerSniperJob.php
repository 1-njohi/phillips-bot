<?php

namespace App\Jobs;

use App\Models\ArmedBid;
use Illuminate\Bus\Queueable;
use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Foundation\Bus\Dispatchable;
use Illuminate\Queue\InteractsWithQueue;
use Illuminate\Queue\SerializesModels;
use Illuminate\Support\Facades\Log;

class TriggerSniperJob implements ShouldQueue
{
    use Dispatchable, InteractsWithQueue, Queueable, SerializesModels;

    public int $tries = 1;
    public int $timeout = 400;   // > 5 min + buffer

    public function __construct(public int $armedBidId)
    {
    }

    public function handle(): void
    {
        $armed = ArmedBid::with(['account', 'vehicle'])->find($this->armedBidId);
        if (!$armed || $armed->status !== 'armed') {
            return;
        }

        $finishTs = $armed->vehicle->finish_time?->timestamp;
        if (!$finishTs) {
            $armed->update(['status' => 'errored']);
            return;
        }

        $lead = (int) config('auction.endgame_lead');   // 300
        $fireAt = $finishTs - $lead;
        $sleep = $fireAt - now()->timestamp;

        if ($sleep > 0) {
            sleep($sleep);
        }

        // The user may have disarmed or the account may have gone invalid
        // while we slept. Re-check before spawning.
        $armed->refresh();

        if ($armed->status !== 'armed') {
            return;
        }

        if (!$armed->account->isValid()) {
            $armed->update(['status' => 'errored']);
            return;
        }

        // Find a sibling ArmedBid on the same account, but only one whose
        // vehicle also closes within the same endgame window. A vehicle
        // that closes an hour from now must not be lumped in with this
        // spawn — it will be picked up by a later TriggerAllSnipersJob
        // when its own window arrives.
        $sibling = ArmedBid::where('account_id', $armed->account_id)
            ->where('status', 'armed')
            ->where('id', '!=', $armed->id)
            ->whereHas('vehicle', function ($q) use ($lead) {
                $q->where('finish_time', '>', now())
                    ->where('finish_time', '<=', now()->addSeconds($lead + 60));
            })
            ->with('vehicle')
            ->first();

        $this->spawnSniper($armed, $sibling);

        $armed->update(['status' => 'firing']);

        if ($sibling) {
            $sibling->update(['status' => 'firing']);
        }
    }

    private function spawnSniper(ArmedBid $v1, ?ArmedBid $v2): void
    {
        $script = config('auction.sniper_script');
        $python = config('auction.sniper_python');
        $logFile = storage_path("logs/sniper-{$v1->id}-" . date('Ymd-His') . '.log');

        $args = [
            '--armed-bid-id1',
            (string) $v1->id,
            '--vehicle-id1',
            (string) $v1->vehicle_id,
            '--product1',
            (string) $v1->vehicle->wp_product_id,
            '--slug1',
            (string) $v1->vehicle->slug,
            '--max1',
            (string) $v1->max_amount,
            '--nonce',
            $v1->account->nonce,
            '--cookie',
            $v1->account->cookie,
            '--db-path',
            base_path('database/database.sqlite'),

            '--base-url',
            config('auction.wp_url'),
            '--increment',
            (string) config('auction.increment'),
            '--poll-quiet-ms',
            (string) config('auction.poll_quiet_ms'),
            '--poll-contest-ms',
            (string) config('auction.poll_contest_ms'),
            '--contest-window-s',
            (string) config('auction.contest_window_s'),
            '--tail-safety-ms',
            (string) config('auction.tail_safety_ms'),
            '--tail-min-ms',
            (string) config('auction.tail_min_ms'),
        ];

        if ($v2 !== null && $v2->id !== $v1->id) {
            $args[] = '--armed-bid-id2';
            $args[] = (string) $v2->id;
            $args[] = '--vehicle-id2';
            $args[] = (string) $v2->vehicle_id;
            $args[] = '--product2';
            $args[] = (string) $v2->vehicle->wp_product_id;
            $args[] = '--slug2';
            $args[] = (string) $v2->vehicle->slug;
            $args[] = '--max2';
            $args[] = (string) $v2->max_amount;
        }

        $cmd = sprintf(
            'cd %s && nohup %s %s %s > %s 2>&1 &',
            escapeshellarg(base_path()),
            escapeshellarg($python),
            escapeshellarg($script),
            implode(' ', array_map('escapeshellarg', $args)),
            escapeshellarg($logFile),
        );

        Log::debug('Spawning sniper', ['cmd' => $cmd]);
        exec($cmd);

        Log::info('Spawned sniper', [
            'armed_bid_id' => $v1->id,
            'second_id' => $v2?->id,
            'log' => $logFile,
        ]);
    }
}