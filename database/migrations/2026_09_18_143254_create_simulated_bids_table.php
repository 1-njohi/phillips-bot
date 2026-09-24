<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void
    {
        Schema::create('simulated_bids', function (Blueprint $table) {
            $table->id();
            $table->foreignId('vehicle_id')->constrained()->cascadeOnDelete();
            $table->unsignedInteger('amount');          // what we would have bid
            $table->unsignedInteger('observed_price');  // Store API price at that tick
            $table->unsignedInteger('shadow_price');    // observed + our prior sim bids
            $table->string('outcome')->default('pending')->index();
            $table->json('context')->nullable();
            $table->timestamp('fired_at')->index();
            $table->index(['vehicle_id', 'fired_at']);
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('simulated_bids');
    }
};