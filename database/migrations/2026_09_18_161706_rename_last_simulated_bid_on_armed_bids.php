<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void
    {
        Schema::table('armed_bids', function (Blueprint $table) {
            $table->renameColumn('last_simulated_bid', 'last_bid');
            $table->renameColumn('simulated_bid_count', 'bid_count');
        });
    }

    public function down(): void
    {
        Schema::table('armed_bids', function (Blueprint $table) {
            $table->renameColumn('last_bid', 'last_simulated_bid');
            $table->renameColumn('bid_count', 'simulated_bid_count');
        });
    }
};