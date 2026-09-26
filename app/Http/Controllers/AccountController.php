<?php

namespace App\Http\Controllers;

use App\Http\Requests\StoreAccountRequest;
use App\Http\Requests\UpdateAccountRequest;
use App\Models\Account;
use App\Models\Vehicle;
use App\Jobs\ValidateAccountJob;
use Illuminate\Http\RedirectResponse;
use Illuminate\Http\Request;

class AccountController extends Controller
{
    public function store(StoreAccountRequest $request): RedirectResponse
    {
        $data = $request->validated();

        $request->user()->accounts()->create([
            'label'  => $data['label'],
            'cookie' => $data['cookie'],
            'nonce'  => $data['nonce'],
            'cookie_captured_at' => now(),
            'status' => 'unverified',
        ]);

        return back();
    }

    public function update(UpdateAccountRequest $request, Account $account): RedirectResponse
    {
        $this->authorizeAccount($request, $account);

        $data = $request->validated();

        // A new cookie invalidates the previous status. If the operator
        // pasted a fresh cookie, they'll re-verify.
        if (array_key_exists('cookie', $data)) {
            $data['cookie_captured_at'] = now();
            if (!array_key_exists('status', $data)) {
                $data['status'] = 'unverified';
            }
        }

        $account->update($data);

        return back();
    }

    public function destroy(Request $request, Account $account): RedirectResponse
    {
        $this->authorizeAccount($request, $account);

        // Refuse to delete an account with live arms. The operator
        // must disarm first.
        if ($account->liveArmCount() > 0) {
            return back()->withErrors([
                'account' => 'Disarm this account\'s vehicles before deleting it.',
            ]);
        }

        $account->delete();

        return back();
    }

    private function authorizeAccount(Request $request, Account $account): void
    {
        abort_unless($account->user_id === $request->user()->id, 403);
    }

    public function validate(Request $request, Account $account): RedirectResponse
    {
        $this->authorizeAccount($request, $account);

        $probeWpId = (int) config('auction.probe_vehicle_wp_id');
        if (!$probeWpId) {
            \Log::info("No probe vehicle configured");
            return back()->withErrors([
                'account' => 'No probe vehicle configured. Set AUCTION_PROBE_VEHICLE_WP_ID in .env.',
            ]);
        }

        $vehicle = Vehicle::where('wp_product_id', $probeWpId)
            // ->where('finish_time', '>', now())
            ->first();

        if (!$vehicle) {
            \Log::info("Probe vehicle " . $probeWpId . "is not in a live auction.");
            return back()->withErrors([
                'account' => 'Probe vehicle is not in a live auction.',
            ]);
        }

        ValidateAccountJob::dispatch($account->id, $vehicle->wp_product_id);

        return back()->with('success', 'Validation queued — refresh in a moment.');
    }
}