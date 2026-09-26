<?php

namespace App\Http\Controllers;

use App\Http\Requests\StoreArmedBidRequest;
use App\Http\Requests\UpdateArmedBidRequest;
use App\Models\Account;
use App\Models\ArmedBid;
use App\Models\Vehicle;
use Illuminate\Http\RedirectResponse;
use Illuminate\Http\Request;
use Inertia\Inertia;
use Inertia\Response;

class ArmingController extends Controller
{
    public function index(Request $request): Response
    {
        $user = $request->user();

        $accounts = $user->accounts()
            ->withCount(['armedBids as live_arms_count' => function ($q) {
                $q->whereIn('status', ['armed', 'firing']);
            }])
            ->orderBy('label')
            ->get()
            ->map(fn (Account $a) => [
                'id'                 => $a->id,
                'label'              => $a->label,
                'status'             => $a->status,
                'has_cookie'         => !empty($a->cookie),
                'has_nonce'          => !empty($a->nonce),
                'cookie_captured_at' => $a->cookie_captured_at?->toIso8601String(),
                'deposit_paid'       => (bool) $a->deposit_paid,
                'live_arms_count'    => $a->live_arms_count,
                'arms_limit'         => 2,
            ]);

        $armedBids = ArmedBid::where('user_id', $user->id)
            ->with(['account:id,label,status', 'vehicle:id,wp_product_id,name,current_price,finish_time'])
            ->orderByDesc('updated_at')
            ->get()
            ->map(fn (ArmedBid $ab) => [
                'id'          => $ab->id,
                'max_amount'  => $ab->max_amount,
                'status'      => $ab->status,
                'live_status' => $ab->liveStatus(),
                'last_bid'    => $ab->last_bid,
                'bid_count'   => $ab->our_bid_count,
                'account'     => [
                    'id'    => $ab->account->id,
                    'label' => $ab->account->label,
                    'status'=> $ab->account->status,
                ],
                'vehicle' => [
                    'id'            => $ab->vehicle->id,
                    'wp_id'         => $ab->vehicle->wp_product_id,
                    'name'          => $ab->vehicle->name,
                    'current_price' => $ab->vehicle->current_price,
                    'finish_time'   => $ab->vehicle->finish_time?->toIso8601String(),
                ],
            ]);

        // Vehicles that are live and not yet armed on any account.
        $unarmedVehicles = Vehicle::query()
            ->whereNotIn('id', function ($q) {
                $q->select('vehicle_id')
                  ->from('armed_bids')
                  ->whereIn('status', ['armed', 'firing']);
            })
            ->orderBy('finish_time')
            ->get(['id', 'wp_product_id', 'name', 'current_price', 'finish_time'])
            ->map(fn (Vehicle $v) => [
                'id'            => $v->id,
                'wp_id'         => $v->wp_product_id,
                'name'          => $v->name,
                'current_price' => $v->current_price,
                'finish_time'   => $v->finish_time?->toIso8601String(),
            ]);

        return Inertia::render('Arming', [
            'accounts'        => $accounts,
            'armedBids'       => $armedBids,
            'unarmedVehicles' => $unarmedVehicles,
            'increment'       => (int) config('auction.increment'),
        ]);
    }

    public function store(StoreArmedBidRequest $request): RedirectResponse
    {
        $user = $request->user();
        $data = $request->validated();

        $account = $user->accounts()->findOrFail($data['account_id']);
        $vehicle = Vehicle::findOrFail($data['vehicle_id']);

        if (! $account->isValid()) {
            return back()->withErrors([
                'account_id' => 'This account is not verified. Mark it valid first.',
            ]);
        }

        if (! $account->canArmAnother()) {
            return back()->withErrors([
                'account_id' => 'This account already has 2 active arms.',
            ]);
        }

        $alreadyArmed = ArmedBid::where('vehicle_id', $vehicle->id)
            ->whereIn('status', ['armed', 'firing'])
            ->exists();

        if ($alreadyArmed) {
            return back()->withErrors([
                'vehicle_id' => 'This vehicle is already armed on another account.',
            ]);
        }

        $vehicleLive = $vehicle->finish_time && $vehicle->finish_time->isFuture();
        if (! $vehicleLive) {
            return back()->withErrors([
                'vehicle_id' => 'This vehicle\'s auction is not open.',
            ]);
        }

        ArmedBid::create([
            'user_id'    => $user->id,
            'account_id' => $account->id,
            'vehicle_id' => $vehicle->id,
            'max_amount' => $data['max_amount'],
            'status'     => 'armed',
        ]);

        return back();
    }

    public function update(UpdateArmedBidRequest $request, ArmedBid $armedBid): RedirectResponse
    {
        $this->authorizeArm($request, $armedBid);

        if (! $armedBid->isLive()) {
            return back()->withErrors([
                'max_amount' => 'This arm has already ended.',
            ]);
        }

        $armedBid->update([
            'max_amount' => $request->validated()['max_amount'],
        ]);

        return back();
    }

    public function destroy(Request $request, ArmedBid $armedBid): RedirectResponse
    {
        $this->authorizeArm($request, $armedBid);

        if ($armedBid->status === 'firing') {
            return back()->withErrors([
                'armed_bid' => 'Cannot disarm while bidding is active.',
            ]);
        }

        $armedBid->delete();

        return back();
    }

    private function authorizeArm(Request $request, ArmedBid $armedBid): void
    {
        abort_unless($armedBid->user_id === $request->user()->id, 403);
    }
}