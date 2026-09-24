<?php
use App\Http\Controllers\AnalyticsController;
use App\Http\Controllers\DashboardController;
use App\Http\Controllers\VehicleController;

Route::middleware(['auth', 'verified'])->group(function () {
    Route::get('/', [DashboardController::class, 'index'])->name('dashboard');
    Route::get('/analytics', [AnalyticsController::class, 'index'])->name('analytics');

    Route::get('/vehicles/{wpId}', [VehicleController::class, 'detail'])
        ->where('wpId', '\d+')
        ->name('vehicle.detail');

    Route::get('/api/state', [DashboardController::class, 'state']);
    Route::post('/api/roster/refresh', [DashboardController::class, 'refreshRoster']);

    Route::patch('/api/vehicles/{vehicle}/watch', [DashboardController::class, 'toggleWatch']);
    Route::patch('/api/vehicles/{vehicle}/watch-price', [DashboardController::class, 'setWatchPrice']);

    Route::patch('/api/wp-vehicles/{wpId}/watch', [DashboardController::class, 'toggleWatchByWpId'])
        ->where('wpId', '\d+');
    Route::patch('/api/wp-vehicles/{wpId}/watch-price', [DashboardController::class, 'setWatchPriceByWpId'])
        ->where('wpId', '\d+');

    Route::get('/api/vehicles/{wpId}/chart', [VehicleController::class, 'chart'])
        ->where('wpId', '\d+');

    Route::get('/api/analytics/overlay', [AnalyticsController::class, 'overlay']);
    Route::get('/api/analytics/leaderboards', [AnalyticsController::class, 'leaderboards']);
});

require __DIR__ . '/settings.php';
