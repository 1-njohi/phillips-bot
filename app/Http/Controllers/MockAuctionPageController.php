<?php

namespace App\Http\Controllers;

use App\Models\Vehicle;
use Illuminate\Http\Response;
use Illuminate\Support\Facades\View;

class MockAuctionPageController extends Controller
{
    public function show(string $slug): Response
    {
        $vehicle = Vehicle::where('slug', $slug)->firstOrFail();

        // If the vehicle has no finish_time in the DB, fail loudly rather
        // than rendering a page with a bogus value. The Python parser would
        // silently accept a wrong timestamp; better to break visibly.
        if (!$vehicle->finish_time) {
            abort(500, "Vehicle {$slug} has no finish_time set.");
        }

        $html = View::make('mock.product', [
            'vehicle' => $vehicle,
            'finishTime' => $vehicle->finish_time->timestamp,
            'currentPrice' => $vehicle->current_price ?? 0,
            'state' => $vehicle->state ?? 'watching',
            'isOpen' => $vehicle->finish_time->isFuture(),
        ])->render();

        return response($html, 200)
            ->header('Content-Type', 'text/html; charset=UTF-8');
    }
}