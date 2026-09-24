<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void
    {
        Schema::dropIfExists('simulated_bids');

        Schema::create('bids', function (Blueprint $table) {
            $table->id();
            $table->foreignId('vehicle_id')->constrained()->cascadeOnDelete();
            $table->unsignedBigInteger('wp_product_id')->index();
            $table->unsignedInteger('amount');              // what we sent
            $table->unsignedInteger('observed_price');      // Store API price just before
            $table->unsignedInteger('price_after')->nullable(); // best guess at new high
            $table->boolean('success')->default(false);     // from response
            $table->json('raw_response')->nullable();       // full body for debugging
            $table->timestamp('fired_at')->index();
            $table->index(['vehicle_id', 'fired_at']);
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('bids');
    }
};