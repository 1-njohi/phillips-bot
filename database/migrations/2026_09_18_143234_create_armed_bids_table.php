<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void
    {
        Schema::create('armed_bids', function (Blueprint $table) {
            $table->id();
            $table->foreignId('vehicle_id')->unique()->constrained()->cascadeOnDelete();
            $table->unsignedInteger('max_amount');
            $table->unsignedInteger('increment')->default(5000);
            $table->string('state')->default('armed');
            $table->unsignedInteger('last_simulated_bid')->nullable();
            $table->unsignedInteger('simulated_bid_count')->default(0);
            $table->timestamps();
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('armed_bids');
    }
};