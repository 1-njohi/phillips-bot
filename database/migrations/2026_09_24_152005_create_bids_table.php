<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    /**
     * Run the migrations.
     */
    public function up(): void
    {
        Schema::create('bids', function (Blueprint $table) {
            $table->id();
            $table->foreignId('vehicle_id')->constrained()->cascadeOnDelete();
            $table->foreignId('armed_bid_id')->nullable()
                ->constrained()->nullOnDelete();
            $table->unsignedBigInteger('amount');
            $table->timestamp('fired_at');
            $table->json('response_json')->nullable();
            $table->boolean('success')->default(false);
            $table->timestamp('created_at')->useCurrent();

            $table->index(['vehicle_id', 'fired_at']);
        });
    }
    /**
     * Reverse the migrations.
     */
    public function down(): void
    {
        Schema::dropIfExists('bids');
    }
};
