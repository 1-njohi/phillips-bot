<?php
use App\Http\Controllers\AnalyticsController;
use App\Http\Controllers\DashboardController;
use App\Http\Controllers\VehicleController;
use App\Http\Controllers\AccountController;
use App\Http\Controllers\ArmingController;
use App\Http\Controllers\MockAuctionPageController;

Route::get('/product/{slug}/', [MockAuctionPageController::class, 'show'])
    ->name('mock.product');

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

    Route::get('/arming', [ArmingController::class, 'index'])->name('arming.index');
    Route::post('/arming', [ArmingController::class, 'store'])->name('arming.store');
    Route::patch('/arming/{armedBid}', [ArmingController::class, 'update'])->name('arming.update');
    Route::delete('/arming/{armedBid}', [ArmingController::class, 'destroy'])->name('arming.destroy');

    Route::post('/accounts', [AccountController::class, 'store'])->name('accounts.store');
    Route::patch('/accounts/{account}', [AccountController::class, 'update'])->name('accounts.update');
    Route::delete('/accounts/{account}', [AccountController::class, 'destroy'])->name('accounts.destroy');
    Route::post('/accounts/{account}/validate', [AccountController::class, 'validate'])
    ->name('accounts.validate');
});

require __DIR__ . '/settings.php';
