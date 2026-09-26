<?php

namespace App\Jobs;

use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Foundation\Queue\Queueable;
use Illuminate\Foundation\Bus\Dispatchable;
use Illuminate\Queue\InteractsWithQueue;
use Illuminate\Queue\SerializesModels;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Log;
use App\Models\Account;
use App\Models\Vehicle;

class ValidateAccountJob implements ShouldQueue
{
    use Dispatchable, InteractsWithQueue, Queueable, SerializesModels;

    public int $tries = 1;
    public int $timeout = 30;

    public function __construct(
        public int $accountId,
        public int $probeVehicleWpId,
    ) {}

    public function handle(): void
    {
        $account = Account::find($this->accountId);
        if (!$account) {
            Log::warning('ValidateAccountJob: account not found', [
                'account_id' => $this->accountId,
            ]);
            return;
        }

        $vehicle = Vehicle::where('wp_product_id', $this->probeVehicleWpId)->first();
        if (!$vehicle) {
            Log::warning('ValidateAccountJob: probe vehicle not found', [
                'account_id'          => $account->id,
                'probe_vehicle_wp_id' => $this->probeVehicleWpId,
            ]);
            return;
        }

        // Assemble the bid POST exactly as the snipe script does.
        // See app/Scripts/snipe.py: fire_bid() for the canonical shape.
        $payload = [
            'action'   => 'yith_wcact_add_bid',
            'security' => $account->nonce,       // decrypted by cast
            'currency' => config('auction.currency'),
            'bid'      => config('auction.increment'),
            'product'  => $vehicle->wp_product_id,
        ];

        \Log::info($payload);

        $baseUrl = rtrim(config('auction.wp_url'), '/');
        $userAgent = config('auction.user_agent')
            ?? 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36';

        try {
            $response = Http::asForm()
                ->timeout(15)
                ->withHeaders([
                    'Cookie'           => $account->cookie,   // decrypted by cast
                    'X-Requested-With' => 'XMLHttpRequest',
                    'Referer'          => "{$baseUrl}/product/{$vehicle->slug}/",
                    'User-Agent'       => $userAgent,
                ])
                ->post("{$baseUrl}/wp-admin/admin-ajax", $payload);
        } catch (\Throwable $e) {
            Log::error('ValidateAccountJob: HTTP request threw', [
                'account_id' => $account->id,
                'error'      => $e->getMessage(),
            ]);

            $account->status = 'invalid';
            $account->save();
            return;
        }

        $status = $response->status();
        $body   = $response->json();

        if (!is_array($body)) {
            $body = ['raw' => $response->body()];
        }

        Log::info('ValidateAccountJob response', [
            'account_id' => $account->id,
            'status'     => $status,
            'body'       => $body,
        ]);

        $isValid = $status === 200;

        $account->status = $isValid ? 'valid' : 'invalid';
        if ($isValid) {
            $account->cookie_captured_at = now();
        }
        $account->save();
    }

    public function failed(\Throwable $e): void
    {
        Log::error('ValidateAccountJob failed', [
            'account_id' => $this->accountId,
            'error'      => $e->getMessage(),
        ]);

        Account::where('id', $this->accountId)->update(['status' => 'invalid']);
    }
}